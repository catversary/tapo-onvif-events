# Tapo ONVIF Events

Clean, phantom-free detection `binary_sensor`s for TP-Link **Tapo** cameras,
sourced directly from the camera's **ONVIF PullPoint** event stream.

It runs **alongside** [`tapo_control`](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control)
— keep that for streams, PTZ, siren and config. This integration only replaces
its detection sensors, which fabricate phantom person/vehicle events on their
~8-minute renewal cycle.

Per camera you get six local-push sensors: **Person, Vehicle, Pet, Motion,
Boundary, Tamper** — deduplicated (state only changes on a real transition) and
near-real-time (~1s).

Add via **Settings → Devices & Services → Add Integration → Tapo ONVIF Events**,
using the camera's ONVIF/"Camera Account" credentials (port `2020`).
