"""Steam and Aroma switches."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MrSteamCoordinator, hex_minutes
from .entity import MrSteamEntity

_LOGGER = logging.getLogger(__name__)

# TEST-BUILD FALLBACK ONLY (v0.1.x): used when deviceProgramList can't be read.
# This is the captured Default program for the user's unit; v0.2 reads it live.
FALLBACK_PROGRAM = {
    "add_time": "2026-07-20 18:52:12",
    "id": 3,
    "profiles_id": 2,
    "program_name": "Default",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MrSteamCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []
    for thing in coordinator.things:
        entities += [SteamSwitch(coordinator, thing), AromaSwitch(coordinator, thing)]
    async_add_entities(entities)


class SteamSwitch(MrSteamEntity, SwitchEntity):
    """Steam on/off."""

    _attr_translation_key = "steam"
    _attr_icon = "mdi:weather-fog"

    def __init__(self, coordinator, thing):
        super().__init__(coordinator, thing, "steam")

    @property
    def is_on(self) -> bool:
        actual = self.reported.get("deviceSteamStatus") == "0001"
        return self.coordinator.effective(self.thing, "steam", actual)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rep = self.reported
        return {
            "requested": self.coordinator.is_pending(self.thing, "steam"),
            "remaining_minutes": hex_minutes(rep.get("deviceSteamRemainTime")),
            "steam_alert": rep.get("deviceSteamAlert"),
            "program": (self._program() or {}).get("program_name"),
        }

    def _program(self) -> dict | None:
        programs = self.coordinator.programs(self.thing)
        for prog in programs:
            if str(prog.get("program_name", "")).lower() == "default":
                return prog
        return programs[0] if programs else FALLBACK_PROGRAM

    async def async_turn_on(self, **kwargs: Any) -> None:
        program = self._program()
        if self.coordinator.reads_available and self.reported.get(
            "deviceSteamStatus"
        ) == "0001":
            return  # already running (only knowable with reads)
        await self.coordinator.async_command(
            self.thing,
            {"steam": {"appSteamStatus": True, "appProgram": program}},
            {"steam": True},
            nudge={"steam": {"appSteamStatus": False}},
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.thing, {"steam": {"appSteamStatus": False}}, {"steam": False}
        )


class AromaSwitch(MrSteamEntity, SwitchEntity):
    """AromaSteam on/off."""

    _attr_translation_key = "aroma"
    _attr_icon = "mdi:flower-tulip-outline"

    def __init__(self, coordinator, thing):
        super().__init__(coordinator, thing, "aroma")

    @property
    def is_on(self) -> bool:
        actual = str(self.reported.get("deviceAromaStatus")) == "1"
        return self.coordinator.effective(self.thing, "aroma", actual)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"requested": self.coordinator.is_pending(self.thing, "aroma")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.coordinator.require_steam(self.thing)
        await self.coordinator.async_command(
            self.thing,
            {"aroma": {"open": True}},
            {"aroma": True},
            nudge={"aroma": {"open": False}},
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.thing, {"aroma": {"open": False}}, {"aroma": False}
        )
