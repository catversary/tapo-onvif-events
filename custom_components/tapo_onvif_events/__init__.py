"""The Tapo ONVIF Events integration.

Creates clean, deduplicated detection binary_sensors from a Tapo camera's ONVIF
PullPoint stream — a phantom-free alternative to tapo_control's detection
sensors. Events-only; leave tapo_control in place for streams/PTZ/siren/config.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    DEFAULT_PORT,
    DOMAIN,
)
from .onvif_client import TapoOnvifClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR]

type TapoOnvifConfigEntry = ConfigEntry[TapoOnvifClient]


async def async_setup_entry(hass: HomeAssistant, entry: TapoOnvifConfigEntry) -> bool:
    """Set up Tapo ONVIF Events from a config entry."""
    client = TapoOnvifClient(
        hass,
        entry.entry_id,
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the background PullPoint loop; it is cancelled automatically when
    # the entry is unloaded (async_create_background_task ties it to the entry).
    entry.async_create_background_task(
        hass,
        client.async_run(),
        name=f"{DOMAIN}_{entry.entry_id}_onvif",
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: TapoOnvifConfigEntry) -> bool:
    """Unload a config entry."""
    client = entry.runtime_data
    await client.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
