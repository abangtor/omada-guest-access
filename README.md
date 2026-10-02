# Omada Guest Access

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

A Home Assistant custom integration and separate captive portal for approval-based Omada guest Wi-Fi. Guests enter a name; an HA administrator or selected HA user reviews the request and approves a time-limited controller authorization or denies it.

**Status:** automated HA/HTTP tests cover the request flow. Live controller and phone captive-portal interoperability still need deployment testing. The adapter follows TP-Link's [External Portal Server protocol for Controller 5.0.15–6.2.0](https://support.omadanetworks.com/en/document/13080); this is not a claim that every controller/firmware combination has been tested.

## Implemented

- Dedicated portal listener, default port `8088`, independent of HA's main HTTP interface.
- EAP and gateway redirect parsing, name/note submission, guest-specific status polling.
- Returning-guest name/note prefilling in the same browser for 30 days, with opt-out and forget controls.
- Automatic registration and version updates for the Lovelace card in storage-managed resources.
- Hotspot Operator login with isolated cookies, CSRF tokens, bounded HTTP timeouts, and one expired-session retry.
- Admin-only approve/deny/cancel services. Approval requires Omada success; cancellation uses Hotspot Manager deauthentication and confirms the grant is no longer active.
- Lovelace card with a native visual editor, section/detail visibility controls, duration presets, cancellation, pending requests, unexpired grants, and stable admin-only history disclosures.
- Configurable portal title, welcome message, accent color, custom CSS, and a sandboxed full-page Jinja layout. Plain-text terms with optional mandatory acceptance.
- Server-validated consent with acceptance timestamp, SHA-256 terms version, and the exact accepted text persisted per request.
- Serialized decisions, persistent requests, decision-user attribution, lifecycle events, expiry and retention.
- Local cleanup independent of controller availability; active grants survive retention cleanup.
- Trusted reverse proxies, optional guest-network ingress allowlist, IP-bound tokens, bounded sessions and rate limits.
- Config/options, reauthentication/reconfiguration, legacy config migration and awaited shutdown/reload cleanup.

### Important boundaries

- **Deauthentication uses the separate Hotspot Manager web API**, not the documented External Portal API. It follows the operator UI flow observed on Omada **6.0.0.39** (see [protocol notes](docs/hotspot-deauthentication.md)). The operator needs client-management permission for the site. Other controller versions may differ. Disable **Enable guest deauthentication** if your controller does not support it. Live traffic-stop behavior still needs testing on your controller; no zero-duration workaround or history deletion is used.
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

The controller URL can be an origin plus a separate Controller ID, or a controller UI URL containing the ID. For example, `https://controller.local:8043/abc123/` normalizes to origin `https://controller.local:8043` and ID `abc123`. Controller reverse-proxy subpaths are not supported. Use the exact `site` value from the redirect (typically the site ID, not its display name).

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

## Returning guests and reload behavior

After a successful request, the portal saves only the guest's **name and note** in that browser's local storage. A new visit to the same portal/controller/site prefills those fields for up to **30 days since the last successful submission**. By default, guests can edit them, uncheck **Remember my name and note**, or use **Forget saved details**. Both controls can be hidden independently in integration options; hiding the checkbox keeps browser-side remembering enabled, while hiding the forget button removes only its portal UI. These controls also appear inside `form_html` in custom Jinja templates.

This is browser-local convenience, not identification or automatic approval. A new session still requires a request and fresh terms acceptance. No request/session tokens or consent are stored in the remembered profile; other guests' history is never looked up by MAC. The fields are shared with anyone using that same browser profile. Forgetting details clears the browser copy, not HA's retained request history. Expired profiles are discarded on the next visit.

HA restarts do not erase this browser storage, but Android captive windows may discard it, and Chrome/private tabs/other browsers have separate storage. If storage is disabled, the form continues to work without prefilling. Details submitted before installing this feature are not retroactively remembered.

