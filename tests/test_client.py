"""Exercise HTTP cookies/CSRF/retry and strict controller response handling."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from aiohttp import web
from homeassistant.util import dt as dt_util

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
        before = dt_util.utcnow()
        expires = await client.async_authorize(context, 8)
        after = dt_util.utcnow()
        assert len(logins) == 2
        assert len(authorizations) == 2
        # Omada 6.x accepts a relative duration in milliseconds.
        assert [request["time"] for request in authorizations] == ["28800000"] * 2
        assert before + timedelta(hours=8) <= expires <= after + timedelta(hours=8)
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
async def test_login_error_excludes_untrusted_response_content(hass, config, code, aiohttp_server, socket_enabled):
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


async def test_remaining_duration_recomputed_after_auth_retry(
    hass, config, context, aiohttp_server, socket_enabled, freezer
):
    deadline = dt_util.utcnow() + timedelta(minutes=17, seconds=23, milliseconds=456)
    authorizations = []

    async def login(request):
        freezer.tick(timedelta(seconds=2))
        return web.json_response({"errorCode": 0, "result": {"token": "fresh"}})

    async def authorize(request):
        authorizations.append(await request.json())
        assert int(authorizations[-1]["time"]) == int((deadline - dt_util.utcnow()).total_seconds() * 1000)
        return web.json_response({"errorCode": -1005 if len(authorizations) == 1 else 0})

    app = web.Application()
    app.router.add_post("/controller/api/v2/hotspot/login", login)
    app.router.add_post("/controller/api/v2/hotspot/extPortal/auth", authorize)
    server = await aiohttp_server(app)
    config["controller_url"] = str(server.make_url("/"))
    client = OmadaExternalPortalClient(hass, config)
    try:
        assert await client.async_authorize(context, 0, expires_at=deadline) == deadline
        assert int(authorizations[0]["time"]) - int(authorizations[1]["time"]) == 2000
        freezer.tick(timedelta(hours=1))
        with pytest.raises(OmadaApiError, match="expired"):
            await client.async_authorize(context, 0, expires_at=deadline)
        assert len(authorizations) == 2
    finally:
        await client.async_close()


async def test_controller_name_sync_patch_retry(hass, config, context, aiohttp_server, socket_enabled):
    from custom_components.omada_guest_access.omada_client import OmadaClientNameClient

    logins, updates = [], []

    async def login(request):
        logins.append(await request.json())
        response = web.json_response({"errorCode": 0, "result": {"token": str(len(logins))}})
        response.set_cookie("session", str(len(logins)))
        return response

    async def site(request):
        assert request.headers["Csrf-Token"] == str(len(logins))
        return web.json_response({"errorCode": 0, "result": {
            "privilege": {"sites": [{"name": "Default", "key": "site-id"}]}
        }})

    async def update(request):
        updates.append(await request.json())
        assert request.cookies["session"] == str(len(logins))
        return web.json_response({"errorCode": -1005 if len(updates) == 1 else 0})

    app = web.Application()
    app.router.add_post("/controller/api/v2/login", login)
    app.router.add_get("/controller/api/v2/users/current", site)
    app.router.add_patch("/controller/api/v2/sites/site-id/clients/AA-BB-CC-DD-EE-FF", update)
    server = await aiohttp_server(app)
    client = OmadaClientNameClient(hass, config | {"controller_url": str(server.make_url("/"))})
    try:
        await client.async_set_client_name(context, 'Name "with quotes"')
        assert len(logins) == 2
        assert logins[0] == {"username": config["username"], "password": config["password"]}
        assert updates == [{"name": "Name with quotes"}] * 2
    finally:
        await client.async_close()


@pytest.mark.parametrize(("sites", "lookup_code", "rename_code", "error"), [
    ([{"name": "Default", "key": "site-id"}], 0, 0, None),
    ([{"name": "Other", "key": "Default"}], 0, 0, None),
    ([], 0, 0, "missing or ambiguous"),
    ([{"name": "Default", "key": "a"}, {"name": "Default", "key": "b"}], 0, 0, "missing or ambiguous"),
    (None, 0, 0, "accessible sites"),
    ([None, {}, {"name": "Default", "key": 7}], 0, 0, "missing or ambiguous"),
    ([], -41011, 0, "site lookup failed:.*-41011"),
    ([{"name": "Default", "key": "site-id"}], 0, -41011, "client rename failed:.*This client does not exist.*-41011"),
])
async def test_name_sync_site_resolution_and_failure_stage(
    hass, config, context, aiohttp_server, socket_enabled, sites, lookup_code, rename_code, error
):
    from custom_components.omada_guest_access.omada_client import OmadaClientNameClient

    updates = []

    async def login(request):
        return web.json_response({"errorCode": 0, "result": {"token": "token"}})

    async def current_user(request):
        return web.json_response({"errorCode": lookup_code, "result": {"privilege": {"sites": sites}},
                                  "msg": "private controller response"})

    async def update(request):
        updates.append((request.match_info["site"], await request.json()))
        return web.json_response({"errorCode": rename_code, "msg": "private controller response"})

    app = web.Application()
    app.router.add_post("/controller/api/v2/login", login)
    app.router.add_get("/controller/api/v2/users/current", current_user)
    app.router.add_patch("/controller/api/v2/sites/{site}/clients/AA-BB-CC-DD-EE-FF", update)
    server = await aiohttp_server(app)
    client = OmadaClientNameClient(hass, config | {"controller_url": str(server.make_url("/"))})
    try:
        if error:
            with pytest.raises(OmadaApiError, match=error) as raised:
                await client.async_set_client_name(context, "Alex")
            assert "private controller response" not in str(raised.value)
            assert len(updates) == (1 if rename_code else 0)
        else:
            await client.async_set_client_name(context, "Alex")
            assert updates == [(sites[0]["key"], {"name": "Alex"})]
    finally:
        await client.async_close()


@pytest.mark.parametrize(("code", "description"), [
    (-41011, "This client does not exist."),
    (-41005, "The authorization information does not exist."),
    (-33000, "This site does not exist."),
    (-30109, "Invalid username or password."),
    (-1505, "The current user does not have permissions to access this site."),
    (-1005, "Operation forbidden."),
    (-1200, "You have been logged out of the controller."),
    (-1001, "Invalid request parameters."),
    (-41009, "Failed to authorize this client."),
    (-41017, "Unauth failed because gateway not existed or disconnected."),
    (-41518, "This SSID does not exist."),
    (-987654, "Unknown controller error."),
    (41011, "Unknown controller error."),
])
@pytest.mark.parametrize("csrf", [False, True])
async def test_api_errors_use_safe_verified_descriptions(
    hass, config, aiohttp_server, socket_enabled, code, description, csrf
):
    async def rejected(request):
        return web.json_response({"errorCode": code, "msg": "private-response-text",
                                  "result": {"token": "secret-token"}})

    app = web.Application()
    app.router.add_post("/controller/api/v2/test", rejected)
    server = await aiohttp_server(app)
    client = OmadaExternalPortalClient(hass, config | {"controller_url": str(server.make_url("/"))})
    try:
        expected_type = OmadaAuthError if not csrf or code == -1005 else OmadaApiError
        with pytest.raises(expected_type) as raised:
            await client._async_raw_request("/test", {}, csrf=csrf)
        detail = str(raised.value)
        assert description in detail
        assert f"(error code {code})" in detail
        assert "private-response-text" not in detail
        assert "secret-token" not in detail
    finally:
        await client.async_close()
