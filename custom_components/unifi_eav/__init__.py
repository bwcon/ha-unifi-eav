"""UniFi Pro AV (EAV-Bridge) video-matrix integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .config_flow import make_api
from .const import PLATFORMS
from .coordinator import UnifiEavConfigEntry, UnifiEavCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: UnifiEavConfigEntry) -> bool:
    coordinator = UnifiEavCoordinator(hass, entry, make_api(hass, entry.data))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    # Hub first (via_device target), then every bridge. TXs have no entity,
    # so they only exist because they are registered here.
    registry = dr.async_get(hass)
    registry.async_get_or_create(config_entry_id=entry.entry_id, **coordinator.hub_device_info())
    for mac in coordinator.data.tx + coordinator.data.rx:
        registry.async_get_or_create(config_entry_id=entry.entry_id, **coordinator.device_info(mac))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: UnifiEavConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