Reloading a still-valid portal session restores its request instead of creating a new one and does not consume the new-session rate limit. Approved pages **do not automatically redirect**: use **Continue to the internet**, or close the portal and browse normally. This prevents an authorization/connectivity delay from bouncing the phone repeatedly between Omada and the portal. If the link returns to the portal, check actual guest internet authorization in Omada; a local approved record is not proof that traffic is passing.

## Portal branding and terms

Open the integration's **Configure / Options** dialog:

| Setting | Default / limit |
| --- | --- |
| Portal title | `Guest Wi-Fi`; nonblank, up to 80 characters |
| Welcome message | `Request internet access from your host.`; up to 500 characters |
| Accent color | `#1769aa`; six-digit `#RRGGBB` |
| Guest Wi-Fi terms | Empty; up to 4,000 characters, multiline plain text |
| Require acceptance | Off; requires nonblank terms when enabled |
| Custom portal CSS | Empty; up to 20,000 characters |
| Full portal Jinja template | Empty uses the built-in layout; up to 50,000 characters |
| Decision users | Empty; selected active HA users may approve, deny, and cancel access; administrators always retain access |
| Forget all local guest records | Off; one-time removal of local requests, sessions, and history |

Title, message, and terms remain escaped plain text. Everything can be served locally: no external fonts, images, or scripts are needed before authorization. Choose an accent with sufficient contrast against white button text.

When acceptance is required, both the browser form and server enforce it before creating a request. The server records its own timestamp, the SHA-256 hash of the displayed terms (UTF-8, outer whitespace trimmed), and a snapshot of that text. Browser-supplied timestamps, hashes, and terms text are ignored. Informational terms without mandatory acceptance do **not** create a consent record.

Saving options reloads the listener: guests with an open page should reconnect. Changes apply to new requests; existing requests retain their original consent (or lack of consent). They are not silently re-consented or automatically revoked. This is a self-reported acceptance record, not verification of a guest's legal identity.

Use **Forget all local guest records** when you have removed guests directly in Omada Hotspot Manager and want HA's request/session list to start fresh. It clears only this integration's local request records, active-session list, and history. It **does not** disconnect anyone in Omada, alter controller credentials/settings, or clear names/notes saved in a guest's browser. The checkbox is a one-time action and is not retained after saving.

### Full-page Jinja layouts

Paste [examples/portal.html.jinja](examples/portal.html.jinja), [portal-animated.html.jinja](examples/portal-animated.html.jinja), [portal-midnight.html.jinja](examples/portal-midnight.html.jinja), or [portal-midnight-animated.html.jinja](examples/portal-midnight-animated.html.jinja) into **Full portal Jinja template** in integration options. The animated variants use the configured accent color and honour reduced-motion preferences. For a more elaborate, calm celestial scene, use [portal-celestial.html.jinja](examples/portal-celestial.html.jinja): slow aurora ribbons, orbital lights, and drifting stardust surround a centered midnight form. It uses the configured accent color, includes a pause-background checkbox, respects reduced motion, and needs no external assets. Existing templates are unchanged. Leave it blank to restore the built-in layout. The template is stored in the config entry, not loaded from a filesystem path. Both initial and resumed-request layouts are validated on save; template errors are reported in the options dialog.

Available variables:

| Variable | Meaning |
|---|---|
| `title`, `message`, `accent` | Configured portal branding, automatically HTML-escaped |
| `terms_text`, `require_terms` | Configured plain-text terms and acceptance requirement |
| `request_id` | This browser's resumed request ID, or `none` |
| `style_html` | Built-in styles followed by your custom CSS; include in `<head>` |
| `form_html` | Required request form, with multiline note and terms controls |
| `status_html` | Required request-status region |
| `script_html` | Required session-bound submission/recovery/polling script; include last in `<body>` |

Include `form_html`, `status_html`, and `script_html` **exactly once**, outside comments and visible page containers. Style or position them with CSS; do not duplicate the form, change its IDs, or nest it inside another form. The renderer verifies required fragments are present, but cannot guarantee arbitrary custom HTML/CSS is visually usable. Keep `style_html` if you want the integration's custom-CSS setting to apply.

