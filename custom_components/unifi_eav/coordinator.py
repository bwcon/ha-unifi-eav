"""Polling coordinator, matrix model, and the pure routing planner."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import UnifiEavApi, UnifiEavAuthError, UnifiEavError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, LOGGER

Op = tuple[str, str | None, dict[str, Any] | None]  # (method, group_id, body)


@dataclass
class MatrixData:
    """Snapshot of the Pro AV video matrix."""

    tx: list[str]  # host (transmitter) MACs
    rx: list[str]  # client (receiver) MACs
    groups: list[dict[str, Any]]
    names: dict[str, str] = field(default_factory=dict)

    def name(self, mac: str) -> str:
        return self.names.get(mac) or mac

    def group_for_tx(self, tx: str) -> dict[str, Any] | None:
        return next((g for g in self.groups if g.get("host", {}).get("mac") == tx), None)

    def route_of(self, rx: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """Return (group, client-entry) holding this RX, or None if unrouted."""
        for group in self.groups:
            for client in group.get("clients", []):
                if client.get("mac") == rx:
                    return group, client
        return None


def _mode(client_count: int) -> str:
    return "MULTICAST" if client_count >= 2 else "UNICAST"


def _new_client(mac: str) -> dict[str, Any]:
    return {
        "mac": mac,
        "client_type": "VIDEO",
        "mode": "UNICAST",
        "resolution": "AUTO",
        "color_format": "RGB",
        "audio_enabled": True,
        "audio_mode": "PCM",
        "enabled": True,
    }


def _with_clients(group: dict[str, Any], clients: list[dict[str, Any]]) -> dict[str, Any]:
    """Echo the group as read, replacing only clients (mode recomputed).

    # verify on hardware: strip id/channel if the PUT rejects them; force
    # MULTICAST everywhere if mixed modes are rejected.
    """
    mode = _mode(len(clients))
    return {**group, "clients": [{**c, "mode": mode} for c in clients]}


def plan_route(data: MatrixData, rx: str, tx: str, tx_name: str) -> list[Op]:
    """Return the ordered API operations that route RX to TX.

    Leave the old group first (PUT shorter list, or DELETE when RX was its
    only client), then join the TX group (PUT) or create it (POST).
    """
    current = data.route_of(rx)
    target = data.group_for_tx(tx)
    if current and current[0] is target:
        return []
    ops: list[Op] = []
    if current:
        old, client = current
        remaining = [c for c in old["clients"] if c["mac"] != rx]
        if remaining:
            ops.append(("PUT", old["id"], _with_clients(old, remaining)))
        else:
            ops.append(("DELETE", old["id"], None))
    else:
        client = _new_client(rx)
    if target:
        ops.append(("PUT", target["id"], _with_clients(target, [*target["clients"], client])))
    else:
        body = {
            "name": tx_name,
            "type": "VIDEO",
            "host": {"mac": tx, "host_type": "VIDEO", "refresh_rate": "AUTO", "enabled": True},
            "clients": [{**client, "mode": "UNICAST"}],
        }
        ops.append(("POST", None, body))
    return ops


def _macs(items: list[Any]) -> list[str]:
    # verify on hardware: /matrix lists are assumed to be MAC strings; tolerate {"mac": ...}.
    return [i if isinstance(i, str) else i["mac"] for i in items]


class UnifiEavCoordinator(DataUpdateCoordinator[MatrixData]):
    """Poll /matrix and serialize writes."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, api: UnifiEavApi) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.api = api
        self._devices: dict[str, dict[str, Any]] = {}  # mac -> device-basic record
        self._seen: set[str] = set()  # MACs already looked up (named or not)
        self._lock = asyncio.Lock()

    async def _async_update_data(self) -> MatrixData:
        try:
            raw = await self.api.get_matrix()
            tx, rx = _macs(raw.get("hosts", [])), _macs(raw.get("clients", []))
            if unknown := set(tx + rx) - self._seen:
                await self._fetch_names(unknown)
        except UnifiEavAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except UnifiEavError as err:
            raise UpdateFailed(str(err)) from err
        names = {mac: d["name"] for mac, d in self._devices.items() if d.get("name")}
        return MatrixData(tx=tx, rx=rx, groups=raw.get("groups", []), names=names)

    async def _fetch_names(self, macs: set[str]) -> None:
        """Refresh bridge names; failure is tolerated (entities fall back to MAC)."""
        try:
            devices = await self.api.get_devices_basic()
        except UnifiEavAuthError:
            raise
        except UnifiEavError as err:
            LOGGER.debug("device-basic unavailable, using MACs as names: %s", err)
            return
        self._devices = {d["mac"]: d for d in devices if "mac" in d}
        self._seen |= macs

    def hub_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.config_entry.entry_id)},
            name=self.config_entry.title,
            manufacturer="Ubiquiti",
            model="UniFi Network (Pro AV)",
            configuration_url=self.api.base,
        )

    def device_info(self, mac: str) -> DeviceInfo:
        dev = self._devices.get(mac, {})
        return DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(dr.CONNECTION_NETWORK_MAC, mac)},
            name=dev.get("name") or mac,
            manufacturer="Ubiquiti",
            model=dev.get("model") or "EAV-Bridge",
            via_device=(DOMAIN, self.config_entry.entry_id),
        )

    async def _apply(self, ops: list[Op]) -> None:
        for method, group_id, body in ops:
            if method == "POST":
                await self.api.create_group(body)
            elif method == "PUT":
                await self.api.update_group(group_id, body)
            else:
                await self.api.delete_group(group_id)
        await self.async_refresh()

    async def route(self, rx: str, tx: str) -> None:
        """Route RX to TX (no-op if already routed there)."""
        async with self._lock:
            if ops := plan_route(self.data, rx, tx, self.data.name(tx)):
                await self._apply(ops)

    async def set_enabled(self, rx: str, enabled: bool) -> None:
        """Flip the RX client's ``enabled`` flag inside its current group."""
        async with self._lock:
            if (route := self.data.route_of(rx)) is None:
                return
            group, _ = route
            clients = [{**c, "enabled": enabled} if c["mac"] == rx else c for c in group["clients"]]
            await self._apply([("PUT", group["id"], {**group, "clients": clients})])


type UnifiEavConfigEntry = ConfigEntry[UnifiEavCoordinator]
