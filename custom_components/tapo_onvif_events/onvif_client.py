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
    PULL_HARD_SLACK,
    PULL_TIMEOUT,
    RENEW_SECONDS,
    RETRY_SECONDS,
    STUCK_ON_TIMEOUT,
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
        # Monotonic time we last received a `true` for each key (updated on every
        # received `true`, including dedup'd ones) — drives the stuck-on watchdog.
        self._last_true: dict[str, float] = {}
        # Monotonic time each key last transitioned off->on — on-duration for the
        # diagnostic line below (and the future max-on ceiling). Cleared on ->off.
        self._on_since: dict[str, float] = {}
        # Throttle (per key) for the per-iteration on-status diagnostic log.
        self._diag_log: dict[str, float] = {}
        self.available = False
        self._stop = asyncio.Event()

    # -- state fan-out ---------------------------------------------------

    def _set_key(self, key: str, value: bool) -> None:
        """Dedup and push a single detection key on change."""
        if value:
            # Refresh liveness on every received `true`, even when deduped, so the
            # watchdog can tell "still actively detected" from "stuck / dropped off".
            self._last_true[key] = time.monotonic()
        if self.state.get(key) != value:
            self.state[key] = value
            if value:
                self._on_since[key] = time.monotonic()
            else:
                self._on_since.pop(key, None)
            _LOGGER.debug("[%s] %s -> %s", self._host, key, "ON" if value else "OFF")
            async_dispatcher_send(
                self.hass, signal_state(self.entry_id), key, value
            )

    def _check_stuck(self) -> None:
        """Force off any key held on past STUCK_ON_TIMEOUT with no fresh `true`.

        Self-heals a dropped-off latch (seen on the basic CellMotion detector,
        which occasionally emits an `on` without a matching `off`). A genuinely
        active subject keeps the camera flooding `true`s, so `_last_true` stays
        fresh and this never fires for it.
        """
        now = time.monotonic()
        for key, is_on in list(self.state.items()):
            if not is_on:
                continue
            last_true_age = now - self._last_true.get(key, 0.0)
            # DIAG (throttled): proves this watchdog is running while a key is on,
            # and whether `true`s keep arriving (flood: last_true ~0s) or not
            # (stale latch: last_true aging). Absence of these lines during a
            # known stuck-on = the pull loop is blocked (hung long-poll).
            if now - self._diag_log.get(key, 0.0) >= 10.0:
                self._diag_log[key] = now
                _LOGGER.debug(
                    "[%s] DIAG %s on %.0fs, last_true %.1fs ago (watchdog alive)",
                    self._host,
                    key,
                    now - self._on_since.get(key, now),
                    last_true_age,
                )
            if last_true_age > STUCK_ON_TIMEOUT:
                _LOGGER.warning(
                    "[%s] %s stuck on >%ss with no fresh event; forcing off",
                    self._host,
                    key,
                    STUCK_ON_TIMEOUT,
                )
                self._set_key(key, False)

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
            name = simple_item.Name
            raw = str(simple_item.Value)
            key = FIELD_MAP.get(name)
            # Log every field the camera sends — including dedup'd repeats and
            # unmapped fields — so the raw PullMessages delivery is visible.
            _LOGGER.debug(
                "[%s] rx %s=%s%s",
                self._host,
                name,
                raw,
                "" if key else " (unmapped)",
            )
            if not key:
                continue
            value = raw.lower() == "true"
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
                # Re-baseline on every (re)subscribe. The camera keeps no state
                # across a reconnect or its scheduled reboot, so any "on" we are
                # still holding is stale. Clear it BEFORE marking available, so
                # entities recover as unavailable -> off (a clean edge); a stale
                # unavailable -> on would be missed by HA `to: "on"` triggers. A
                # genuinely-active detection re-fires a clean off -> on on the
                # camera's next event.
                for _field, _key, _dc, _label in KEYS:
                    self._set_key(_key, False)
                self._set_available(True)
                fail_since = None
                _LOGGER.info("[%s] subscribed %s", self._host, cam.xaddrs[PP_NS])
                next_renew = time.monotonic() + RENEW_SECONDS
                while not self._stop.is_set():
                    budget = next_renew - time.monotonic()
                    req = pullpoint.create_type("PullMessages")
                    req.Timeout = _pull_timeout(budget)
                    req.MessageLimit = 100
                    # Force-abort a single poll that outlives the camera's own
                    # long-poll timeout by more than PULL_HARD_SLACK. A stalled
                    # Tapo can hold the connection open (alive, never completing)
                    # for hours, freezing this loop and the stuck-key watchdog
                    # with it. Aborting drops to the reconnect path below, whose
                    # resubscribe re-baselines every key to off (clears a stale
                    # latch within ~one retry) and restores event flow.
                    hard_timeout = req.Timeout.total_seconds() + PULL_HARD_SLACK
                    poll_start = time.monotonic()
                    try:
                        resp = await asyncio.wait_for(
                            pullpoint.PullMessages(req), timeout=hard_timeout
                        )
                    except asyncio.TimeoutError as err:
                        # Name the keys currently ON so a captured stall directly
                        # shows the causal chain: a stall while (e.g.) person is ON
                        # is the exact scenario that stranded the closing `false`
                        # overnight; the resubscribe below re-baselines it off.
                        on_keys = [k for k, is_on in self.state.items() if is_on]
                        raise RuntimeError(
                            f"PullMessages stalled >{hard_timeout:.0f}s "
                            "(camera held the long-poll open; keys on: "
                            f"{', '.join(on_keys) if on_keys else 'none'}); "
                            "forcing reconnect"
                        ) from err
                    messages = getattr(resp, "NotificationMessage", None) or []
                    _LOGGER.debug(
                        "[%s] PullMessages returned after %.1fs with %d message(s)",
                        self._host,
                        time.monotonic() - poll_start,
                        len(messages),
                    )
                    for message in messages:
                        self._handle_message(message)
                    # Runs each poll iteration (>= every long-poll timeout even
                    # when idle), so a stuck key self-clears within ~one timeout
                    # of STUCK_ON_TIMEOUT.
                    self._check_stuck()
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
        """Best-effort clean teardown of a subscription + connection.

        Guarded with short timeouts: right after a stalled poll the same wedged
        connection would otherwise hang here too.
        """
        if sub_mgr is not None:
            try:
                await asyncio.wait_for(sub_mgr.Unsubscribe(), timeout=5)
            except Exception:  # noqa: BLE001
                pass
        if cam is not None:
            try:
                await asyncio.wait_for(cam.close(), timeout=5)
            except Exception:  # noqa: BLE001
                pass


def _pull_timeout(budget: float):
    """Clamp the PullMessages long-poll timeout to fit before the next renew."""
    from datetime import timedelta

    seconds = int(budget) if budget > 0 else 1
    return timedelta(seconds=max(1, min(PULL_TIMEOUT, seconds)))
