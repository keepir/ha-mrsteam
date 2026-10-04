"""Data coordinator for MrSteam."""
from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MrSteamApi, MrSteamAuthError, MrSteamError
from .const import (
    DOMAIN,
    PENDING_SECONDS,
    POLL_IDLE_HTTPS,
    POLL_IDLE_MQTT,
    POLL_RUNNING_HTTPS,
    POLL_RUNNING_MQTT,
    REFRESH_DELAYS,
)

_LOGGER = logging.getLogger(__name__)


# ── decoding helpers ────────────────────────────────────────────────────────


def hex_minutes(value: Any) -> int | None:
    """'001E' -> 30."""
    try:
        return int(str(value), 16)
    except (TypeError, ValueError):
        return None


def raw_to_celsius(value: Any) -> float | None:
    """MrSteam temperature raw -> °C.  raw = °C*18 = (°F-32)*10."""
    try:
        raw = int(str(value), 16) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None
    return round(raw / 18, 3)  # keep precision so °F display is exact


class MrSteamCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls every discovered thing; data = {thingName: {'desired', 'reported'}}."""

    def __init__(
        self, hass: HomeAssistant, api: MrSteamApi, things: list[dict[str, Any]]
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_IDLE_HTTPS),
        )
        self.api = api
        self.things = {t["thingName"]: t for t in things}
        # {thing: {key: (value, expires_monotonic)}}
        self._pending: dict[str, dict[str, tuple[Any, float]]] = {}
        self._refresh_unsubs: list = []

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = {}
        try:
            for thing in self.things:
                data[thing] = await self.api.async_get_shadow(thing)
        except MrSteamAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MrSteamError as err:
            raise UpdateFailed(str(err)) from err

        running = any(
            d.get("reported", {}).get("devices", {}).get("deviceSteamStatus") == "0001"
            for d in data.values()
        )
        if self.api.https_read_ok is False:
            secs = POLL_RUNNING_MQTT if running else POLL_IDLE_MQTT
        else:
            secs = POLL_RUNNING_HTTPS if running else POLL_IDLE_HTTPS
        self.update_interval = timedelta(seconds=secs)
        return data

    # ── accessors ───────────────────────────────────────────────────────────

    def reported(self, thing: str) -> dict[str, Any]:
        return (self.data or {}).get(thing, {}).get("reported", {}).get("devices", {})

    def desired(self, thing: str) -> dict[str, Any]:
        return (self.data or {}).get(thing, {}).get("desired", {})

    def programs(self, thing: str) -> list[dict[str, Any]]:
        return self.reported(thing).get("deviceProgramList") or []

    # ── requested-state handling ────────────────────────────────────────────

    def set_pending(self, thing: str, key: str, value: Any) -> None:
        self._pending.setdefault(thing, {})[key] = (
            value,
            time.monotonic() + PENDING_SECONDS,
        )

    def is_pending(self, thing: str, key: str) -> bool:
        entry = self._pending.get(thing, {}).get(key)
        return bool(entry and time.monotonic() < entry[1])

    def effective(self, thing: str, key: str, actual: Any) -> Any:
        """Requested value until the device confirms it or 45 s pass."""
        entry = self._pending.get(thing, {}).get(key)
        if entry is None:
            return actual
        value, expires = entry
        if actual == value or time.monotonic() >= expires:
            self._pending[thing].pop(key, None)
            return actual
        return value

    # ── commands ────────────────────────────────────────────────────────────

    async def async_command(
        self, thing: str, fragment: dict[str, Any], pending: dict[str, Any]
    ) -> None:
        """Mark requested values, publish the desired fragment, refresh later."""
        for key, value in pending.items():
            self.set_pending(thing, key, value)
        self.async_update_listeners()
        try:
            await self.api.async_update_desired(thing, fragment)
        except MrSteamError as err:
            for key in pending:
                self._pending.get(thing, {}).pop(key, None)
            self.async_update_listeners()
            raise HomeAssistantError(f"MrSteam command failed: {err}") from err
        self._schedule_refreshes()

    @callback
    def _schedule_refreshes(self) -> None:
        for unsub in self._refresh_unsubs:
            unsub()
        self._refresh_unsubs = [
            async_call_later(self.hass, delay, self._delayed_refresh)
            for delay in REFRESH_DELAYS
        ]

    async def _delayed_refresh(self, _now) -> None:
        await self.async_request_refresh()

    @callback
    def async_cancel_refreshes(self) -> None:
        for unsub in self._refresh_unsubs:
            unsub()
        self._refresh_unsubs = []
