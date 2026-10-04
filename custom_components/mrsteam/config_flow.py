"""Config flow for MrSteam."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .api import MrSteamApi, MrSteamAuthError, MrSteamError
from .const import CONF_MODEL_NUMBER, DEFAULT_MODEL_NUMBER, DOMAIN

_LOGGER = logging.getLogger(__name__)


class MrSteamConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MrSteam."""

    VERSION = 1

    async def _validate(self, data: dict[str, Any]) -> tuple[dict[str, str], int]:
        errors: dict[str, str] = {}
        api = MrSteamApi(
            self.hass,
            data[CONF_EMAIL],
            data[CONF_PASSWORD],
            data.get(CONF_MODEL_NUMBER, DEFAULT_MODEL_NUMBER),
        )
        count = 0
        try:
            await api.async_login()
            count = len(await api.async_discover())
            if not count:
                errors["base"] = "no_devices"
        except MrSteamAuthError:
            errors["base"] = "invalid_auth"
        except MrSteamError:
            _LOGGER.exception("MrSteam connection failed")
            errors["base"] = "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error during MrSteam setup")
            errors["base"] = "unknown"
        return errors, count

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_EMAIL].strip().lower())
            self._abort_if_unique_id_configured()
            errors, _ = await self._validate(user_input)
            if not errors:
                return self.async_create_entry(
                    title=f"MrSteam ({user_input[CONF_EMAIL]})", data=user_input
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(
                        CONF_MODEL_NUMBER, default=DEFAULT_MODEL_NUMBER
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, CONF_PASSWORD: user_input[CONF_PASSWORD]}
            errors, _ = await self._validate(data)
            if not errors:
                return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
        )
