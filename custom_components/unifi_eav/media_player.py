"""One media_player per EAV receiver; sources are the transmitters."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import UnifiEavError
from .const import DOMAIN
from .coordinator import UnifiEavConfigEntry, UnifiEavCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: UnifiEavConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(UnifiEavReceiver(coordinator, mac) for mac in coordinator.data.rx)


class UnifiEavReceiver(CoordinatorEntity[UnifiEavCoordinator], MediaPlayerEntity):
    """An RX bridge; select_source moves it to another TX group."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.RECEIVER
    _attr_supported_features = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: UnifiEavCoordinator, mac: str) -> None:
        super().__init__(coordinator)
        self._mac = mac
        self._attr_unique_id = mac
        self._attr_device_info = coordinator.device_info(mac)

    @property
    def available(self) -> bool:
        return super().available and self._mac in self.coordinator.data.rx

    @property
    def _route(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        return self.coordinator.data.route_of(self._mac)

    @property
    def state(self) -> MediaPlayerState:
        if (route := self._route) is None:
            return MediaPlayerState.IDLE
        return MediaPlayerState.ON if route[1].get("enabled", True) else MediaPlayerState.OFF

    @property
    def source_list(self) -> list[str]:
        data = self.coordinator.data
        return [data.name(tx) for tx in data.tx]

    @property
    def source(self) -> str | None:
        if (route := self._route) is None:
            return None
        return self.coordinator.data.name(route[0]["host"]["mac"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        group, client = self._route or ({}, {})
        return {
            "tx_mac": group.get("host", {}).get("mac"),
            "group_id": group.get("id"),
            "channel": group.get("channel"),
            "mode": client.get("mode"),
        }

    async def async_select_source(self, source: str) -> None:
        data = self.coordinator.data
        tx = next((mac for mac in data.tx if data.name(mac) == source), None)
        if tx is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_source",
                translation_placeholders={"source": source},
            )
        await self._write(self.coordinator.route(self._mac, tx))

    async def async_turn_on(self) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self) -> None:
        await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        if self._route is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_routed",
                translation_placeholders={"name": self.coordinator.data.name(self._mac)},
            )
        await self._write(self.coordinator.set_enabled(self._mac, enabled))

    async def _write(self, coro: Awaitable[None]) -> None:
        try:
            await coro
        except UnifiEavError as err:
            raise HomeAssistantError(f"UniFi console rejected the change: {err}") from err
