"""plan_route table + coordinator name handling and auth failure."""

import copy

from homeassistant.config_entries import SOURCE_REAUTH

from custom_components.unifi_eav.coordinator import MatrixData, plan_route

from .conftest import DEVICE_BASIC, G1, LOGIN, MATRIX, RX1, RX2, RX3, TX1, TX2, fixture, register_console

NAMES = {TX1: "Apple TV", TX2: "Cable Box"}


def data(groups=None) -> MatrixData:
    m = fixture("matrix.json")
    return MatrixData(tx=m["hosts"], rx=m["clients"], groups=groups if groups is not None else m["groups"], names=NAMES)


def test_unrouted_rx_to_tx_without_group_posts_unicast():
    ops = plan_route(data(), RX3, TX2, "Cable Box")
    assert [(op, gid) for op, gid, _ in ops] == [("POST", None)]
    body = ops[0][2]
    assert body["name"] == "Cable Box"
    assert body["host"] == {"mac": TX2, "host_type": "VIDEO", "refresh_rate": "AUTO", "enabled": True}
    assert [(c["mac"], c["mode"]) for c in body["clients"]] == [(RX3, "UNICAST")]


def test_unrouted_rx_joins_existing_group_multicast():
    ops = plan_route(data(), RX3, TX1, "Apple TV")
    assert [(op, gid) for op, gid, _ in ops] == [("PUT", G1)]
    body = ops[0][2]
    assert body["id"] == G1 and body["channel"] == 1  # group echoed as read
    assert [(c["mac"], c["mode"]) for c in body["clients"]] == [(RX1, "MULTICAST"), (RX2, "MULTICAST"), (RX3, "MULTICAST")]


def test_move_leaves_old_group_first_and_flips_it_to_unicast():
    ops = plan_route(data(), RX2, TX2, "Cable Box")
    assert [(op, gid) for op, gid, _ in ops] == [("PUT", G1), ("POST", None)]
    assert [(c["mac"], c["mode"]) for c in ops[0][2]["clients"]] == [(RX1, "UNICAST")]
    assert [(c["mac"], c["mode"]) for c in ops[1][2]["clients"]] == [(RX2, "UNICAST")]


def test_sole_client_leaving_deletes_group_then_joins():
    groups = copy.deepcopy(fixture("matrix.json")["groups"])
    g2 = copy.deepcopy(groups[0])
    g2.update(id="g2", channel=2, name="Cable Box", host={**g2["host"], "mac": TX2})
    g2["clients"] = [{**g2["clients"][0], "mac": RX3, "mode": "UNICAST"}]
    ops = plan_route(data(groups + [g2]), RX3, TX1, "Apple TV")
    assert [(op, gid) for op, gid, _ in ops] == [("DELETE", "g2"), ("PUT", G1)]
    assert ops[0][2] is None
    assert [c["mode"] for c in ops[1][2]["clients"]] == ["MULTICAST"] * 3


def test_already_routed_is_noop():
    assert plan_route(data(), RX1, TX1, "Apple TV") == []


async def test_names_from_device_basic_with_mac_fallback(hass, setup):
    d = setup.runtime_data.data
    assert d.name(TX1) == "Apple TV"
    assert d.name(RX3) == RX3  # not named in device-basic


async def test_device_basic_failure_is_tolerated(hass, config_entry, aioclient_mock):
    register_console(aioclient_mock, device_basic_status=500)
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.runtime_data.data.name(TX1) == TX1
    assert hass.states.get(f"media_player.{RX1.replace(':', '_')}").attributes["source"] == TX1


async def test_device_basic_fetched_once(hass, setup, mock_console):
    await setup.runtime_data.async_refresh()
    assert sum(1 for m, u, _, _ in mock_console.mock_calls if str(u) == DEVICE_BASIC) == 1


async def test_auth_failure_on_poll_starts_reauth(hass, setup, mock_console):
    mock_console.clear_requests()
    mock_console.post(LOGIN, status=401)
    mock_console.get(MATRIX, status=401)
    await setup.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert not setup.runtime_data.last_update_success
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
