"""Entity state, device tree, and the exact HTTP sequence behind each service."""

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    DOMAIN as MP_DOMAIN,
    SERVICE_SELECT_SOURCE,
)
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from custom_components.unifi_eav.const import DOMAIN

from .conftest import G1, GROUP, LOGIN, MATRIX, RX1, RX2, RX3, TX1, TX2, calls, fixture, register_console

LIVING = "media_player.living_room_tv"
KITCHEN = "media_player.kitchen_tv"
UNNAMED = f"media_player.{RX3.replace(':', '_')}"


async def _call(hass, service, entity_id, **data):
    await hass.services.async_call(MP_DOMAIN, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True)


async def test_states(hass, setup):
    living = hass.states.get(LIVING)
    assert living.state == "on"
    assert living.attributes["device_class"] == "receiver"
    assert living.attributes["source"] == "Apple TV"
    assert living.attributes["source_list"] == ["Apple TV", "Cable Box"]
    assert {k: living.attributes[k] for k in ("tx_mac", "group_id", "channel", "mode")} == {
        "tx_mac": TX1, "group_id": G1, "channel": 1, "mode": "MULTICAST"
    }
    unnamed = hass.states.get(UNNAMED)
    assert unnamed.state == "idle"
    assert unnamed.attributes.get("source") is None
    assert unnamed.attributes["tx_mac"] is None


async def test_device_tree(hass, setup):
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, setup.entry_id)
    assert len(devices) == 6  # hub + 2 TX + 3 RX
    hub = registry.async_get_device(identifiers={(DOMAIN, setup.entry_id)})
    rx1 = registry.async_get_device(identifiers={(DOMAIN, RX1)})
    assert rx1.name == "Living Room TV" and rx1.via_device_id == hub.id and rx1.manufacturer == "Ubiquiti"
    assert registry.async_get_device(identifiers={(DOMAIN, TX2)}).name == "Cable Box"
    assert registry.async_get_device(identifiers={(DOMAIN, RX3)}).name == RX3


async def test_select_source_creates_group_with_csrf(hass, setup, mock_console):
    mock_console.mock_calls.clear()
    await _call(hass, SERVICE_SELECT_SOURCE, UNNAMED, **{ATTR_INPUT_SOURCE: "Cable Box"})
    assert calls(mock_console) == [("POST", GROUP), ("GET", MATRIX)]
    _, _, body, headers = mock_console.mock_calls[0]
    assert headers["X-CSRF-Token"] == "csrf1"
    assert headers["Cookie"] == "TOKEN=tok1"
    assert body["name"] == "Cable Box" and body["type"] == "VIDEO" and body["host"]["mac"] == TX2
    assert [(c["mac"], c["mode"], c["enabled"]) for c in body["clients"]] == [(RX3, "UNICAST", True)]
    assert "id" not in body
    assert "X-CSRF-Token" not in mock_console.mock_calls[1][3]  # GET carries no CSRF


async def test_select_source_move_leaves_old_group_first(hass, setup, mock_console):
    mock_console.mock_calls.clear()
    await _call(hass, SERVICE_SELECT_SOURCE, KITCHEN, **{ATTR_INPUT_SOURCE: "Cable Box"})
    assert calls(mock_console) == [("PUT", f"{GROUP}/{G1}"), ("POST", GROUP), ("GET", MATRIX)]
    put_body = mock_console.mock_calls[0][2]
    assert [(c["mac"], c["mode"]) for c in put_body["clients"]] == [(RX1, "UNICAST")]
    assert put_body["id"] == G1
    assert [(c["mac"], c["mode"]) for c in mock_console.mock_calls[1][2]["clients"]] == [(RX2, "UNICAST")]


async def test_select_source_same_tx_is_noop(hass, setup, mock_console):
    mock_console.mock_calls.clear()
    await _call(hass, SERVICE_SELECT_SOURCE, LIVING, **{ATTR_INPUT_SOURCE: "Apple TV"})
    assert calls(mock_console) == []


async def test_select_unknown_source(hass, setup):
    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_SELECT_SOURCE, LIVING, **{ATTR_INPUT_SOURCE: "Nope"})


async def test_turn_off_flips_enabled_only(hass, setup, mock_console):
    mock_console.mock_calls.clear()
    await _call(hass, SERVICE_TURN_OFF, LIVING)
    assert calls(mock_console) == [("PUT", f"{GROUP}/{G1}"), ("GET", MATRIX)]
    body = mock_console.mock_calls[0][2]
    assert [(c["mac"], c["enabled"], c["mode"]) for c in body["clients"]] == [
        (RX1, False, "MULTICAST"),
        (RX2, True, "MULTICAST"),
    ]


async def test_turn_on_unrouted_rejected(hass, setup, mock_console):
    mock_console.mock_calls.clear()
    with pytest.raises(ServiceValidationError):
        await _call(hass, SERVICE_TURN_ON, UNNAMED)
    assert calls(mock_console) == []


async def test_write_failure_raises_home_assistant_error(hass, setup, mock_console):
    mock_console.clear_requests()
    register_console(mock_console, group_status=500)
    with pytest.raises(HomeAssistantError):
        await _call(hass, SERVICE_TURN_OFF, LIVING)


async def test_401_triggers_relogin_and_retry(hass, setup, mock_console):
    statuses = iter([401, 200])

    async def flaky(method, url, data):
        return AiohttpClientMockResponse(method, url, status=next(statuses), json=fixture("matrix.json"))

    mock_console.clear_requests()
    mock_console.get(MATRIX, side_effect=flaky)
    mock_console.post(LOGIN, json={}, headers={"X-CSRF-Token": "csrf2"}, cookies={"TOKEN": "tok2"})
    mock_console.mock_calls.clear()
    await setup.runtime_data.async_refresh()
    assert calls(mock_console) == [("GET", MATRIX), ("POST", LOGIN), ("GET", MATRIX)]
    assert mock_console.mock_calls[2][3]["Cookie"] == "TOKEN=tok2"
    assert setup.runtime_data.last_update_success
    assert hass.states.get(LIVING).state == "on"
