# Omada Guest Access

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

**Omada Guest Access** is a Home Assistant custom integration and captive portal for approval-based guest Wi-Fi. A guest enters a name on the Omada redirect page; Home Assistant receives an actionable request; approving it authorizes that exact captive-portal session through Omada.

> **Controller compatibility:** the production authorization path implements TP-Link's documented **External Portal Server API** for Omada Controller **v5.0.15–v6.2.x**. It uses a **Hotspot Operator** account, not a normal Controller administrator account.

## Features

- Dedicated reverse-proxy-friendly guest portal listener (default `8088`).
- Omada redirect-context validation for EAP and gateway portals; the browser never submits its own MAC address.
- Opaque, IP-bound, short-lived portal sessions; status can only be read by the original guest session.
- Pending queue and active-session sensors with structured dashboard attributes.
- `approve`, `deny`, and `revoke` services plus lifecycle events for HA automations/notifications.
- Approval only changes state **after** Omada accepts the controller authorization.
- Persistent request history, expiry, retention cleanup, rate limiting, and portal request-size limits.

## Install with HACS

1. In HACS, open **Integrations** → overflow menu → **Custom repositories**.
2. Add `https://github.com/abangtor/omada-guest-access` as an **Integration** repository.
3. Install **Omada Guest Access**, restart Home Assistant, then add the integration from **Settings → Devices & services**.

## Omada configuration

1. In Omada, create a **Hotspot Operator** for this integration. Do not use an administrator account.
2. Configure the guest SSID's portal authentication type as **External Portal Server**.
3. Set its landing URL to your public portal address, for example `https://guest.example.com/`.
4. Reverse proxy `guest.example.com` to Home Assistant's portal listener (default `8088`) and use TLS.
5. Allow the guest VLAN to resolve and reach the portal hostname before authentication. Keep Home Assistant's normal UI and the Omada controller off the guest VLAN.

For controller-specific settings, use TP-Link's versioned External Portal Server documentation. The integration needs the Controller URL, a Controller ID (normally already present in its URL), the site name, and the Hotspot Operator credentials.

## Home Assistant entities and services

| Item | Purpose |
| --- | --- |
| Pending requests sensor | Count plus `requests` attribute containing request IDs and guest details. |
| Active sessions sensor | Count plus `sessions` attribute for currently authorized guests. |
| Portal online binary sensor | Controller/API reachability. |
| `omada_guest_access.approve_request` | Authorize a request; optional `duration_hours`. |
| `omada_guest_access.deny_request` | Deny a request; optional guest-visible `reason`. |
| `omada_guest_access.revoke_access` | Invalidate an active authorization. |

Example approval automation action:

```yaml
service: omada_guest_access.approve_request
data:
  request_id: "{{ request_id }}"
  duration_hours: 8
```

Events: `omada_guest_access_request_created`, `omada_guest_access_request_approved`, `omada_guest_access_request_denied`, `omada_guest_access_request_expired`, `omada_guest_access_access_revoked`, and `omada_guest_access_omada_api_error`.

## Security and privacy

- The external portal redirect context is supplied by Omada and remains server-side after the landing request.
- Guest status requests require the opaque, expiring portal session token and are bound to the origin IP.
- Portal requests are rate-limited and capped at 8 KiB.
- Request history is retained for the configured period (30 days by default); change it under integration options.
- Do not expose the controller to the Internet. TLS termination should happen at the reverse proxy; the portal listener itself is HTTP.
- Omada's redirect protocol has no cryptographic callback signature. Restrict portal ingress to the guest VLAN/reverse proxy and do not expose it as a general public form.

## Development

```bash
pip install -r requirements_test.txt
ruff check .
pytest
```

The repository's GitHub Actions workflow runs HACS validation, hassfest, Ruff, and tests.
