"""Chroma light."""
from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    LIGHT_OFF,
    LIGHT_RGB,
    LIGHT_WHITE,
    MS_BRIGHT_MAX,
    MS_BRIGHT_MIN,
)
from .coordinator import MrSteamCoordinator
from .entity import MrSteamEntity


def ms_to_ha(value: int) -> int:
    # HA treats brightness 0 as off, so MrSteam 1 maps to HA 1 rather than 0
    return max(1, round((value - MS_BRIGHT_MIN) * 255 / (MS_BRIGHT_MAX - MS_BRIGHT_MIN)))


def ha_to_ms(value: int) -> int:
    ms = round(MS_BRIGHT_MIN + value * (MS_BRIGHT_MAX - MS_BRIGHT_MIN) / 255)
    return max(MS_BRIGHT_MIN, min(MS_BRIGHT_MAX, ms))


def hex_to_rgb(color: str | None) -> tuple[int, int, int] | None:
    try:
        c = str(color).lstrip("#")
        return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16))
    except (ValueError, IndexError):
        return None


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MrSteamCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ChromaLight(coordinator, t)
        for t in coordinator.things
        if coordinator.reported(t).get("deviceChromaConnected") is not False
    )


class ChromaLight(MrSteamEntity, LightEntity):
    """Off = type 0, default white = type 4 #FFFFFF, custom = type 1 #RRGGBB."""

    _attr_translation_key = "chroma"
    _attr_supported_color_modes = {ColorMode.RGB}
    _attr_color_mode = ColorMode.RGB

    def __init__(self, coordinator, thing):
        super().__init__(coordinator, thing, "chroma")

    @property
    def is_on(self) -> bool:
        actual = bool(self.reported.get("deviceLightStatus"))
        return self.coordinator.effective(self.thing, "light_on", actual)

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        actual = hex_to_rgb(self.desired.get("light", {}).get("color"))
        return self.coordinator.effective(self.thing, "light_rgb", actual)

    @property
    def brightness(self) -> int | None:
        raw = self.desired.get("lightBright", {}).get("data")
        actual = ms_to_ha(int(raw)) if raw is not None else None
        return self.coordinator.effective(self.thing, "light_bri", actual)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "requested": self.coordinator.is_pending(self.thing, "light_on"),
            "mode": {LIGHT_OFF: "off", LIGHT_RGB: "color", LIGHT_WHITE: "white"}.get(
                self.desired.get("light", {}).get("type")
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        fragment: dict[str, Any] = {}
        pending: dict[str, Any] = {"light_on": True}
        rgb = kwargs.get(ATTR_RGB_COLOR)
        bri = kwargs.get(ATTR_BRIGHTNESS)
        if rgb is not None:
            color = "#{:02X}{:02X}{:02X}".format(*rgb)
            fragment["light"] = {"type": LIGHT_RGB, "color": color}
            pending["light_rgb"] = tuple(rgb)
        elif not self.is_on:
            fragment["light"] = {"type": LIGHT_WHITE, "color": "#FFFFFF"}
            pending["light_rgb"] = (255, 255, 255)
        if bri is not None:
            ms = ha_to_ms(bri)
            fragment["lightBright"] = {"data": ms}
            pending["light_bri"] = ms_to_ha(ms)
        if not fragment:
            return
        await self.coordinator.async_command(self.thing, fragment, pending)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_command(
            self.thing, {"light": {"type": LIGHT_OFF}}, {"light_on": False}
        )
