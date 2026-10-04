"""Steam duration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature, UnitOfTime
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
    entities: list[NumberEntity] = []
    for t in coordinator.things:
        entities += [SteamDuration(coordinator, t), SteamTargetTemp(coordinator, t)]
    async_add_entities(entities)


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
        # (Oct 4 2026), so the time always travels with appSteamStatus: true,
        # and only during a session.
        self.coordinator.require_steam(self.thing)
        minutes = int(round(value))
        nudge_minutes = minutes + 1 if minutes < self.native_max_value else minutes - 1
        await self.coordinator.async_command(
            self.thing,
            {"steam": {"appSteamStatus": True, "appSteamTime": format(minutes, "04X")}},
            {"duration": minutes},
            nudge={
                "steam": {
                    "appSteamStatus": True,
                    "appSteamTime": format(nudge_minutes, "04X"),
                }
            },
        )


class SteamTargetTemp(MrSteamEntity, NumberEntity):
    """appSteamTemp, raw = (°F - 32) * 10. Confirmed Oct 4 2026 (106 °F -> 740).

    The unit resets to 110 °F at every start, so this is only sent during a
    session. Reported value: deviceSteamTemp (decimal, same encoding).
    """

    _attr_translation_key = "steam_target_temperature"
    _attr_icon = "mdi:thermometer"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.FAHRENHEIT
    _attr_native_min_value = 95
    _attr_native_max_value = 120
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, thing):
        super().__init__(coordinator, thing, "steam_target_temperature")

    @property
    def native_value(self) -> float | None:
        raw = self.reported.get("deviceSteamTemp")
        try:
            actual = round(int(str(raw), 10) / 10 + 32)
        except (TypeError, ValueError):
            actual = None
        return self.coordinator.effective(self.thing, "target_temp", actual)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"requested": self.coordinator.is_pending(self.thing, "target_temp")}

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.require_steam(self.thing)
        temp_f = int(round(value))
        nudge_f = temp_f + 1 if temp_f < self.native_max_value else temp_f - 1
        await self.coordinator.async_command(
            self.thing,
            {"steam": {"appSteamStatus": True, "appSteamTemp": (temp_f - 32) * 10}},
            {"target_temp": temp_f},
            nudge={"steam": {"appSteamStatus": True, "appSteamTemp": (nudge_f - 32) * 10}},
        )
