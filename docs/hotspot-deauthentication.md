# Hotspot Manager deauthentication protocol

This is an optional controller web-API adapter, separate from TP-Link's documented External Portal authorization API. No additional management/admin credentials are used.

## Evidence

Read-only inspection of the JavaScript served by an Omada 6.0.0.39 controller on 2026-09-30:

- `/modules/hotspot/authClients/models-6cb1dcaf16.js`: `authClientsStore` lists `/api/v2/hotspot/sites/<site>/clients`, with `id`, `mac`, `ssid`, `authType`, and `valid` fields.
- `/modules/hotspot/authClients/controllers-e3775a6bfc.js`: the valid-client **Unauthorize** action posts to `/api/v2/hotspot/sites/<site>/cmd/clients/<record-id>/disconnect`. The distinct DELETE endpoint removes historical records and is **not** used for deauthentication.
- `/js/su/widget-62e5b9990c.js`: pagination uses `currentPage`, `currentPageSize`, and `totalRows`.

The full controller route includes `/<controller-id>` before `/api/v2`. Authentication uses the existing operator cookie and `Csrf-Token` header. These asset hashes are evidence for that build, not paths the integration fetches at runtime.

## Adapter behavior

1. Read the site's paginated Hotspot Manager records (bounded to 10,000; reject invalid/incomplete responses).
2. Find exactly one valid External Portal (`authType: 4`) record matching the normalized MAC, and SSID when present in the original context. Never select another authorization method or guess a record ID from the MAC.
3. POST its disconnect command. Do not send a zero-duration grant or DELETE the record.
4. Re-read records, allowing short propagation delay. Only succeed when the target is inactive/absent and no matching active External Portal grant remains.
5. Persist local `revoked` status and emit the existing event only after success.

On missing/ambiguous grants, permission failures, transport errors, malformed data, or unconfirmed disconnects, return an error and leave the local grant state unchanged. The operator must have client-management permission on the selected site, not merely view permission.

## Verification boundary

Tests use real local HTTP mock servers for pagination, cookies, CSRF, expired-session retries, AP/gateway contexts, command failures, and confirmation. The controller web assets establish the endpoint and intended **Unauthorize** semantics; they do not establish successful authenticated behavior or packet filtering on every firmware. Live deauthentication and a phone traffic-stop test remain required after deployment. Devices may stay associated to Wi-Fi while their internet authorization is removed.
