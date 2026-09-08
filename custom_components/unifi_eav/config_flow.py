"""Config, reauth and options flows for UniFi EAV."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UnifiEavApi, UnifiEavAuthError, UnifiEavConnectionError, UnifiEavError
from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SITE,
    CONF_UNIFI_OS,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SITE,
    DOMAIN,
    LOGGER,
)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_SITE, default=DEFAULT_SITE): str,
        vol.Optional(CONF_UNIFI_OS, default=True): bool,
        vol.Optional(CONF_VERIFY_SSL, default=False): bool,
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str})


def make_api(hass: HomeAssistant, data: Mapping[str, Any]) -> UnifiEavApi:
    """Build a client from config-entry data."""
    return UnifiEavApi(
        async_get_clientsession(hass, verify_ssl=data.get(CONF_VERIFY_SSL, False)),
        host=data[CONF_HOST],
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
        site=data.get(CONF_SITE, DEFAULT_SITE),
        unifi_os=data.get(CONF_UNIFI_OS, True),
    )


async def _validate(hass: HomeAssistant, data: Mapping[str, Any]) -> str | None:
    """Try login + matrix read; return an error key or None."""
    try:
        api = make_api(hass, data)
        await api.login()
        await api.get_matrix()
    except UnifiEavAuthError:
        return "invalid_auth"
    except UnifiEavConnectionError:
        return "cannot_connect"
    except UnifiEavError as err:
        return "no_proav" if err.status == 404 else "unknown"
    except Exception:  # noqa: BLE001
        LOGGER.exception("Unexpected error validating UniFi EAV connection")
        return "unknown"
    return None


class UnifiEavConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the user and reauth steps."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> UnifiEavOptionsFlow:
        return UnifiEavOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_HOST].lower())
            self._abort_if_unique_id_configured()
            if (error := await _validate(self.hass, user_input)) is None:
                return self.async_create_entry(title=user_input[CONF_HOST], data=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(STEP_USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            if (error := await _validate(self.hass, {**entry.data, **user_input})) is None:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                STEP_REAUTH_SCHEMA, {CONF_USERNAME: entry.data[CONF_USERNAME]}
            ),
            description_placeholders={CONF_HOST: entry.data[CONF_HOST]},
            errors=errors,
        )


class UnifiEavOptionsFlow(OptionsFlowWithReload):
    """Poll interval; the entry reloads on save."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300))
                }
            ),
        )
