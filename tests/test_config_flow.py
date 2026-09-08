"""User, reauth and options flows."""

from datetime import timedelta

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType

from custom_components.unifi_eav.const import CONF_SCAN_INTERVAL, DOMAIN

from .conftest import BASE, ENTRY_DATA, LOGIN, MATRIX


async def _start(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    return result


async def test_user_success(hass, mock_console):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == BASE
    assert result["data"] == ENTRY_DATA
    assert result["result"].unique_id == BASE


@pytest.mark.parametrize(
    ("login_kwargs", "matrix_kwargs", "error"),
    [
        ({"status": 401}, {}, "invalid_auth"),
        ({"status": 429}, {}, "unknown"),
        ({"exc": aiohttp.ClientConnectionError("boom")}, {}, "cannot_connect"),
        ({"json": {}, "cookies": {"TOKEN": "t"}}, {"status": 404}, "no_proav"),
        ({"json": {}, "cookies": {"TOKEN": "t"}}, {"status": 500}, "unknown"),
    ],
)
async def test_user_errors(hass, aioclient_mock, login_kwargs, matrix_kwargs, error):
    aioclient_mock.post(LOGIN, **login_kwargs)
    aioclient_mock.get(MATRIX, **matrix_kwargs)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_user_already_configured(hass, setup):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ENTRY_DATA)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(hass, setup, mock_console):
    setup.async_start_reauth(hass)
    await hass.async_block_till_done()
    (flow,) = hass.config_entries.flow.async_progress()
    assert flow["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(flow["flow_id"], {"username": "admin", "password": "new"})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert setup.data[CONF_PASSWORD] == "new"


async def test_options_reload_scan_interval(hass, setup):
    result = await hass.config_entries.options.async_init(setup.entry_id)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(result["flow_id"], {CONF_SCAN_INTERVAL: 30})
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup.options == {CONF_SCAN_INTERVAL: 30}
    assert setup.runtime_data.update_interval == timedelta(seconds=30)
