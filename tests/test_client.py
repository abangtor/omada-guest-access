"""Exercise HTTP cookies/CSRF/retry and strict controller response handling."""

from unittest.mock import patch

import pytest
from aiohttp import web

from custom_components.omada_guest_access.omada_client import (
    OmadaApiError,
    OmadaAuthError,
    OmadaExternalPortalClient,
    normalize_controller_url,
)


@pytest.mark.parametrize(
    "url,identifier",
    [
        ("https://controller.example/controller/", ""),
        ("https://controller.example/controller/#login", "controller"),
        ("https://controller.example", "controller"),
    ],
)
def test_normalize_url(url, identifier):
    assert normalize_controller_url(url, identifier) == ("https://controller.example", "controller")


@pytest.mark.parametrize(
    "url,identifier",
    [
        ("https://controller.example", ""),
        ("https://user:password@host", "id"),
        ("ftp://host", "id"),
        ("https://host/one", "two"),
        ("https://host", "../id"),
    ],
)
def test_reject_invalid_url(url, identifier):
    with pytest.raises(ValueError):
        normalize_controller_url(url, identifier)


@pytest.mark.parametrize("expired_response", ["json", "redirect"])
async def test_cookie_csrf_session_retry_and_exact_expiry(
    hass, config, context, aiohttp_server, socket_enabled, expired_response
):
    logins, authorizations = [], []

    async def login(request):
        logins.append(await request.json())
        response = web.json_response({"errorCode": 0, "result": {"token": f"csrf-{len(logins)}"}})
        response.set_cookie("TPOMADA_SESSIONID", f"session-{len(logins)}")
        return response

    async def authorize(request):
        authorizations.append(await request.json())
        assert request.headers["Csrf-Token"] == f"csrf-{len(logins)}"
        assert request.cookies["TPOMADA_SESSIONID"] == f"session-{len(logins)}"
        if expired_response == "redirect" and len(authorizations) == 1:
            return web.Response(status=302, headers={"Location": "/controller/hotspot/login"})
        return web.json_response({"errorCode": -1005 if len(authorizations) == 1 else 0})

    app = web.Application()
    app.router.add_post("/controller/api/v2/hotspot/login", login)
    app.router.add_post("/controller/api/v2/hotspot/extPortal/auth", authorize)
    server = await aiohttp_server(app)
    config["controller_url"] = str(server.make_url("/controller/"))
    client = OmadaExternalPortalClient(hass, config)
    try:
        expires = await client.async_authorize(context, 8)
        assert len(logins) == 2
        assert len(authorizations) == 2
        assert authorizations[-1]["time"] == int(expires.timestamp() * 1_000_000)
    finally:
        await client.async_close()
    assert client._session.closed


async def test_health_check_reuses_an_existing_operator_session(hass, config, aiohttp_server, socket_enabled):
    """Coordinator polls must not repeatedly log an operator into Omada."""
    logins = []

    async def login(request):
        logins.append(await request.json())
        return web.json_response({"errorCode": 0, "result": {"token": "csrf"}})

    app = web.Application()
    app.router.add_post("/controller/api/v2/hotspot/login", login)
    server = await aiohttp_server(app)
    config["controller_url"] = str(server.make_url("/controller/"))
    client = OmadaExternalPortalClient(hass, config)
    try:
        await client.async_test_connection()
        await client.async_test_connection()
        await client.async_test_connection()
        assert len(logins) == 1
    finally:
        await client.async_close()


@pytest.mark.parametrize(
    "body,status,error",
    [
        ({}, 200, OmadaApiError),
        ([], 200, OmadaApiError),
        ({"errorCode": 0, "result": {}}, 200, OmadaApiError),
        ({"errorCode": -1}, 200, OmadaAuthError),
        ({}, 401, OmadaAuthError),
        ({}, 302, OmadaApiError),
        ({}, 500, OmadaApiError),
    ],
)
async def test_login_failure(hass, config, body, status, error, aiohttp_server, socket_enabled):
    async def login(request):
        return web.json_response(body, status=status)

    app = web.Application()
    app.router.add_post("/controller/api/v2/hotspot/login", login)
    server = await aiohttp_server(app)
    config["controller_url"] = str(server.make_url("/"))
    client = OmadaExternalPortalClient(hass, config)
    try:
        with pytest.raises(error):
            await client.async_test_connection()
    finally:
        await client.async_close()


async def test_disabled_revoke_does_not_issue_api_call(hass, config, context):
    config["enable_revoke"] = False
    client = OmadaExternalPortalClient(hass, config)
    try:
        with patch.object(client._session, "post") as post:
            with pytest.raises(OmadaApiError, match="disabled"):
                await client.async_revoke(context)
            post.assert_not_called()
    finally:
        await client.async_close()


async def test_timeout_is_sanitized(hass, config):
    client = OmadaExternalPortalClient(hass, config)
    try:
        with patch.object(client._session, "post", side_effect=TimeoutError("secret-data")):
            with pytest.raises(OmadaApiError, match="timed out after 15 seconds") as error:
                await client.async_test_connection()
            assert "secret-data" not in str(error.value)
    finally:
        await client.async_close()


async def test_certificate_failure_is_distinct_and_sanitized(hass, config):
    import ssl

    from aiohttp import ClientConnectorCertificateError
    from aiohttp.client_reqrep import ConnectionKey

    from custom_components.omada_guest_access.omada_client import OmadaTlsError

    key = ConnectionKey('controller.example', 443, True, True, None, None, None)
    failure = ClientConnectorCertificateError(key, ssl.CertificateError('private certificate detail'))
    client = OmadaExternalPortalClient(hass, config)
    try:
        with patch.object(client._session, 'post', side_effect=failure):
            with pytest.raises(OmadaTlsError, match='verified TLS connection') as error:
                await client.async_test_connection()
            assert 'private certificate detail' not in str(error.value)
    finally:
        await client.async_close()


@pytest.mark.parametrize('code', [-30109, 'private-data', True])
async def test_login_error_only_exposes_numeric_code(hass, config, code, aiohttp_server, socket_enabled):
    async def login(request):
        return web.json_response({'errorCode': code, 'msg': 'private-data', 'result': {'token': 'secret-token'}})

    app = web.Application()
    app.router.add_post('/controller/api/v2/hotspot/login', login)
    server = await aiohttp_server(app)
    config['controller_url'] = str(server.make_url('/'))
    client = OmadaExternalPortalClient(hass, config)
    try:
        with pytest.raises(OmadaApiError) as error:
            await client.async_test_connection()
        detail = str(error.value)
        assert 'private-data' not in detail
        assert 'secret-token' not in detail
        if type(code) is int:
            assert str(code) in detail
    finally:
        await client.async_close()


@pytest.mark.parametrize("operation", ["authorize", "hotspot"])
async def test_login_redirect_retry_is_bounded(hass, config, context, aiohttp_server, socket_enabled, operation):
    calls = {"login": 0, "operation": 0, "redirect": 0}

    async def login(request):
        calls["login"] += 1
        return web.json_response({"errorCode": 0, "result": {"token": "fresh"}})

    async def operation_handler(request):
        calls["operation"] += 1
        return web.Response(status=302, headers={"Location": "/controller/hotspot/login"})

    async def redirect_handler(request):
        calls["redirect"] += 1
        return web.Response(text="must not follow")

    app = web.Application()
    app.router.add_post("/controller/api/v2/hotspot/login", login)
    app.router.add_post("/controller/api/v2/hotspot/extPortal/auth", operation_handler)
    app.router.add_get("/controller/api/v2/hotspot/sites/site/clients", operation_handler)
    app.router.add_get("/controller/hotspot/login", redirect_handler)
    server = await aiohttp_server(app)
    config["controller_url"] = str(server.make_url("/"))
    client = OmadaExternalPortalClient(hass, config)
    try:
        with pytest.raises(OmadaAuthError, match="redirected to login"):
            if operation == "authorize":
                await client.async_authorize(context, 8)
            else:
                await client._async_authenticated_request("/hotspot/sites/site/clients", method="GET")
        assert calls == {"login": 2, "operation": 2, "redirect": 0}
        assert client._csrf_token is None
    finally:
        await client.async_close()


@pytest.mark.parametrize("location,is_auth", [
    ("/controller/hotspot/login", True),
    ("/controller/login#hotspot", True),
    ("/controller/hotspot/login/", True),
    ("https://other.example/controller/hotspot/login?secret=hidden", False),
    ("//other.example/controller/hotspot/login", False),
    ("/other/hotspot/login", False),
    ("/controller/api/v2/new-endpoint", False),
    ("https://[malformed", False),
    ("", False),
])
async def test_redirect_targets_are_restricted(hass, config, aiohttp_server, socket_enabled, location, is_auth):
    async def handler(request):
        target = location
        # Also exercise the absolute same-origin URL emitted by the real controller.
        if target == "/controller/hotspot/login":
            target = str(server.make_url(target))
        return web.Response(status=302, headers={"Location": target})

    app = web.Application()
    app.router.add_post("/controller/api/v2/hotspot/extPortal/auth", handler)
    server = await aiohttp_server(app)
    config["controller_url"] = str(server.make_url("/"))
    client = OmadaExternalPortalClient(hass, config)
    client._csrf_token = "stale"
    try:
        with pytest.raises(OmadaApiError) as error:
            await client._async_raw_request("/hotspot/extPortal/auth", {}, csrf=True)
        assert isinstance(error.value, OmadaAuthError) is is_auth
        assert "hidden" not in str(error.value)
    finally:
        await client.async_close()
