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

Target temperature is intentionally not exposed yet.

## Behaviour notes
- **Reads:** HTTPS GetThingShadow only. Reads that connect with `client_id == thingName` are never used — they
  kick the wall controller off the cloud. If HTTPS is denied (current state), entities run in **assumed state**:
  they show the last commanded value, with no polling.
- **Commands:** MQTT over websockets, QoS 1, unique `app-ha<random>-dev` client ID, `clientToken: app-<thingName>`,
  partial `state.desired` only. Refreshes 4 s and 12 s after each command.
- **Requested state:** a commanded value shows immediately (attribute `requested: true`) until the unit confirms it or
  45 s pass, then the entity falls back to what the unit reports.
