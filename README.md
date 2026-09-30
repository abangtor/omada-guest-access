# Omada Guest Access

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

A Home Assistant custom integration and separate captive portal for approval-based Omada guest Wi-Fi. Guests enter a name; an HA administrator reviews the request and approves a time-limited controller authorization or denies it.

**Status:** automated HA/HTTP tests cover the request flow. Live controller and phone captive-portal interoperability still need deployment testing. The adapter follows TP-Link's [External Portal Server protocol for Controller 5.0.15–6.2.0](https://support.omadanetworks.com/en/document/13080); this is not a claim that every controller/firmware combination has been tested.

## Implemented

- Dedicated portal listener, default port `8088`, independent of HA's main HTTP interface.
- EAP and gateway redirect parsing, name/note submission, guest-specific status polling.
- Hotspot Operator login with isolated cookies, CSRF tokens, bounded HTTP timeouts, and one expired-session retry.
- Admin-only approve/deny services; approval is recorded only after Omada accepts it.
- Optional Lovelace card with approve/deny buttons, pending requests, unexpired grants, and admin-only searchable/paginated history.
- Configurable portal title, welcome message, accent color, and plain-text terms with optional mandatory acceptance.
- Server-validated consent with acceptance timestamp, SHA-256 terms version, and the exact accepted text persisted per request.
- Serialized decisions, persistent requests, decision-user attribution, lifecycle events, expiry and retention.
- Local cleanup independent of controller availability; active grants survive retention cleanup.
- Trusted reverse proxies, optional guest-network ingress allowlist, IP-bound tokens, bounded sessions and rate limits.
- Config/options, reauthentication/reconfiguration, legacy config migration and awaited shutdown/reload cleanup.

### Important boundaries

- **Revocation is not supported by the documented External Portal API.** Earlier code incorrectly assumed that zero-duration authorization revoked access. That behavior has been removed. The retained `revoke_access` service raises an explanatory error and does not alter the grant. End access early in Omada Hotspot Manager, or let the grant expire.
- The active sensor lists **locally recorded, unexpired grants**, not a controller-confirmed live client list. Manual controller changes are not reconciled. A network timeout during authorization can leave the outcome uncertain; inspect Omada before retrying.
- Omada redirect query parameters are **unsigned and forgeable**. Opaque tokens protect subsequent status access, but do not authenticate the original MAC/AP context. Guest names are self-reported. Restrict ingress to the guest network and manually review requests; do not automatically approve names or MACs as authenticated identities.
- Voucher creation, bandwidth profiles, revoke-all, and portal translations are not implemented. History shows retained request records, not an immutable event-by-event audit trail.

## Install with HACS

1. HACS → Integrations → Custom repositories: add `https://github.com/abangtor/omada-guest-access` as an **Integration**.
2. Install, restart Home Assistant, then add **Omada Guest Access** in Settings → Devices & services.
3. Use a dedicated **Hotspot Operator**, not a controller administrator account.

Home Assistant 2025.1+ is the declared minimum. The regression suite currently runs against HA 2026.2.3 on Python 3.13; older supported versions still need their own compatibility runs.

## Configure Omada and networking

1. Create a Hotspot Operator account in Omada Hotspot Manager.
2. Configure the guest SSID/network portal authentication as **External Portal Server**.
3. Set the external portal URL to `https://guest.example.com/`.
4. Reverse proxy that hostname to the HA host on `8088`. Preserve the redirect's query string. Serve at the hostname root, not a subpath.
5. Allow unauthenticated guests to resolve/reach that hostname. Keep HA port `8123`, controller management ports, and other LAN services inaccessible from the guest VLAN.
6. Configure **Public portal URL**, **Trusted proxy IPs/CIDRs**, and preferably **Allowed guest IPs/CIDRs** in integration options. Each integration entry needs a distinct listen port.

The controller URL can be an origin plus a separate Controller ID, or a controller UI URL containing the ID. For example, `https://controller.local:8043/abc123/` normalizes to origin `https://controller.local:8043` and ID `abc123`. Controller reverse-proxy subpaths are not supported. Use the actual site name expected by the redirect.

### Proxy example

See [examples/nginx.conf](examples/nginx.conf). It overwrites `X-Forwarded-For` using the client socket address. Only add the proxy's own IP/CIDR under **Trusted proxies**; never trust arbitrary guest IPs as proxies. Untrusted forwarding headers are rejected. Trusted chains are examined right-to-left, stopping at the first untrusted hop.

If **Allowed guest networks** is empty, the portal accepts requests from any reachable IP. Set it to your guest VLAN CIDR (for example, `192.168.50.0/24`) when the proxy preserves those source addresses. If guest traffic is NATed, an IP allowlist cannot distinguish individual devices behind that NAT.

