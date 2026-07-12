"""ONVIF PullPoint client for a single Tapo camera.

Holds one PullPoint subscription, long-polls for events, deduplicates them
(only reports a key on a true<->false change), and pushes state into Home
Assistant via the dispatcher. Ported from the standalone onvif-bridge service
(MQTT replaced with dispatcher signals; mechanics kept verbatim).
"""

from __future__ import annotations

import asyncio
import logging
import time

from onvif import ONVIFCamera

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    FIELD_MAP,
    KEYS,
    OFFLINE_GRACE,
    PP_NS,
    PULL_TIMEOUT,
    RENEW_SECONDS,
    RETRY_SECONDS,
    SUB_LIFETIME,
    signal_availability,
    signal_state,
)

_LOGGER = logging.getLogger(__name__)


async def async_probe_camera(
    host: str, port: int, user: str, password: str
) -> dict[str, str]:
    """Connect and read device information. Used by the config flow to validate.

    Returns a dict with serial/model/manufacturer/firmware. Raises on failure.
    """
    cam = ONVIFCamera(host, port, user, password)
    try:
        await cam.update_xaddrs()
        devicemgmt = await cam.create_devicemgmt_service()
        info = await devicemgmt.GetDeviceInformation()
        return {
            "manufacturer": getattr(info, "Manufacturer", "") or "Tapo",
            "model": getattr(info, "Model", "") or "Tapo ONVIF",
            "firmware": getattr(info, "FirmwareVersion", "") or "",
            "serial": getattr(info, "SerialNumber", "") or "",
        }
    finally:
        try:
            await cam.close()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


class TapoOnvifClient:
    """Background PullPoint subscriber for one camera, pushing to the dispatcher."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        host: str,
        port: int,
        user: str,
        password: str,
    ) -> None:
        """Initialise the client."""
        self.hass = hass
        self.entry_id = entry_id
        self._host = host
        self._port = port
        self._user = user
        self._password = password

        # Baseline: every key OFF until the camera says otherwise.
        self.state: dict[str, bool] = {key: False for (_f, key, _dc, _l) in KEYS}
        self.available = False
        self._stop = asyncio.Event()

    # -- state fan-out ---------------------------------------------------

    def _set_key(self, key: str, value: bool) -> None:
        """Dedup and push a single detection key on change."""
        if self.state.get(key) != value:
            self.state[key] = value
            _LOGGER.debug("[%s] %s -> %s", self._host, key, "ON" if value else "OFF")
            async_dispatcher_send(
                self.hass, signal_state(self.entry_id), key, value
            )

    def _set_available(self, value: bool) -> None:
        """Push availability on change."""
        if self.available != value:
            self.available = value
            async_dispatcher_send(self.hass, signal_availability(self.entry_id))

    def _handle_message(self, message) -> None:
        """Parse one ONVIF NotificationMessage and apply its SimpleItems."""
        try:
            element = message.Message._value_1
        except Exception:  # noqa: BLE001 - malformed message, skip
            return
        data = getattr(element, "Data", None)
        if data is None:
            return
        for simple_item in getattr(data, "SimpleItem", None) or []:
            key = FIELD_MAP.get(simple_item.Name)
            if not key:
                continue
            value = str(simple_item.Value).lower() == "true"
            self._set_key(key, value)

    # -- lifecycle -------------------------------------------------------

    async def async_run(self) -> None:
        """Main reconnect loop. Runs until stop() is set."""
        fail_since: float | None = None
        while not self._stop.is_set():
            cam: ONVIFCamera | None = None
            sub_mgr = None
            try:
                cam = ONVIFCamera(self._host, self._port, self._user, self._password)
                await cam.update_xaddrs()
                events = await cam.create_events_service()
                sub = await events.CreatePullPointSubscription(
                    {"InitialTerminationTime": cam.get_next_termination_time(SUB_LIFETIME)}
                )
                cam.xaddrs[PP_NS] = sub.SubscriptionReference.Address._value_1
                # Subscription manager (Renew / Unsubscribe) + pull service.
                sub_mgr = await cam.create_subscription_service("PullPointSubscription")
                pullpoint = await cam.create_pullpoint_service()
                self._set_available(True)
                fail_since = None
                _LOGGER.info("[%s] subscribed %s", self._host, cam.xaddrs[PP_NS])
                next_renew = time.monotonic() + RENEW_SECONDS
                while not self._stop.is_set():
                    budget = next_renew - time.monotonic()
                    req = pullpoint.create_type("PullMessages")
                    req.Timeout = _pull_timeout(budget)
                    req.MessageLimit = 100
                    resp = await pullpoint.PullMessages(req)
                    for message in getattr(resp, "NotificationMessage", None) or []:
                        self._handle_message(message)
                    if time.monotonic() >= next_renew:
                        await sub_mgr.Renew(cam.get_next_termination_time(SUB_LIFETIME))
                        next_renew = time.monotonic() + RENEW_SECONDS
                        _LOGGER.debug("[%s] subscription renewed", self._host)
            except asyncio.CancelledError:
                await self._teardown(cam, sub_mgr)
                raise
            except Exception as err:  # noqa: BLE001 - reconnect on any ONVIF error
                if fail_since is None:
                    fail_since = time.monotonic()
                if time.monotonic() - fail_since >= OFFLINE_GRACE:
                    self._set_available(False)
                _LOGGER.warning(
                    "[%s] loop error: %r; retry in %ss",
                    self._host,
                    err,
                    RETRY_SECONDS,
                )
                await self._teardown(cam, sub_mgr)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=RETRY_SECONDS)
                except asyncio.TimeoutError:
                    pass

    async def async_stop(self) -> None:
        """Signal the loop to stop (teardown happens inside the loop task)."""
        self._stop.set()

    async def _teardown(self, cam, sub_mgr) -> None:
        """Best-effort clean teardown of a subscription + connection."""
        if sub_mgr is not None:
            try:
                await sub_mgr.Unsubscribe()
            except Exception:  # noqa: BLE001
                pass
        if cam is not None:
            try:
                await cam.close()
            except Exception:  # noqa: BLE001
                pass


def _pull_timeout(budget: float):
    """Clamp the PullMessages long-poll timeout to fit before the next renew."""
    from datetime import timedelta

    seconds = int(budget) if budget > 0 else 1
    return timedelta(seconds=max(1, min(PULL_TIMEOUT, seconds)))
