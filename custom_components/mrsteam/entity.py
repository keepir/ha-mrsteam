"""Base entity for MrSteam."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MrSteamCoordinator


class MrSteamEntity(CoordinatorEntity[MrSteamCoordinator]):
    """Common device info / helpers."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MrSteamCoordinator, thing: str, key: str) -> None:
        super().__init__(coordinator)
        self.thing = thing
        self._attr_unique_id = f"{thing}_{key}"
        meta = coordinator.things.get(thing, {})
        rep = coordinator.reported(thing)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, thing)},
            name=meta.get("device_custom_name") or "MrSteam",
            manufacturer="MrSteam",
            model=f"iSteamX ({coordinator.api.model_number})",
            sw_version=rep.get("deviceHubVersion"),
        )

    @property
    def reported(self) -> dict:
        return self.coordinator.reported(self.thing)

    @property
    def desired(self) -> dict:
        return self.coordinator.desired(self.thing)

    @property
    def assumed_state(self) -> bool:
        return not self.coordinator.reads_available

    @property
    def available(self) -> bool:
        return super().available and self.thing in (self.coordinator.data or {})
