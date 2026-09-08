# UniFi Pro AV (EAV Bridge) for Home Assistant

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bwcon&repository=ha-unifi-eav&category=integration)
[![Validate](https://github.com/bwcon/ha-unifi-eav/actions/workflows/validate.yml/badge.svg)](https://github.com/bwcon/ha-unifi-eav/actions/workflows/validate.yml)

Route Ubiquiti **EAV-Bridge** AV-over-IP transmitters to receivers from Home Assistant — a virtual HDMI matrix driven through the UniFi Network application's Pro AV endpoints. Each receiver becomes a `media_player`; its source list is your transmitters.

> **Status: pre-hardware.** Built against the API captured by the community (see [Credits](#credits)) and a local mock console. Not yet validated on real EAV-Bridge hardware; the routing bodies carry `# verify on hardware` notes in the code where an assumption was made.

## Features

- One `media_player` per receiver (RX), device class *receiver*
  - `select_source` — move the RX to another transmitter's group (leaves the old group first; recomputes `UNICAST`/`MULTICAST` on both groups; deletes a group when its last RX leaves)
  - `turn_on` / `turn_off` — toggle the RX's `enabled` flag without changing the route
  - Attributes: `tx_mac`, `group_id`, `channel`, `mode`
- Device tree: the console as hub, every bridge (TX and RX) as a child device named from UniFi
- Local polling (default 15 s, configurable 5–300 s), cookie + CSRF session handling with transparent re-login, re-authentication flow when credentials stop working
- No extra Python requirements — `aiohttp` from Home Assistant core only

Not included yet: snapshot / "now playing" preview images (no fake data — a follow-up once the hardware shows where the Network app fetches them), audio-only routing, per-RX resolution/color settings.

## Requirements

- UniFi Network application **10.3 or newer** with Pro AV enabled, on a UniFi OS console (UDM / UCG / Cloud Key Gen2) or a standalone controller
- A **local** UniFi OS account (not Ubiquiti SSO) **without MFA**. A dedicated read/write "Network" admin is recommended.
- Home Assistant 2026.1 or newer

## Installation

**HACS:** click the badge above, or add `https://github.com/bwcon/ha-unifi-eav` as a custom repository (category *Integration*), download, restart Home Assistant.

**Manual:** copy `custom_components/unifi_eav/` into your `config/custom_components/` and restart.

## Configuration

*Settings → Devices & services → Add integration → UniFi Pro AV (EAV Bridge)*

| Field | Default | Notes |
|---|---|---|
| Console address | — | IP or hostname. A scheme and port may be included (`http://localhost:8443` for the mock). Bare hosts use `https://`. |
| Username / Password | — | Local UniFi OS account, MFA off |
| Site | `default` | UniFi Network site name (the URL slug, not the display name) |
| UniFi OS console | on | On: `/proxy/network` prefix and `/api/auth/login`. Off: legacy standalone controller (`/api/login`, no prefix). |
| Verify SSL certificate | off | Consoles ship a self-signed certificate |

**Options** (⋮ → *Configure* on the integration card): poll interval in seconds (5–300, default 15). The entry reloads on save.

## How routing works

UniFi models Pro AV as one *group* per transmitter: `{host: TX, clients: [RX, …]}`. An RX watches whichever TX's group contains its MAC. `select_source` therefore:

1. `PUT` the RX's current group without it (or `DELETE` the group if the RX was its only client)
2. `PUT` the target TX's group with the RX added, or `POST` a new group if the TX has none

`mode` is set to `MULTICAST` when the resulting group has two or more clients, otherwise `UNICAST`. Groups are echoed back as read with only `clients` replaced.

## Mock console (development)

`tools/mock_unifi.py` is a small aiohttp server that speaks the same endpoints: login with `TOKEN` cookie and `X-CSRF-Token`, `401` without a valid cookie, `403` on writes without the CSRF header, matrix, group CRUD (`201` with a server-assigned `id`/`channel`), the one-group-per-RX rule (`400`), and `device-basic` names. It serves both UniFi OS and legacy path shapes.

```bash
pip install aiohttp
python tools/mock_unifi.py --port 8443                     # http://localhost:8443, admin / password
python tools/mock_unifi.py --tls tools/mock_cert.pem tools/mock_key.pem   # https with the bundled self-signed cert
python tools/mock_unifi.py --expire-after 30               # sessions die after 30 s -> exercises re-login
```

Add the integration with console address `http://localhost:8443`, user `admin`, password `password`. The mock ships two transmitters (Apple TV, Cable Box) and three receivers, one of them un-named so you can see the MAC fallback. Every request and every write body is logged to stdout.

## Development

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements_test.txt   # Windows
pytest tests/ -q
```

Tests use `pytest-homeassistant-custom-component`; all HTTP is intercepted by its `aioclient_mock`, and `test_media_player.py` asserts the exact request sequence (including the CSRF header and a `401 → re-login → retry` case). CI runs hassfest, the HACS action, and pytest on Python 3.13.

## Credits

The Pro AV endpoints are not part of Ubiquiti's public Integration API. They were captured from the Network application by u/type111 and documented in [this r/Ubiquiti thread](https://www.reddit.com/r/Ubiquiti/comments/1tscycy/) — thank you.

## License

MIT © 2026 BW Consulting
