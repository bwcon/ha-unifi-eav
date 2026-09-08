# Design — UniFi EAV-Bridge (Pro AV) virtual matrix for Home Assistant

Spec record copied from the approved plan (2026-09-08). The API section is the source of truth for the routing rules; the Deliverable 1 section is what this repo implements.

## Reverse-engineered UniFi API (source of truth for all three deliverables)
Auth (UniFi OS console): `POST https://<console>/api/auth/login` `{"username","password"}` (local account, not SSO) → `TOKEN` cookie (JWT). Writes need `X-CSRF-Token` header (from login response header `X-CSRF-Token`/`X-Updated-CSRF-Token`); GET needs none. Prefix `/proxy/network` on UniFi OS; none on legacy standalone controller (`POST /api/login`). Self-signed TLS.
Base `https://<console>/proxy/network/v2/api/site/<site>/proav/video`:
| Op | Method | Path |
|---|---|---|
| Read matrix | GET | `/matrix` → `{bridges[],hosts[],clients[],host_candidates[],client_candidates[],groups[]}` (MAC lists) |
| Create route | POST | `/group` → 201, server assigns `id`,`channel` |
| Update route | PUT | `/group/<id>` |
| Delete route | DELETE | `/group/<id>` |
Group: `{name, type:"VIDEO", host:{mac, host_type:"VIDEO", refresh_rate:"AUTO", enabled:true}, clients:[{mac, client_type:"VIDEO", mode:"UNICAST"|"MULTICAST", resolution:"AUTO", color_format:"RGB", audio_enabled:true, audio_mode:"PCM", enabled:true}]}`.
Switching: RX watches the TX whose `group.clients` holds its MAC. Route = remove RX from current group (PUT shorter list, DELETE if empty) then add to TX group (PUT, or POST new). One group per TX. Bridge names not in `/matrix` → `GET /proxy/network/api/s/<site>/stat/device-basic` (same cookie auth; fallback MAC).
Shared routing rule (both drivers): leave old group first; `mode` = MULTICAST when resulting group has ≥2 clients else UNICAST, recomputed on both touched groups; PUT echoes the group as read with only `clients` replaced (`# verify on hardware`: strip `id`/`channel` if rejected; fallback always-MULTICAST if mixed modes rejected).

---

# Deliverable 1 — Home Assistant integration `unifi_eav`
Folder `D:\Coding Projects\UniFi EAV for HA`, repo `bwcon/ha-unifi-eav` (private now; public before HACS submission).
Mirror `D:\Coding Projects\Crestron Home for HA\custom_components\crestron_home\` (api.py exception ladder + 401→relogin→retry; coordinator `UpdateFailed` shell; config_flow error mapping; strings/translations; hassfest+hacs workflow). Fix its gaps: add `available`, 10 s request timeout, reauth, tests, `integration_type`, one `device_info()` source, `OptionsFlowWithReload`, `entry.runtime_data`. aiohttp only (no `aiounifi`: pin conflicts with core).

**Entities**: one `media_player` per RX (HA idiom for HDMI matrices, cf. core `blackbird`/`monoprice`): `source_list` = TX names, `select_source` routes, `turn_on/off` flips client `enabled`, `device_class` RECEIVER, attrs `tx_mac, group_id, channel, mode`, `available` = coordinator ok ∧ MAC in `clients`. Devices: hub (console) + one device per bridge (TX registered in `__init__`, `via_device` hub, manufacturer Ubiquiti, model/name/sw from device-basic). Skipped (YAGNI): binary_sensors, custom services, snapshot entity, dynamic entity add without reload.

**Files**
```
custom_components/unifi_eav/
  __init__.py      setup/unload, device registry, runtime_data, PLATFORMS=[media_player]
  api.py           UnifiEavApi(session, host[, scheme], username, password, site, verify_ssl, unifi_os): login(), _request() (Cookie header forwarding — HA jar drops IP-host cookies; X-CSRF-Token on writes; refresh csrf from X-Updated-CSRF-Token; 401→login→retry once), get_matrix/get_devices_basic/create_group/update_group/delete_group; UnifiEavError(status)/UnifiEavAuthError/UnifiEavConnectionError
  coordinator.py   MatrixData(tx, rx, groups, names; route_of(), group_for_tx(), name()); UnifiEavCoordinator(_async_update_data → ConfigEntryAuthFailed/UpdateFailed; names fetched when unknown MAC appears, tolerate failure; route(); set_enabled(); device_info(mac); one asyncio.Lock around writes) ; pure plan_route(data, rx, tx, tx_name) → [(op, group_id, body)]
  config_flow.py   user step (host may carry scheme for mock; unique_id host.lower()); reauth/reauth_confirm (async_update_reload_and_abort); UnifiEavOptionsFlow(OptionsFlowWithReload): scan_interval 5–300 default 15. Errors: AuthError→invalid_auth, ConnectionError→cannot_connect, status 404→no_proav, else unknown
  media_player.py  UnifiEavReceiver(CoordinatorEntity, MediaPlayerEntity); ServiceValidationError (unknown_source, not_routed); HomeAssistantError on API failure; await coordinator.async_refresh() after writes (no optimistic mutation)
  const.py, manifest.json, strings.json, translations/en.json, brand/{icon,logo}.png
tests/  conftest.py (enable_custom_integrations; mock_console via phcc aioclient_mock), fixtures/{matrix,device_basic}.json (2 TX, 3 RX, one MULTICAST group, one unrouted RX, one un-named bridge), test_config_flow.py, test_coordinator.py (plan_route table: POST/UNICAST; move with old group keeping 1 → PUT UNICAST + PUT MULTICAST; sole client → DELETE + PUT; no-op), test_media_player.py (exact HTTP sequence incl. CSRF header; 401 relogin)
tools/mock_unifi.py   aiohttp mock console (plain HTTP): login/CSRF, TOKEN cookie check (401), CSRF check on writes (403), matrix, group CRUD (201 uuid id + channel), device-basic, enforces RX-in-one-group (400), --expire-after N, --port
.github/workflows/validate.yml (hassfest, hacs/action ignore=brands, pytest py3.13) ; hacs.json {name, homeassistant} ; README (my-link badge, config, mock section, "no snapshot yet") ; LICENSE MIT ; pyproject.toml (asyncio_mode auto) ; requirements_test.txt (pytest-homeassistant-custom-component) ; .gitignore
```
**HACS readiness**: manifest keys domain, name, codeowners ["@bwcon"], config_flow, documentation, integration_type "hub", iot_class "local_polling", issue_tracker, requirements [], version "0.1.0"; GitHub Release `v0.1.0` (tag = manifest version); repo description + topics + issues on; brands PR to `home-assistant/brands` (`custom_integrations/unifi_eav/icon.png` 256 + @2x 512, logo) then drop `ignore: brands`; `hacs/default` PR after hardware validation + repo public.
**Order**: scaffold → api.py + mock (curl smoke) → coordinator + fixtures + tests → config_flow + tests → __init__ + media_player + tests → manual E2E in dev HA against mock (hub + 5 bridge devices, 3 media_players, select_source op sequence in mock log, options reload, --expire-after relogin, wrong password → reauth) → tag v0.1.0 + release.
