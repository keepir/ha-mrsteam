"""Data coordinator for MrSteam."""
from __future__ import annotations

from datetime import timedelta
import asyncio
import logging
import time
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import MrSteamApi, MrSteamAuthError, MrSteamError, ReadsUnavailable, ShadowListener
from .const import (
    DOMAIN,
    PENDING_SECONDS,
    POLL_IDLE_HTTPS,
        POLL_RUNNING_HTTPS,
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


def raw_to_celsius(value: Any, hex_string: bool) -> float | None:
    """MrSteam temperature raw -> °C.  raw = °C*18 = (°F-32)*10.

    deviceRoomTemp is a hex string ("023A"); deviceSteamTemp is decimal and
    arrives as either an int (780) or a decimal string ("780").
    """
    try:
        raw = int(str(value), 16 if hex_string else 10)
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
        self.reads_available = True
        self.push_active = False
        self.listener: ShadowListener | None = None

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        data: dict[str, dict[str, Any]] = {}
        try:
            for thing in self.things:
                data[thing] = await self.api.async_get_shadow(thing)
        except ReadsUnavailable:
            # No HTTPS reads: stop polling. Live push (if allowed) keeps state.
            self.reads_available = self.push_active
            self.update_interval = None
            return {
                thing: (self.data or {}).get(thing) or {"desired": {}, "reported": {}}
                for thing in self.things
            }
        except MrSteamAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MrSteamError as err:
            raise UpdateFailed(str(err)) from err

        self.reads_available = True
        running = any(
            d.get("reported", {}).get("devices", {}).get("deviceSteamStatus") == "0001"
            for d in data.values()
        )
        secs = POLL_RUNNING_HTTPS if running else POLL_IDLE_HTTPS
        self.update_interval = timedelta(seconds=secs)
        return data

    # ── push updates ────────────────────────────────────────────────────────

    def start_listener(self) -> None:
        self.listener = ShadowListener(
            self.api, list(self.things), self._push_from_thread
        )
        self.listener.start()

    def stop_listener(self) -> None:
        if self.listener:
            self.listener.stop()

    def _push_from_thread(self, topic: str, payload: dict) -> None:
        self.hass.loop.call_soon_threadsafe(self._handle_push, topic, payload)

    @staticmethod
    def _merge(base: dict, new: dict) -> dict:
        out = dict(base)
        for key, value in (new or {}).items():
            if value is None:
                out.pop(key, None)
            elif isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = MrSteamCoordinator._merge(out[key], value)
            else:
                out[key] = value
        return out

    @callback
    def _handle_push(self, topic: str, payload: dict) -> None:
        try:
            thing = topic.split("/")[2]
        except IndexError:
            return
        if thing not in self.things:
            return
        current = dict((self.data or {}).get(thing) or {"desired": {}, "reported": {}})
        if topic.endswith("/update/documents"):
            state = (payload.get("current") or {}).get("state") or {}
            current = {"desired": state.get("desired") or {}, "reported": state.get("reported") or {}}
        elif topic.endswith("/get/accepted"):
            state = payload.get("state") or {}
            current = {"desired": state.get("desired") or {}, "reported": state.get("reported") or {}}
        elif topic.endswith("/update/accepted"):
            state = payload.get("state") or {}
            current = {
                "desired": self._merge(current.get("desired") or {}, state.get("desired") or {}),
                "reported": self._merge(current.get("reported") or {}, state.get("reported") or {}),
            }
        else:
            return
        if not self.push_active:
            _LOGGER.info("MrSteam: live state received; leaving assumed-state mode")
        self.push_active = True
        self.reads_available = True
        data = dict(self.data or {})
        data[thing] = current
        self.async_set_updated_data(data)

    # ── accessors ───────────────────────────────────────────────────────────

    def reported(self, thing: str) -> dict[str, Any]:
        return (self.data or {}).get(thing, {}).get("reported", {}).get("devices", {})

    def desired(self, thing: str) -> dict[str, Any]:
        return (self.data or {}).get(thing, {}).get("desired", {})

    def programs(self, thing: str) -> list[dict[str, Any]]:
        return self.reported(thing).get("deviceProgramList") or []

    def steam_running(self, thing: str) -> bool:
        """Best knowledge of whether a session is running (reported or assumed)."""
        actual = self.reported(thing).get("deviceSteamStatus") == "0001"
        return bool(self.effective(thing, "steam", actual))

    def require_steam(self, thing: str) -> None:
        if not self.steam_running(thing):
            raise HomeAssistantError(
                "Start steam first: the controller only accepts this during a session"
            )

    # ── requested-state handling ────────────────────────────────────────────

    def set_pending(self, thing: str, key: str, value: Any) -> None:
        self._pending.setdefault(thing, {})[key] = (
            value,
            time.monotonic() + PENDING_SECONDS,
        )

    def is_pending(self, thing: str, key: str) -> bool:
        entry = self._pending.get(thing, {}).get(key)
        if entry and not self.reads_available:
            return False
        return bool(entry and time.monotonic() < entry[1])

    def effective(self, thing: str, key: str, actual: Any) -> Any:
        """Requested value until the device confirms it or 45 s pass."""
        entry = self._pending.get(thing, {}).get(key)
        if entry is None:
            return actual
        value, expires = entry
        if not self.reads_available:
            return value  # assumed state: last commanded value stands
        if actual == value or time.monotonic() >= expires:
            self._pending[thing].pop(key, None)
            return actual
        return value

    # ── commands ────────────────────────────────────────────────────────────

    async def async_command(
        self,
        thing: str,
        fragment: dict[str, Any],
        pending: dict[str, Any],
        nudge: dict[str, Any] | None = None,
    ) -> None:
        """Mark requested values, publish the desired fragment, refresh later.

        The controller only acts on values that CHANGE in the shadow, and the
        shadow keeps stale values (e.g. a session ended at the touchscreen
        leaves appSteamStatus: true). `nudge` is published first, ~1.5 s
        earlier, so the real value is always a change.
        """
        for key, value in pending.items():
            self.set_pending(thing, key, value)
        self.async_update_listeners()
        try:
            if nudge is not None:
                await self.api.async_update_desired(thing, nudge)
                await asyncio.sleep(1.5)
            await self.api.async_update_desired(thing, fragment)
        except MrSteamError as err:
            for key in pending:
                self._pending.get(thing, {}).pop(key, None)
            self.async_update_listeners()
            raise HomeAssistantError(f"MrSteam command failed: {err}") from err
        self._schedule_refreshes()

    @callback
    def _schedule_refreshes(self) -> None:
        if not self.reads_available:
            return
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
