"""Binary sensors for Tapo ONVIF detection events."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TapoOnvifConfigEntry
from .const import DOMAIN, KEYS, signal_availability, signal_state
from .onvif_client import TapoOnvifClient


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TapoOnvifConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the six detection binary_sensors for this camera."""
    client = entry.runtime_data
    async_add_entities(
        TapoOnvifBinarySensor(client, entry, key, device_class, label)
        for (_field, key, device_class, label) in KEYS
    )


class TapoOnvifBinarySensor(BinarySensorEntity):
    """A single ONVIF detection sensor, fed by the dispatcher."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        client: TapoOnvifClient,
        entry: TapoOnvifConfigEntry,
        key: str,
        device_class: str,
        label: str,
    ) -> None:
        """Initialise the sensor."""
        self._client = client
        self._key = key
        self._attr_name = label
        self._attr_device_class = BinarySensorDeviceClass(device_class)
        self._attr_unique_id = f"{entry.unique_id or entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Tapo (ONVIF)",
            model=entry.data.get("model", "Tapo ONVIF"),
        )

    @property
    def is_on(self) -> bool:
        """Return the current detection state."""
        return self._client.state.get(self._key, False)

    @property
    def available(self) -> bool:
        """Return whether the camera subscription is currently live."""
        return self._client.available

    async def async_added_to_hass(self) -> None:
        """Subscribe to state and availability updates for this camera."""
        entry_id = self._client.entry_id

        @callback
        def _state_update(key: str, _value: bool) -> None:
            if key == self._key:
                self.async_write_ha_state()

        @callback
        def _avail_update() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, signal_state(entry_id), _state_update)
        )
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_availability(entry_id), _avail_update
            )
        )
