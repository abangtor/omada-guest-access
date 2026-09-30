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


async def test_cookie_csrf_session_retry_and_exact_expiry(hass, config, context, aiohttp_server, socket_enabled):
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


@pytest.mark.parametrize(
    "body,status,error",
    [
        ({}, 200, OmadaApiError),
        ([], 200, OmadaApiError),
        ({"errorCode": 0, "result": {}}, 200, OmadaApiError),
        ({"errorCode": -1}, 200, OmadaAuthError),
        ({}, 401, OmadaAuthError),
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


async def test_revoke_does_not_issue_undocumented_api_call(hass, config, context):
    client = OmadaExternalPortalClient(hass, config)
    try:
        with patch.object(client._session, "post") as post:
            with pytest.raises(OmadaApiError, match="not supported"):
                await client.async_revoke(context)
            post.assert_not_called()
    finally:
        await client.async_close()


async def test_timeout_is_sanitized(hass, config):
    client = OmadaExternalPortalClient(hass, config)
    try:
        with patch.object(client._session, "post", side_effect=TimeoutError("secret-data")):
            with pytest.raises(OmadaApiError, match="network error or timeout") as error:
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