The Jinja environment is sandboxed and autoescaped. It receives **no HA state/functions, controller credentials, filesystem loader, other guests, or arbitrary redirect parameters**. The CSP allows the built-in nonced script, inline styles, and same-origin/data-URI images. Remote fonts, remote scripts, third-party images, and inline event handlers are blocked. To add a logo, embed a data-URI image or serve it through your portal reverse proxy at the same origin; the integration itself does not provide arbitrary file hosting.

### Approval durations and cancelling sessions

In integration options, set:

- **Default access duration:** `8` hours by default. Used by service calls with no duration and by cards without a duration override.
- **Approval dropdown presets:** `1,2,4,8,12,24,48,72,168,0` by default. Use `0` for **Forever** (a finite 3650-day duration; controller acceptance still needs live verification); otherwise use comma-separated whole hours from **1 to 168 (one week)**. These are UI suggestions, not a permission policy; explicit administrator service calls accept `0` or 1–720 hours.
- **Enable guest deauthentication:** on by default. Adds **Cancel access** for active grants. It uses the same operator credentials to discover the active External Portal grant, calls Hotspot Manager's disconnect command, and confirms that no matching grant remains active before marking the request revoked. A failure leaves the local grant unchanged and shows an error.

A cancellation removes portal internet authorization; it does not necessarily disassociate the radio/Wi-Fi link. Guest access is denied until a new authorization is issued. If the operator lacks permission, the record is missing/ambiguous, or the controller still reports it active, use Hotspot Manager to inspect it. A timeout after the command may have cancelled access even though HA reports uncertainty; do not treat the local grant list as authoritative live state.

## Request-management dashboard card

The integration serves the bundled card through **HA's normal HTTP interface**, not the guest listener.

The card resource is **registered automatically** when the integration loads. Existing local resource entries are upgraded to the installed integration version and duplicates for this card are consolidated; unrelated resources are untouched. You do not need to add or update it manually in the usual storage-managed resource mode. Reload an already-open dashboard after an integration update. This registers the resource, not a card on your dashboard.

For **YAML-managed resources**, add this under `lovelace.resources` in `configuration.yaml` instead (the integration does not rewrite your YAML):

```yaml
lovelace:
  resources:
    - url: /omada_guest_access/omada-guest-access-card.js?v=1.3.1
      type: module
```

Refresh the dashboard, select **Add card → Omada Guest Access**, and use the visual editor. It suggests sensors from the same integration entry when available; confirm both selections. You can also use a Manual card:

```yaml
type: custom:omada-guest-access-card
pending_entity: sensor.pending_requests
active_entity: sensor.active_sessions
title: Guest Wi-Fi
# duration_hours: 8  # optional card override; omit to follow integration options
```

Entity IDs may have suffixes if multiple sites/integrations are installed. Find them under the integration's entities. Administrators and users selected in integration options see enabled approval controls. Errors are displayed in the card; grants show their scheduled expiry and a Cancel access button when deauthentication is enabled.

### Visual editor and visibility

Edit the card to choose sensors, title, optional default-duration override (`0` for the 10-year Forever preset or 1–720 hours; blank follows integration options), history page size (1–50), compact spacing, and what to display. Existing YAML remains compatible; all sections/details are shown by default.

| YAML option | Default | Controls |
|---|---|---|
| `show_title` | `true` | Card title |
| `show_pending` | `true` | Pending requests section |
| `show_active` | `true` | Unexpired grants section |
| `show_history` | `true` | Admin history section; hidden history is not fetched |
| `show_notes` | `true` | Guest notes in pending requests and history |
| `show_mac` | `true` | MAC address labels |
| `show_timestamps` | `true` | Requested, expiry, update, and consent timestamps |
| `show_actions` | `true` | Approve, deny, and cancellation buttons |
| `show_duration_selector` | `true` | Alternative-duration dropdown and approval button |
| `show_notices` | `true` | Explanatory/admin notices (errors remain visible) |
| `show_empty` | `true` | Empty pending/grant sections |
| `show_history_details` | `true` | Expandable history details |
| `show_decision_user` | `true` | HA decision user IDs in history |
| `show_terms` | `true` | Consent/terms details in history |
| `compact` | `false` | Reduced spacing |
| `history_page_size` | `20` | Requests per history page, 1–50 |