### Home Assistant Container

With host networking, expose the dedicated portal port only through your firewall/reverse proxy. With bridge networking, explicitly publish the additional port; installing through HACS does not change container networking:

```yaml
ports:
  - "8123:8123"
  - "192.168.10.10:8088:8088"  # replace with the Docker host's actual LAN IP
```

The listener itself serves HTTP. Terminate HTTPS at the proxy; do not proxy guest traffic to HA's authenticated UI. HA's `http.trusted_proxies` setting is separate from this listener's integration options.

## Portal branding and terms

Open the integration's **Configure / Options** dialog:

| Setting | Default / limit |
| --- | --- |
| Portal title | `Guest Wi-Fi`; nonblank, up to 80 characters |
| Welcome message | `Request internet access from your host.`; up to 500 characters |
| Accent color | `#1769aa`; six-digit `#RRGGBB` |
| Guest Wi-Fi terms | Empty; up to 4,000 characters, multiline plain text |
| Require acceptance | Off; requires nonblank terms when enabled |

Text is escaped, never rendered as HTML. Everything is served locally: no external fonts, images, or scripts are needed before authorization. Choose an accent with sufficient contrast against white button text.

When acceptance is required, both the browser form and server enforce it before creating a request. The server records its own timestamp, the SHA-256 hash of the displayed terms (UTF-8, outer whitespace trimmed), and a snapshot of that text. Browser-supplied timestamps, hashes, and terms text are ignored. Informational terms without mandatory acceptance do **not** create a consent record.

Saving options reloads the listener: guests with an open page should reconnect. Changes apply to new requests; existing requests retain their original consent (or lack of consent). They are not silently re-consented or automatically revoked. This is a self-reported acceptance record, not verification of a guest's legal identity.

## Request-management dashboard card

The integration serves the bundled card through **HA's normal HTTP interface**, not the guest listener.

1. Settings → Dashboards → Resources (Advanced Mode): add `/omada_guest_access/omada-guest-access-card.js?v=1.2.0` as a **JavaScript module**.
2. Add a Manual card using your actual sensor entity IDs:

```yaml
type: custom:omada-guest-access-card
pending_entity: sensor.pending_requests
active_entity: sensor.active_sessions
duration_hours: 8
title: Guest Wi-Fi
```

Entity IDs may have suffixes if multiple sites/integrations are installed. Find them under the integration's entities. Only administrators see enabled approval controls. Errors are displayed in the card; grants show their scheduled expiry and the revocation limitation.

### Request history

Administrators can select **Show history**, filter by status, or search by name, MAC address, or request ID. Pages contain 20 requests, newest first. **Request details** shows timestamps, denial reason, the HA decision user ID, and any recorded terms acceptance and text. Use **Search / refresh** for fresh data; history is a snapshot and is not streamed. New requests or retention cleanup between page loads can shift page boundaries.

The card obtains the integration entry ID from the pending sensor. History uses HA's authenticated WebSocket command `omada_guest_access/history`, with server-side admin checks; it is never exposed on the guest port or added to sensor attributes. Requests for missing, unloaded, or unrelated entries are rejected. Automation decisions and older records can lack a user ID. This is a browsable current/final-state record, not an append-only controller audit log.

Example command for an authenticated HA administrator:

```json
{"id": 1, "type": "omada_guest_access/history", "entry_id": "YOUR_ENTRY_ID", "status": "denied", "query": "Alex", "offset": 0, "limit": 20}
```

Omit `status` for all statuses. `query` is case-insensitive (maximum 120 characters), `offset` is zero-based, and `limit` is 1–50. The result contains `requests`, `total`, `offset`, and `limit`. Controller credentials and internal redirect/session context are never returned.

## Entities and services

| Item | Meaning |
| --- | --- |
| Pending requests sensor | Count and `requests` attribute for the pending queue. |
| Active sessions sensor | Count and `sessions` attribute for local unexpired grants; `controller_confirmed: false`. |
| Portal online | Dedicated local listener is running. |
| Controller online | Most recent controller authentication/health check succeeded. |
| `omada_guest_access.approve_request` | `request_id`, optional `duration_hours` (1–720). |
| `omada_guest_access.deny_request` | `request_id`, optional guest-visible `reason` (up to 500 characters). |
| `omada_guest_access.revoke_access` | Compatibility service: reports unsupported, never claims success. |

Services accept administrator users and trusted HA automation/system contexts. User-initiated decisions persist `decision_user_id`; system decisions have no user ID. Events include `entry_id` for multi-site automation routing.

