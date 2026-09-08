"""Minimal aiohttp client for the UniFi Network Pro AV (EAV) endpoints.

Reverse-engineered API (no official routing API exists):
  POST {login}                                   -> TOKEN cookie + X-CSRF-Token header
  GET  {prefix}/v2/api/site/{site}/proav/video/matrix
  POST {prefix}/v2/api/site/{site}/proav/video/group        -> 201, server assigns id/channel
  PUT/DELETE {prefix}/v2/api/site/{site}/proav/video/group/{id}
  GET  {prefix}/api/s/{site}/stat/device-basic  (bridge names)
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from .const import LOGGER

TIMEOUT = aiohttp.ClientTimeout(total=10)


class UnifiEavError(Exception):
    """Base error; carries the HTTP status when there is one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class UnifiEavAuthError(UnifiEavError):
    """Login rejected or session cannot be re-established."""


class UnifiEavConnectionError(UnifiEavError):
    """Console unreachable or timed out."""


class UnifiEavApi:
    """Cookie/CSRF-aware client. Not thread safe; one instance per config entry."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        site: str = "default",
        unifi_os: bool = True,
    ) -> None:
        # host may carry a scheme (mock server on plain http); bare host -> https.
        self.base = host.rstrip("/") if "://" in host else f"https://{host}"
        self._session = session
        self._username = username
        self._password = password
        self._site = site
        self._login_path = "/api/auth/login" if unifi_os else "/api/login"
        self._prefix = "/proxy/network" if unifi_os else ""
        self._cookies: dict[str, str] = {}
        self._csrf: str | None = None

    @property
    def _av(self) -> str:
        return f"{self.base}{self._prefix}/v2/api/site/{self._site}/proav/video"

    async def login(self) -> None:
        """Authenticate; stores the session cookies and CSRF token."""
        self._cookies, self._csrf = {}, None
        try:
            async with self._session.post(
                f"{self.base}{self._login_path}",
                json={"username": self._username, "password": self._password},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise UnifiEavAuthError("Invalid credentials", resp.status)
                if resp.status >= 400:
                    raise UnifiEavError(f"Login failed: HTTP {resp.status}", resp.status)
                self._cookies = {k: m.value for k, m in resp.cookies.items()}
                self._csrf = (
                    resp.headers.get("X-CSRF-Token")
                    or resp.headers.get("X-Updated-CSRF-Token")
                    or self._cookies.get("csrf_token")  # legacy controller
                )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UnifiEavConnectionError(f"Cannot connect to {self.base}: {err}") from err
        if not self._cookies:
            raise UnifiEavAuthError("Login returned no session cookie")
        LOGGER.debug("Logged in to %s", self.base)

    async def _request(self, method: str, url: str, body: Any = None, retry: bool = True) -> Any:
        """Send an authenticated request; on 401 re-login once and retry."""
        if not self._cookies:
            await self.login()
        # HA's cookie jar drops cookies for bare-IP hosts, so forward them by hand.
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items())}
        if method != "GET" and self._csrf:
            headers["X-CSRF-Token"] = self._csrf
        try:
            async with self._session.request(
                method, url, json=body, headers=headers, timeout=TIMEOUT
            ) as resp:
                if updated := resp.headers.get("X-Updated-CSRF-Token"):
                    self._csrf = updated
                if resp.status == 401:
                    if not retry:
                        raise UnifiEavAuthError("Session rejected after re-login", 401)
                    LOGGER.debug("401 from %s, re-authenticating", url)
                    self._cookies = {}
                    return await self._request(method, url, body, retry=False)
                text = await resp.text()
                if resp.status >= 400:
                    raise UnifiEavError(f"{method} {url} -> HTTP {resp.status}: {text[:200]}", resp.status)
                return json.loads(text) if text else None
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UnifiEavConnectionError(f"Request to {url} failed: {err}") from err

    async def get_matrix(self) -> dict[str, Any]:
        """Return the Pro AV video matrix (bridges/hosts/clients/groups)."""
        return await self._request("GET", f"{self._av}/matrix")

    async def get_devices_basic(self) -> list[dict[str, Any]]:
        """Return basic device records (mac, name, model) for the site."""
        data = await self._request("GET", f"{self.base}{self._prefix}/api/s/{self._site}/stat/device-basic")
        return data.get("data", []) if isinstance(data, dict) else data or []

    async def create_group(self, group: dict[str, Any]) -> Any:
        """POST a new TX group; server assigns id and channel."""
        return await self._request("POST", f"{self._av}/group", group)

    async def update_group(self, group_id: str, group: dict[str, Any]) -> Any:
        """PUT a full group body."""
        return await self._request("PUT", f"{self._av}/group/{group_id}", group)

    async def delete_group(self, group_id: str) -> Any:
        """DELETE a group (used when its last RX leaves)."""
        return await self._request("DELETE", f"{self._av}/group/{group_id}")
