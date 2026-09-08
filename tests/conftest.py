"""Shared fixtures: a mocked UniFi console served through HA's aiohttp session."""

import json
import pathlib
import re
import sys

import pytest
import pytest_socket
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL

from custom_components.unifi_eav.const import CONF_SITE, CONF_UNIFI_OS, DOMAIN

BASE = "http://console.test"
LOGIN = f"{BASE}/api/auth/login"
MATRIX = f"{BASE}/proxy/network/v2/api/site/default/proav/video/matrix"
GROUP = f"{BASE}/proxy/network/v2/api/site/default/proav/video/group"
DEVICE_BASIC = f"{BASE}/proxy/network/api/s/default/stat/device-basic"

TX1, TX2 = "f4:e2:c6:00:00:01", "f4:e2:c6:00:00:02"
RX1, RX2, RX3 = "f4:e2:c6:00:00:11", "f4:e2:c6:00:00:12", "f4:e2:c6:00:00:13"
G1 = "3f0c9d1e-5b1a-4c8e-9f2d-7a6b5c4d3e2f"

ENTRY_DATA = {
    CONF_HOST: BASE,
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "password",
    CONF_SITE: "default",
    CONF_UNIFI_OS: True,
    CONF_VERIFY_SSL: False,
}


def fixture(name: str) -> dict:
    return json.loads((pathlib.Path(__file__).parent / "fixtures" / name).read_text())


def register_console(aioclient_mock, *, group_status: int = 200, device_basic_status: int = 200) -> None:
    """Register the happy-path console routes (call after clear_requests to re-arm)."""
    aioclient_mock.post(LOGIN, json={"username": "admin"}, headers={"X-CSRF-Token": "csrf1"}, cookies={"TOKEN": "tok1"})
    aioclient_mock.get(MATRIX, json=fixture("matrix.json"))
    aioclient_mock.get(DEVICE_BASIC, status=device_basic_status, json=fixture("device_basic.json"))
    aioclient_mock.post(GROUP, status=201 if group_status == 200 else group_status, json={"id": "new-id", "channel": 2})
    aioclient_mock.put(re.compile(rf"{re.escape(GROUP)}/.+"), status=group_status, json={})
    aioclient_mock.delete(re.compile(rf"{re.escape(GROUP)}/.+"), status=group_status, json={})


def calls(aioclient_mock) -> list[tuple[str, str]]:
    """(METHOD, url) of every recorded request, in order."""
    return [(method.upper(), str(url)) for method, url, _, _ in aioclient_mock.mock_calls]


if sys.platform == "win32":
    # asyncio's Proactor loop needs an AF_INET socketpair; pytest-socket only exempts AF_UNIX.
    # Skip the creation guard here; phcc's socket_allow_hosts(["127.0.0.1"]) still blocks connects.
    pytest_socket.disable_socket = lambda allow_unix_socket=False: None


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/ in tests."""


@pytest.fixture
def mock_console(aioclient_mock):
    register_console(aioclient_mock)
    return aioclient_mock


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(domain=DOMAIN, title=BASE, data=ENTRY_DATA, unique_id=BASE)


@pytest.fixture
async def setup(hass, config_entry, mock_console) -> MockConfigEntry:
    """Integration set up against the mocked console."""
    config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry
