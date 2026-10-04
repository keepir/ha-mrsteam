"""MrSteam iSteamX integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .api import MrSteamApi, MrSteamAuthError, MrSteamError
from .const import CONF_MODEL_NUMBER, DEFAULT_MODEL_NUMBER, DOMAIN
from .coordinator import MrSteamCoordinator

PLATFORMS = [Platform.LIGHT, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

SERVICE_SEND_DESIRED = "send_desired"
SEND_DESIRED_SCHEMA = vol.Schema(
    {
        vol.Required("fragment"): dict,
        vol.Optional("thing_name"): cv.string,
    }
)


def _register_services(hass: HomeAssistant) -> None:
    """Test/diagnostic service: publish an arbitrary state.desired fragment."""
    if hass.services.has_service(DOMAIN, SERVICE_SEND_DESIRED):
        return

    async def _send_desired(call: ServiceCall) -> None:
        coordinators: list[MrSteamCoordinator] = list(hass.data.get(DOMAIN, {}).values())
        wanted = call.data.get("thing_name")
        for coordinator in coordinators:
            for thing in coordinator.things:
                if wanted in (None, thing):
                    await coordinator.async_command(thing, call.data["fragment"], {})
                    return
        raise HomeAssistantError(f"No MrSteam unit found ({wanted or 'any'})")

    hass.services.async_register(
        DOMAIN, SERVICE_SEND_DESIRED, _send_desired, schema=SEND_DESIRED_SCHEMA
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    api = MrSteamApi(
        hass,
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_MODEL_NUMBER, DEFAULT_MODEL_NUMBER),
    )
    try:
        things = await api.async_discover()
    except MrSteamAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except MrSteamError as err:
        raise ConfigEntryNotReady(str(err)) from err
    if not things:
        raise ConfigEntryNotReady("No MrSteam devices found on this account")

    coordinator = MrSteamCoordinator(hass, api, things)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    _register_services(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: MrSteamCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        coordinator.async_cancel_refreshes()
    return unloaded
