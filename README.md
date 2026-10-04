# MrSteam iSteamX for Home Assistant

Custom integration for MrSteam iSteamX / Bliss steam generators (cloud, AWS IoT device shadow).

## Install
HACS → Integrations → ⋮ → Custom repositories → add this repo (category: Integration) → install → restart.
Then Settings → Devices & services → Add integration → **MrSteam iSteamX**, sign in with your MrSteam app account.

## Entities (per steam unit)
| Entity | Source |
|---|---|
| `switch.*_steam` | start: `appSteamStatus: true` + full program object from `deviceProgramList`; stop: `appSteamStatus: false`; state `deviceSteamStatus` |
| `number.*_steam_duration` | `appSteamTime` (4-digit hex minutes), state `deviceSteamTime` |
| `sensor.*_steam_time_remaining` | `deviceSteamRemainTime` (hex minutes) |
| `sensor.*_room_temperature` | `deviceRoomTemp`, raw/18 = °C (inferred — validate against the touchscreen) |
| `sensor.*_steam_head_temperature` | `deviceSteamTemp` (diagnostic; NOT the target setpoint) |
| `light.*_chroma` | off type 0 · white type 4 `#FFFFFF` · color type 1 `#RRGGBB` · brightness `lightBright.data` 1–33 |
| `switch.*_aroma` | `aroma.open`, state `deviceAromaStatus` |
| `number.*_steam_target_temperature` | `appSteamTemp` = (°F − 32) × 10, sent with `appSteamStatus: true`; state `deviceSteamTemp` |

## Controller rules (confirmed on hardware, Oct 4 2026)
- **Steam is the gateway.** Duration, temperature, Aroma and Chroma are only accepted during a session; the entities refuse otherwise.
- **Only changes count.** The controller acts on values that change in the shadow, and the shadow keeps stale values
  (a session ended at the touchscreen leaves `appSteamStatus: true`). Commands publish a "nudge" first so the real value is always a change.
- **A `steam` update without `appSteamStatus` reads as "off"**, so duration and temperature always carry `appSteamStatus: true`.
- The unit resets the target to 110 °F at every start.
- Chroma on always includes a brightness (never the stale near-invisible level).
- `mrsteam.send_desired` publishes a raw `state.desired` fragment, for testing.

## Behaviour notes
- **Live updates (push, no polling):** a persistent MQTT connection with its own unique `app-ha<random>-dev` client ID
  subscribes to the shadow's `update/documents`, `update/accepted` and `get/accepted` topics, so changes made at the
  touchscreen or in the app arrive as they happen. It never uses the thing name as a client ID (that kicks the wall
  controller off AWS IoT). Each topic is subscribed separately; denied ones are logged and skipped. The steam switch's
  `live_updates` attribute shows which topics are active. If none are allowed, entities fall back to assumed state.
- **HTTPS reads:** GetThingShadow is tried first; it is currently denied for this identity, so push is the read path.
- **Commands:** MQTT over websockets, QoS 1, unique `app-ha<random>-dev` client ID, `clientToken: app-<thingName>`,
  partial `state.desired` only. Refreshes 4 s and 12 s after each command.
- **Requested state:** a commanded value shows immediately (attribute `requested: true`) until the unit confirms it or
  45 s pass, then the entity falls back to what the unit reports.
