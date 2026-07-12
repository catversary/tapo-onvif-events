"""Config flow for the Tapo ONVIF Events integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    DEFAULT_PORT,
    DOMAIN,
)
from .onvif_client import async_probe_camera

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class TapoOnvifConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tapo ONVIF Events."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: one config entry per camera."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await async_probe_camera(
                    user_input[CONF_HOST],
                    user_input[CONF_PORT],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                )
            except Exception as err:  # noqa: BLE001 - surface any connect failure
                _LOGGER.debug("ONVIF validation failed: %r", err)
                errors["base"] = "cannot_connect"
            else:
                # Prefer the camera serial as a stable unique id; fall back to
                # host:port so a serial-less camera still de-duplicates.
                unique_id = (
                    info.get("serial")
                    or f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = info.get("model") or user_input[CONF_HOST]
                return self.async_create_entry(
                    title=f"{title} ({user_input[CONF_HOST]})",
                    data={**user_input, "model": info.get("model", "Tapo ONVIF")},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )
