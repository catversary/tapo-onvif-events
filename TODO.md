# TODO / Roadmap

Pending work for **tapo-onvif-events**. Contributions welcome.

## Verification

- [ ] **Confirm clean recovery across a camera reboot.**
  Tapo cameras can be scheduled to auto-restart (e.g. a daily ~06:00–06:30
  window). Verify the integration handles a real reboot end-to-end:
  - the pull loop retries (~5s) while the camera is unreachable;
  - entities flip `unavailable` after the offline grace (30s);
  - on the camera's return the subscription is re-established (no leak — the
    camera drops its own subs on reboot);
  - each sensor recovers to **`off`** via the re-baseline, i.e. no stale
    `unavailable → on` (which HA `to: "on"` triggers would miss).

  The re-baseline landed in v0.1.1; this item is to confirm it against an
  actual reboot and note the real downtime.

## Ideas / potential options (not yet built)

- [ ] **Optional "hold" / off-delay for detections.**
  The camera's smart detectors (person/vehicle/pet) are *motion-driven*: they
  emit `false` when the subject stops moving even if still in frame, then
  re-fire `true` when movement resumes. For use cases that want a sensor to
  stay `on` while a subject lingers, add an **optional per-entry off-delay
  (debounce)**: hold a key `on` for N seconds after the last received `true`,
  resetting the timer on each new `true`, and only emit `off` once the quiet
  period elapses.
  - Surface as a config-flow / options-flow value; default `0` = today's
    faithful pass-through behaviour.
  - This is a smoothing layer on top of the camera — the camera itself has no
    "present but static" signal, so this can only approximate presence.
  - Interacts with the stuck-on watchdog: `STUCK_ON_TIMEOUT` (60s) must stay
    **greater** than any chosen off-delay, or the watchdog could clip a held
    detection.

## Done

- [x] v0.1.1 — self-healing stuck-on watchdog (guards the CellMotion
      dropped-`off` latch; forces a key off after 60s with no fresh `true`)
- [x] v0.1.1 — re-baseline detection state on every (re)subscribe
- [x] v0.1.0 — initial release (ONVIF PullPoint → 6 deduplicated
      binary_sensors, config flow, HACS metadata)
