# TODO / Roadmap

Pending work for **tapo-onvif-events**. Contributions welcome.

## Investigation — pending recurrence

- [x] **Flap recovery for a chronically flaky camera — opt-in `flap_recovery` option (v0.3.0).**
  Some Tapo firmware (observed on a C325WB after a firmware update; a C575D on the same
  integration is unaffected) drives the ONVIF event endpoint into a *flapping* state: it
  creates the PullPoint subscription fine, but then drops a large fraction (~60% observed)
  of individual `PullMessages` connections with an instant `ServerDisconnectedError` — the
  TCP socket is accepted, then closed at the HTTP layer. Left unhandled this produced
  multi-hour silent event gaps.

  **The root cause of those gaps was on our side, not just the camera.** A live sole-client
  probe proved the PullPoint *subscription survives the disconnects*: one held subscription,
  never resubscribed, kept delivering the camera's queued events for 180s straight at
  ~31–44% pull success. The old loop tore the subscription down and re-subscribed on *every*
  error — the `Unsubscribe` discarded the events the camera had queued, and the fresh
  subscribe re-baselined state. During a bad stretch (roughly one attempt every few seconds
  at ~30% odds, each on a brand-new subscription) that silently dropped detections for
  minutes to hours. (Keep-alive was ruled out as the cause: forcing `Connection: close`
  scored *worse* — cold connections are dropped more — so the library's default keepalive
  is kept.)

  **Fix — opt-in, per-entry `flap_recovery` option (options flow, default OFF, so a healthy
  camera's code path is byte-for-byte unchanged):**
  - On a transient pull disconnect (`aiohttp.ClientConnectionError` / `ConnectionError`),
    retry `PullMessages` on the **same subscription** every `FLAP_PULL_RETRY_DELAY` (0.25s,
    ~4/s) instead of tearing it down — so the camera re-delivers its queued events on
    whichever connection survives. Turns a near-total blackout into events captured within
    ~1–2s even at a ~60% drop rate.
  - Escalate to a subscription **refresh** (teardown + fresh subscribe) only on a
    non-transient error, a hard-timeout stall, or no successful pull for
    `FLAP_OFFLINE_GRACE` (120s) *since the last (re)subscribe* — gated on the resubscribe
    time, not the last-success time, so a sustained zero-success stretch can't permanently
    collapse the rapid retry into one attempt per refresh.
  - Honest availability: the camera goes `unavailable` once no pull has completed for the
    grace window, instead of every entity reading a deceptive "off".
  - Log de-flood: one WARNING when a flap is detected + a `FLAP_HEARTBEAT` (300s) heartbeat;
    per-retry disconnects drop to DEBUG.

  Enable only on an affected camera's entry; leave OFF for healthy cameras. Also positively
  captured, during the probe, the long-suspected **`IsMotion` continuous-assert flood** (the
  camera streams `IsMotion=true` back-to-back) — see the flood item below; dedup absorbs it.

- [ ] **Suspected `IsMotion` "continuous-assert" flood (unconfirmed; max-on
      ceiling deferred until captured).**
  Observed once (2026-07-12): the `motion` sensor latched `on` for ~2h43m after
  a real event burst, with the pull loop apparently alive throughout (renewals
  continued) and the 60s watchdog never firing — which would require the camera
  to keep streaming `IsMotion=true` continuously (deduped, so invisible in
  history; per-event `rx` logging didn't exist yet, so this remains inference).
  Later instrumented captures of motion sticks (2026-07-14, ×2) turned out to be
  **dropped-`false`** instead — camera goes silent, watchdog force-offs at 60s —
  so the flood variant has never been positively observed.
  - **To confirm:** with v0.1.2's `rx`/DIAG debug logging, a flood shows as
    `DIAG motion on <N>s, last_true ~0s ago` persisting while stuck.
  - **Candidate fix (deferred):** absolute **max-on ceiling** for `IsMotion`
    (`MAX_ON_SECONDS`, e.g. 300s) that force-offs regardless of fresh `true`s,
    re-arming only on an observed `false` (or a cooldown) so an ongoing flood
    can't immediately re-latch/flap. Scope to `IsMotion`; the smart detectors
    clear cleanly. Interacts with the off-delay idea below — any off-delay must
    stay < the ceiling.

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

- [x] v0.1.2 — **hard client-side timeout on the `PullMessages` long-poll**
      (requested ONVIF timeout + 10s slack). Root-caused multi-hour stuck
      sensors: the camera can hold the long-poll open indefinitely — connection
      alive but never completing — freezing the pull loop *and* the in-loop
      watchdog, and trapping any queued closing `false` behind it (a sensor that
      went `on` just before the stall stays `on` for hours; the stale `false`
      only flushes when the next real event forces the camera to respond). The
      timeout aborts the stalled poll → reconnect → re-baseline clears any
      stranded key within ~one retry. Stalls were observed to cluster within
      ~2 min of a fresh (re)subscribe. Also: teardown guarded with 5s timeouts
      (a wedged connection could otherwise hang `Unsubscribe`/`close`), the
      stall warning names the keys that were `on` (`keys on: …`) to make a
      mid-detection stall self-evident, and debug-level diagnostics were added
      (per-poll duration/message-count, per-field `rx <Name>=<val>` including
      deduped repeats, and a DIAG line proving watchdog liveness + `last_true`
      age). Validated in production: multiple stalls caught and self-healed,
      dropped-`false` motion sticks cleared by the 60s watchdog, no stuck
      sensors.
- [x] Confirmed clean recovery across a real camera auto-reboot (2026-07-13):
      two brief drops during the reboot window, each re-subscribed within ~5s;
      the 30s offline grace absorbed the gaps so entities never went
      `unavailable`, and no stale `unavailable → on` edge occurred.
- [x] v0.1.1 — self-healing stuck-on watchdog (guards the CellMotion
      dropped-`false` latch; forces a key off after 60s with no fresh `true`).
      Since confirmed live: camera sends `IsMotion=true` then never the closing
      `false`; watchdog force-offs at ~60–75s.
- [x] v0.1.1 — re-baseline detection state on every (re)subscribe
- [x] v0.1.0 — initial release (ONVIF PullPoint → 6 deduplicated
      binary_sensors, config flow, HACS metadata)
