# Tapo ONVIF Events

A Home Assistant custom integration that creates clean, deduplicated detection
`binary_sensor`s for TP-Link **Tapo** cameras straight from the camera's own
**ONVIF PullPoint** event stream.

It is **events-only** and designed to run *alongside*
[`tapo_control`](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control): keep
that integration for live streams, PTZ, siren and camera configuration — this
one only replaces its **detection sensors**.

- **Local push** — a persistent ONVIF PullPoint subscription per camera; events
  arrive in ~1s, no polling, no cloud.
- **Deduplicated** — a sensor only changes on a real `off`↔`on` transition. The
  camera floods ~18 duplicate events/second while a subject is present; those
  are collapsed.
- **No inbound endpoint** — PullPoint pulls, so it works behind NAT with no
  webhook/port-forward.

## Why this exists

`tapo_control`'s detection `binary_sensor`s **phantom-fire**: person/vehicle flip
`on` with no real subject, on a steady ~8-minute cadence. This was traced to the
integration re-asserting state on its native-API / subscription-renewal cycle —
**not** the camera. Proof: running an independent ONVIF PullPoint subscription
during a phantom received **zero** ONVIF events, while a real walk-test pushed a
real `PeopleDetector/People` event within ~10s. The camera's ONVIF stream is
clean and functional; the integration invents the phantoms.

This integration subscribes to that same clean ONVIF stream directly.

> Note: an ONVIF webhook (Base Notification push) was suggested by others as
> "more reliable", but it did **not** fix the phantoms — the phantoms come from
> the native-API path, not ONVIF delivery. PullPoint is kept because it needs no
> inbound endpoint.

## Entities

Six sensors per camera:

| ONVIF topic | ONVIF Data field | Entity | `device_class` |
|---|---|---|---|
| `PeopleDetector/People` | `IsPeople` | `_person` | motion |
| `TPSmartEventDetector/TPSmartEvent` | `IsVehicle` | `_vehicle` | motion |
| `TPSmartEventDetector/TPSmartEvent` | `IsPet` | `_pet` | motion |
| `CellMotionDetector/Motion` | `IsMotion` | `_motion` | motion |
| `LineCrossDetector/LineCross` | `IsLineCross` | `_boundary` | motion |
| `TamperDetector/Tamper` | `IsTamper` | `_tamper` | tamper |

Vehicle vs pet is the **Data field name** inside the generic `TPSmartEvent`
topic, not a separate topic.

## Installation (HACS)

1. HACS → ⋮ → **Custom repositories** → add this repo's URL, category
   **Integration**.
2. Install **Tapo ONVIF Events**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Tapo ONVIF Events**.

### Configuration

| Field | Value |
|---|---|
| **Host** | Camera IP, e.g. `192.168.1.50` |
| **ONVIF port** | `2020` for Tapo |
| **Username** / **Password** | The camera's **Camera Account** credentials, set in the Tapo app (*Advanced Settings → Camera Account*). **Not** your TP-Link cloud login. |

Add one entry per camera. The camera serial number is used as the unique id, so
the same camera can't be added twice.

## Behaviour & reliability notes

- **Subscription renewal.** Tapo PullPoint subscriptions expire after ~10 min.
  The integration **renews** the same subscription every 480s (600s lifetime)
  and `Unsubscribe`s on teardown — it never recreates, which would pile up
  subscriptions past the camera's limit and cause `Fault('error')`.
- **Availability grace.** A camera is only marked `unavailable` after ~30s of
  sustained failure, so a brief reconnect doesn't create an `unavailable → on`
  edge (Home Assistant `to: "on"` triggers don't fire on that edge).

## Requirements

- Home Assistant 2024.12 or newer.
- `onvif-zeep-async` (installed automatically; pinned to the tested version).
- A Tapo camera with ONVIF enabled and a Camera Account configured.

## Status

**v0.1.0** — initial release. Ported from a proven standalone ONVIF→MQTT bridge.

## License

MIT — see [`LICENSE`](LICENSE).
