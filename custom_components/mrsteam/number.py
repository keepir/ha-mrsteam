"""Steam duration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
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
        minutes = int(round(value))
        await self.coordinator.async_command(
            self.thing,
            {"steam": {"appSteamTime": format(minutes, "04X")}},
            {"duration": minutes},
        )