Both sensor selections are still required, including for a history-only card. Visibility is a presentation setting, **not an access-control or redaction mechanism**: HA permissions and backend history authorization still apply. Turning off MAC labels does not redact arbitrary guest notes or terms text.

For example, a minimal pending-request card (also configurable entirely in the visual editor):

```yaml
type: custom:omada-guest-access-card
pending_entity: sensor.pending_requests
active_entity: sensor.active_sessions
title: Guest approvals
duration_hours: 4
show_active: false
show_history: false
show_mac: false
show_notices: false
compact: true
```

### Request history

Administrators can select **Show history**, filter by status, or search by name, MAC address, or request ID. Pages contain 20 requests by default (configurable from 1–50), newest first. **Request details** shows timestamps, denial reason, the HA decision user ID, and any recorded terms acceptance and text. Use **Search / refresh** for fresh data; history is a snapshot and is not streamed. Expanded details remain open across HA state updates, refreshes, and pagination within the card session; search input and focus survive updates too. A dashboard reload or card reconfiguration resets this temporary state. New requests or retention cleanup between page loads can shift page boundaries.

The card obtains the integration entry ID from the pending sensor. History uses HA's authenticated WebSocket command `omada_guest_access/history`, with server-side admin checks; it is never exposed on the guest port or added to sensor attributes. Requests for missing, unloaded, or unrelated entries are rejected. Automation decisions and older records can lack a user ID. This is a browsable current/final-state record, not an append-only controller audit log.

Example command for an authenticated HA administrator:

```json
{"id": 1, "type": "omada_guest_access/history", "entry_id": "YOUR_ENTRY_ID", "status": "denied", "query": "Alex", "offset": 0, "limit": 20}
```

Omit `status` for all statuses. `query` is case-insensitive (maximum 120 characters), `offset` is zero-based, and `limit` is 1–50. The result contains `requests`, `total`, `offset`, and `limit`. Controller credentials and internal redirect/session context are never returned.

## Entities and services

| Item | Meaning |
| --- | --- |
| Pending requests sensor | Count, `requests`, `entry_id`, `default_duration`, and `duration_options` attributes. |
| Active sessions sensor | Count and `sessions` attribute for local unexpired grants; `controller_confirmed: false`. |
| Portal online | Dedicated local listener is running. |
| Controller online | Most recent controller authentication/health check succeeded. |
| `omada_guest_access.approve_request` | `request_id`, optional `duration_hours` (`0` for the 10-year Forever preset, otherwise 1–720). |
| `omada_guest_access.deny_request` | `request_id`, optional guest-visible `reason` (up to 500 characters). |
| `omada_guest_access.revoke_access` | `request_id`; controller deauthentication with confirmation before local revocation. |
| `omada_guest_access.set_guest_label` | `request_id`, optional `label`; set a local admin label without replacing the submitted name. |

Services accept administrators, users selected in **Users allowed to approve, deny, or cancel guest access**, and trusted HA automation/system contexts. User-initiated decisions persist `decision_user_id`; system decisions have no user ID. Events include `entry_id` for multi-site automation routing.

Events: `omada_guest_access_request_created`, `omada_guest_access_request_approved`, `omada_guest_access_request_denied`, `omada_guest_access_request_expired`, `omada_guest_access_access_revoked`, and `omada_guest_access_omada_api_error`.

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

## Upgrade notes for 1.4.6

- Fixes authorization failing with HTTP 302 when Omada redirects an expired operator session to its login page. Recognized same-controller login redirects trigger one fresh login and one retry, for authorization and Hotspot Manager actions.
- Redirects are never followed; unrelated or external redirects remain errors. Repeated login redirects fail without looping or marking a guest approved.
- Update/redownload in HACS and restart Home Assistant, then retry approval. Existing credentials and guest records are preserved.

