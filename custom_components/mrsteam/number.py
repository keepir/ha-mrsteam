"""Steam duration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MrSteamCoordinator, hex_minutes
from .entity import MrSteamEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MrSteamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(SteamDuration(coordinator, t) for t in coordinator.things)


class SteamDuration(MrSteamEntity, NumberEntity):
    """Session length in minutes (sent as 4-digit hex)."""

    _attr_translation_key = "steam_duration"
    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 5
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, thing):
        super().__init__(coordinator, thing, "steam_duration")

    @property
    def native_value(self) -> int | None:
        actual = hex_minutes(self.reported.get("deviceSteamTime"))
        return self.coordinator.effective(self.thing, "duration", actual)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"requested": self.coordinator.is_pending(self.thing, "duration")}

    async def async_set_native_value(self, value: float) -> None:
        # The controller reads a steam block without appSteamStatus as "off"
        # (Oct 4 2026: a time-only update stopped a running session 3 of 3
        # times), so the time always travels with appSteamStatus: true, and
        # only while steam is believed to be running.
        steam_on = self.coordinator.effective(
            self.thing,
            "steam",
            self.reported.get("deviceSteamStatus") == "0001",
        )
        if not steam_on:
            raise HomeAssistantError(
                "Start steam first: duration can only be changed during a session"
            )
        minutes = int(round(value))
        await self.coordinator.async_command(
            self.thing,
            {"steam": {"appSteamStatus": True, "appSteamTime": format(minutes, "04X")}},
            {"duration": minutes},
        )
