# Omada Guest Access

[![HACS](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)

A Home Assistant custom integration for managing Omada guest Wi-Fi access requests. It provides the Home Assistant foundation for a separate captive portal: guests request access, and an administrator can approve, deny, or revoke it from Home Assistant.

> **Status: alpha.** The request queue, dedicated guest portal, Home Assistant services, events, persistent storage, and request expiry are implemented. Controller-specific authorization is the next compatibility layer; no guest is marked as authorized by Omada until that adapter is configured and verified.

## HACS installation

1. In HACS, open **Integrations** → the three-dot menu → **Custom repositories**.
2. Add `https://github.com/abangtor/omada-guest-access` with category **Integration**.
3. Search for **Omada Guest Access**, download it, and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration**, then choose **Omada Guest Access**.

## What works

- A dedicated portal listener (default `8088`) with a mobile-friendly request form and JSON API.
- Persistent request history via Home Assistant storage, including pending-request expiry.
- Home Assistant events and services to approve, deny, and revoke requests.
- Pending and active-session sensors.

The portal accepts `POST /api/request` and exposes `GET /api/request/{request_id}`. Configure Omada's external portal redirect to include the connected client MAC address and place the portal behind TLS. The guest portal must only be reachable from the guest network/reverse proxy; it is intentionally not an authenticated HA UI.

## Entities

- `sensor.omada_guest_access_pending_requests`
- `sensor.omada_guest_access_active_sessions`
- `binary_sensor.omada_guest_access_portal_online`

## Current services

- `omada_guest_access.approve_request`
- `omada_guest_access.deny_request`
- `omada_guest_access.revoke_access`

## Planned architecture

- A dedicated, reverse-proxy-friendly portal listener (default port `8088`).
- Persistent guest request queue and audit log.
- Omada controller adapter for direct authorization or voucher issuance.
- Actionable notifications and dashboard controls.

## Development

Install this repository under `custom_components/omada_guest_access/` in a Home Assistant development environment, then use the config flow to create an entry.