Events: `omada_guest_access_request_created`, `omada_guest_access_request_approved`, `omada_guest_access_request_denied`, `omada_guest_access_request_expired`, `omada_guest_access_access_revoked` (reserved for a future supported adapter), and `omada_guest_access_omada_api_error`.

See [examples/actionable-notifications.yaml](examples/actionable-notifications.yaml) for a mobile approval automation. Restrict its notifications to a trusted administrator's phone.

## Persistence and privacy

Requests, decisions, and any consent snapshots use HA `.storage`; normal history retention defaults to 30 days after the last state change. Unexpired grants are never removed by retention. Pending deadlines continue to be enforced during controller outages, and stale approvals are rejected immediately. Portal browser tokens are deliberately memory-only: reconnect after an HA restart to obtain a new session. A long configured pending timeout extends the token lifetime accordingly.

HA config-entry credentials are stored in HA's configuration storage, **not guaranteed to be encrypted**. Protect configuration files and backups. Sensor attributes contain guest names, notes and MAC addresses. HA Recorder/backups have separate retention policies; exclude these sensors from Recorder if needed:

```yaml
recorder:
  exclude:
    entities:
      - sensor.pending_requests
      - sensor.active_sessions
```

Guest responses exclude other guests, internal controller context and credentials. Guest pages/status/errors use `Cache-Control: no-store`; the portal disables its own access log. Configure reverse-proxy logging accordingly: the initial query contains MAC addresses and connection details.

## Upgrade notes for 1.2.2

- Setup now distinguishes controller TLS failures from network/API failures and rejected credentials. Errors include sanitized timeout, HTTP-status, API-code or unexpected-response details, never credentials or raw controller response text.
- For a trusted local controller with a self-signed certificate, uncheck **Verify controller TLS certificate** in the integration form. This keeps HTTPS encryption but disables certificate verification for this integration only. Prefer a trusted certificate and matching hostname when available.
- The workaround also works in earlier versions; no update is required to change this setting. Update/redownload from HACS and restart HA to receive the clearer errors.

## Upgrade notes for 1.2.1

- Fixes the setup/reconfigure/reauthentication form returning “Config flow could not be loaded: 500 Internal Server Error”.
- Update or redownload the integration through HACS, then restart Home Assistant before retrying Add Integration.
- Existing configuration and history are preserved. No dashboard card update is needed for this patch.

## Upgrade notes for 1.2.0

- Update through HACS and restart HA. Update the card resource URL to `?v=1.2.0` and refresh the dashboard.
- Existing configuration and request history remain compatible; no new credentials are required.
- Branding retains the previous defaults. Terms acceptance is off until configured explicitly.
- Legacy records show “Terms acceptance was not recorded”; no consent is fabricated for them.
- Consent snapshots share the request's retention policy; backups have their own retention. Only administrators can use the new history API.

## Upgrade notes for 1.1.0

- Existing v2 configuration and stored history are retained. v1 config entries migrate; missing controller IDs/credentials can be repaired through reauthentication or Reconfigure.
- Legacy requests without an Omada connection context are marked expired, never authorized using guessed data.
- Reconfigure is available through the integration menu; options do not require re-entering credentials.
- **Revoke is now explicitly unsupported.** Review existing automations that expected it to disconnect clients.
- Configure trusted proxies if the proxy sends `X-Forwarded-For`; without this, requests with that header are rejected.

## Verification and remaining deployment checks

Run the automated suite:

```bash
python3.13 -m venv .venv
.venv/bin/pip install --only-binary=bluetooth-data-tools,bluetooth-auto-recovery,dbus-fast -r requirements_test.txt
.venv/bin/ruff check .
.venv/bin/pytest
```

The test requirements pin the HA test plugin for reproducible results. Tests use real HA fixtures, local mock HTTP controllers and real local portal sockets; no live controller credentials are needed. The optional [browser smoke test](tests/browser_smoke.py) exercises the guest form and card with Chromium; see its module instructions.

Before production, check your specific controller and guest devices:

1. Redirect from an actual guest SSID, including the correct site/AP/client fields.
2. Guest submits a name; HA displays the request and mobile notification.
3. HA approval causes actual internet access; denial keeps access blocked.
4. Controller-enforced access expiry, including across HA restart/outage.
5. iOS/Android captive-portal browser behavior and proxy/VLAN reachability.
6. End access manually in Omada and confirm traffic stops. The local grant remains until its recorded deadline, because this adapter cannot reconcile controller state.

GitHub Actions runs HACS validation, hassfest, Ruff, the Python test suite, and the Chromium browser smoke test (mocked HA/controller traffic).
