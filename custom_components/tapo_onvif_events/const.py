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

# Dispatcher signals (per config entry)
def signal_state(entry_id: str) -> str:
    """Signal fired when a detection key changes state."""
    return f"{DOMAIN}_{entry_id}_state"


def signal_availability(entry_id: str) -> str:
    """Signal fired when camera availability changes."""
    return f"{DOMAIN}_{entry_id}_avail"
