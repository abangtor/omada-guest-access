"""Unit tests for the documented Omada External Portal protocol boundary."""

from __future__ import annotations

import pytest
from aiohttp import web

from custom_components.omada_guest_access.omada_client import PortalContext, _authorization_payload
from custom_components.omada_guest_access.portal import _portal_context_from_query


def test_wireless_redirect_context_is_normalized() -> None:
    """An Omada EAP redirect produces the correct protected context."""
    context = _portal_context_from_query(
        {
            "clientMac": "aa-bb-cc-dd-ee-ff",
            "apMac": "11:22:33:44:55:66",
            "ssidName": "Guest Wi-Fi",
            "radioId": "1",
            "site": "Default",
            "redirectUrl": "https://example.com/",
        },
        "Default",
    )
    assert context.client_mac == "AA:BB:CC:DD:EE:FF"
    assert context.ap_mac == "11:22:33:44:55:66"
    assert context.is_wireless


def test_rejects_context_from_the_wrong_site() -> None:
    """A portal listener must not authorize a redirect for another configured site."""
    with pytest.raises(web.HTTPBadRequest):
        _portal_context_from_query(
            {"clientMac": "AA:BB:CC:DD:EE:FF", "gatewayMac": "11:22:33:44:55:66", "vid": "20", "site": "Other"},
            "Default",
        )


def test_external_portal_payload_uses_documented_wireless_fields() -> None:
    """Authorization body contains the EAP fields required by Omada."""
    context = PortalContext(
        client_mac="AA:BB:CC:DD:EE:FF",
        site="Default",
        ap_mac="11:22:33:44:55:66",
        ssid_name="Guest Wi-Fi",
        radio_id="1",
    )
    payload = _authorization_payload(context, 8)
    assert payload["authType"] == 4
    assert payload["clientMac"] == context.client_mac
    assert payload["apMac"] == context.ap_mac
    assert "gatewayMac" not in payload
    assert isinstance(payload["time"], str)
    assert int(payload["time"]) > 2_000_000_000_000_000


def test_forever_authorization_uses_a_long_term_future_timestamp():
    context = PortalContext(client_mac="AA:BB:CC:DD:EE:FF", site="Default", gateway_mac="11:22:33:44:55:66", vlan_id="90")
    payload = _authorization_payload(context, 0)
    # Omada interprets time as an absolute timestamp in microseconds; zero
    # is 1970-01-01, so the Forever preset must be a far-future grant.
    assert int(payload["time"]) > 2_000_000_000_000_000
    assert payload["gatewayMac"] == context.gateway_mac