## Upgrade notes for 1.3.2

- In **Settings → Devices & services → Omada Guest Access → Configure**, check **Forget all local guest records** and save to remove stale local records after manual Hotspot Manager changes. This does not deauthenticate Omada guests.

## Upgrade notes for 1.3.1

- Update/redownload through HACS and restart HA, then refresh the dashboard. Storage-managed card resources update automatically; do not add another resource. YAML-managed resources still need the URL above.
- Guest names and multiline notes are remembered after the next successful submission, subject to the browser-storage limits described above.
- Automatic approval redirects are replaced by an explicit continue link; valid-session reloads no longer exhaust the session-creation limit. Restarting clears the previous in-memory attempt counters; reconnect to guest Wi-Fi for a fresh portal session.
- The separate **header/footer settings are removed** and previously stored values are ignored. Custom CSS and full Jinja layouts remain. Legacy `header_html` / `footer_html` template variables render empty for compatibility; remove those placeholders from existing templates. Put any desired page layout directly in the Jinja template instead.

## Upgrade notes for 1.3.0

- Update/redownload through HACS and restart Home Assistant.
- **Change the existing resource URL** to `/omada_guest_access/omada-guest-access-card.js?v=1.3.0` (do not add a duplicate), then reload the dashboard/browser. The new editor and history fix require the new JavaScript; a cached 1.2.0 resource will keep the old behavior.
- Edit the card to use the visual editor. Existing YAML works unchanged; visibility options default to the previous layout. Clear any `duration_hours` override to follow integration defaults. Configure durations and portal customization under integration options.
- Adds controller-backed cancellation via Hotspot Manager, configurable approval presets (1h–1 week), header/footer HTML, custom CSS, and sandboxed Jinja page templates.
- Fixes history disclosures closing on HA updates. Adds visual configuration, same-entry entity suggestions, visibility switches, compact spacing, and configurable history page size. Active sensor attributes now include the integration entry ID to pair the suggested sensors reliably.

## Upgrade notes for 1.2.3

- Update/redownload through HACS and restart HA. The guest portal update needs no proxy header changes.
- Reloading the guest page now restores the existing request and polls its status instead of showing a fresh form. Recovery uses an HttpOnly, SameSite=Lax cookie, marked Secure when the configured public portal URL uses HTTPS. Keep that public URL set correctly behind a reverse proxy.
- Recovery is restricted to the same browser, guest IP and Omada redirect context. It lasts for the original portal session (at least 20 minutes, or the pending timeout plus 5 minutes). Cookies must be enabled. HA restart/integration reload clears browser sessions, but not stored requests; reconnect to guest Wi-Fi for a new session. Recovery never identifies a guest by MAC address alone.
- The optional note is now multiline (still limited to 500 characters); line breaks are retained in the request and dashboard card.
- The existing request card is unchanged: no new resource version is required. A copyable [card configuration](examples/dashboard-card.yaml) is included; adjust its sensor IDs to your installation.

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
6. Use **Cancel access** on a test guest and confirm internet traffic stops and the Omada record is inactive. Verify operator permissions; no live deauthentication has been performed by the test suite. Manual controller changes still are not continuously reconciled into the local grant list.

GitHub Actions runs HACS validation, hassfest, Ruff, the Python test suite, and the Chromium browser smoke test (mocked HA/controller traffic).

## Upgrade notes for 1.4.8

Authorization now sends a relative duration in microseconds as a decimal string,
not an absolute Unix timestamp. For example, 24 hours sends `"86400000000"`.
Local expiry remains approval time plus the requested duration. The prior epoch
encoding could produce excessively long grants. Update in HACS and restart HA;
existing controller grants are not retroactively corrected. End incorrect grants
in Hotspot Manager and submit a fresh request. Verify with a one-hour approval
that Omada records expiry one hour after authorization before using longer grants.
Forever is a finite 3650-day request, not a verified unlimited-access sentinel.
