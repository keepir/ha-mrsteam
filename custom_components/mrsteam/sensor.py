"""Remaining time, room temperature, steam head temperature."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MrSteamCoordinator, hex_minutes, raw_to_celsius
from .entity import MrSteamEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MrSteamCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for thing in coordinator.things:
        entities += [
            RemainingTime(coordinator, thing),
            RoomTemp(coordinator, thing),
            SteamHeadTemp(coordinator, thing),
        ]
    async_add_entities(entities)


class RemainingTime(MrSteamEntity, SensorEntity):
    _attr_translation_key = "remaining_time"
    _attr_icon = "mdi:timer-sand"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator, thing):
        super().__init__(coordinator, thing, "remaining_time")

    @property
    def native_value(self) -> int | None:
        return hex_minutes(self.reported.get("deviceSteamRemainTime"))


class RoomTemp(MrSteamEntity, SensorEntity):
    """deviceRoomTemp: hex, raw/18 = °C (inferred, validate vs touchscreen)."""

    _attr_translation_key = "room_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator, thing):
        super().__init__(coordinator, thing, "room_temperature")

    @property
    def native_value(self) -> float | None:
        return raw_to_celsius(self.reported.get("deviceRoomTemp"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"raw": self.reported.get("deviceRoomTemp")}


class SteamHeadTemp(MrSteamEntity, SensorEntity):
    """deviceSteamTemp — diagnostic only, NOT the target setpoint."""

    _attr_translation_key = "steam_head_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, thing):
        super().__init__(coordinator, thing, "steam_head_temperature")

    @property
    def native_value(self) -> float | None:
        return raw_to_celsius(self.reported.get("deviceSteamTemp"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"raw": self.reported.get("deviceSteamTemp")}
