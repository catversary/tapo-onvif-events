"""Constants for the Tapo ONVIF Events integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "tapo_onvif_events"

# Config-entry data keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

DEFAULT_PORT = 2020  # Tapo ONVIF port

# Options-flow key (per config entry). Opt-in flap recovery for a chronically
# flaky camera (seen on some Tapo C325WB firmware, whose ONVIF server hangs up
# the connection every few seconds in an endless reconnect loop). OFF by default
# so a healthy camera keeps the original, unchanged code path.
CONF_FLAP_RECOVERY = "flap_recovery"

# ONVIF PullPoint subscription namespace (xaddr key for the pull service)
PP_NS = "http://www.onvif.org/ver10/events/wsdl/PullPointSubscription"

# ONVIF Data-field name -> (entity key, HA device_class, friendly label)
# Vehicle/pet arrive on the generic TPSmartEvent topic; the subtype is the
# Data field NAME. Person/motion/linecross/tamper have their own topics.
KEYS: list[tuple[str, str, str, str]] = [
    ("IsPeople", "person", "motion", "Person"),
    ("IsVehicle", "vehicle", "motion", "Vehicle"),
    ("IsPet", "pet", "motion", "Pet"),
    ("IsMotion", "motion", "motion", "Motion"),
    ("IsLineCross", "linecross", "motion", "Boundary"),
    ("IsTamper", "tamper", "tamper", "Tamper"),
]

# Map an ONVIF Data field name to its entity key
FIELD_MAP: dict[str, str] = {field: key for (field, key, _dc, _label) in KEYS}

# Tapo PullPoint subscriptions expire after ~10 min. RENEW the same
# subscription well before that (never recreate -> no accumulating subs, no
# gap, no missed events, no unavailable blip).
SUB_LIFETIME = timedelta(seconds=600)  # requested / renewed subscription lifetime
RENEW_SECONDS = 480                     # renew this often (< SUB_LIFETIME)
PULL_TIMEOUT = 30                       # long-poll PullMessages timeout (seconds)
# Hard client-side ceiling on a single PullMessages call = its requested ONVIF
# timeout + this slack. A healthy camera always returns within PULL_TIMEOUT; some
# Tapo firmware occasionally holds the long-poll open indefinitely (connection
# alive, but the call never completes), which freezes the poll loop — and the
# stuck-key watchdog riding inside it — for hours. Past this we force-abort and
# reconnect, and the resubscribe re-baselines every key to off (clears stale latch).
PULL_HARD_SLACK = 10
RETRY_SECONDS = 5                       # backoff after a loop error

# Only mark entities unavailable after this many seconds of sustained failure,
# so a brief reconnect doesn't create an `unavailable -> on` edge (HA `to: "on"`
# triggers do NOT fire on that edge).
OFFLINE_GRACE = 30

# Self-healing watchdog: a key held "on" this long with no fresh `true` from the
# camera is treated as a stale latch and forced off. The camera floods repeated
# `true`s while a subject is genuinely present (sub-second cadence), so a gap this
# large means the detection really ended and its `false` was dropped/lost. Mainly
# guards the basic CellMotion (IsMotion) detector, which can emit an `on` with no
# matching `off`; the smart detectors clear on their own well within this window.
STUCK_ON_TIMEOUT = 60

# --- Flap recovery (only active when CONF_FLAP_RECOVERY is enabled) ----------
# A flaky camera can accept then drop the ONVIF connection every few seconds
# (ServerDisconnectedError) in a tight reconnect-fail loop that never holds a
# subscription long enough to pull queued events — so detections are silently
# lost while every entity still reads a healthy "off". These tunables let an
# opted-in entry (a) retry the pull on the same subscription instead of tearing
# it down (the teardown discards the camera's queued events) and de-flood the
# log, and (b) go honestly "unavailable" while the feed is down, so automations
# and reports can tell the event feed is degraded.
FLAP_THRESHOLD = 3        # consecutive disconnects before we treat it as a flap
# Pause before re-subscribing when the in-loop retry escalates (subscription
# refresh). Small — we want to resume rapid pulling on a fresh subscription
# quickly — but non-zero so we never hammer CreatePullPointSubscription (heavier
# than a pull, and the camera has a low subscription cap). This is a rare path:
# the escalation only fires after FLAP_OFFLINE_GRACE with zero successful pulls.
FLAP_RESUB_DELAY = 1.0
# Spacing between same-subscription pull retries during a flap. A dropped pull
# connection does NOT invalidate the PullPoint subscription (proven by live
# probe: one held subscription kept delivering queued events across a ~60% drop
# rate), so we retry the pull on the SAME subscription at this cadence instead of
# tearing down + resubscribing (the teardown is what loses the queued events).
# ~0.25s ≈ 4 attempts/s — enough to catch the ~40% of connections that survive
# within ~1-2s, without busy-spinning.
FLAP_PULL_RETRY_DELAY = 0.25
# No successful PullMessages for this long (while flapping) => mark unavailable.
# Must exceed one idle long-poll (PULL_TIMEOUT) so a quiet healthy camera — which
# still completes an empty PullMessages every long-poll — never trips it.
FLAP_OFFLINE_GRACE = 120
FLAP_HEARTBEAT = 300      # while flapping, emit one summary WARNING this often

# Dispatcher signals (per config entry)
def signal_state(entry_id: str) -> str:
    """Signal fired when a detection key changes state."""
    return f"{DOMAIN}_{entry_id}_state"


def signal_availability(entry_id: str) -> str:
    """Signal fired when camera availability changes."""
    return f"{DOMAIN}_{entry_id}_avail"
