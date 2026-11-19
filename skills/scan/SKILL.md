---
name: scan
description: Scan Home Assistant devices and auto-fill Device Metadata (protocol, hub, device type, power, battery, cloud dependency); dry run first.
---

# Scan devices and fill metadata

Fills the empty fields of Device Metadata records with what can be inferred from Home Assistant. It never
overwrites a stored value. Connection setup is the same as the `install` skill (config at
`${CLAUDE_USER_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-plugins}/ha-device-metadata/config.json`, `HA_URL` +
token env var).

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/dm.py scan                      # dry run, all devices
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/dm.py scan --protocol Zigbee    # only devices inferred as Zigbee
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/dm.py scan --integration zha --search kitchen
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/dm.py scan --protocol Zigbee --apply
```

Output JSON: `devices_to_fill`, and `plan[]` with `name`, `fill` (field → value) and `why` (field → evidence).

## What it infers

| Field | Evidence |
|---|---|
| `protocols`, `hub` | Zigbee2MQTT identifiers on MQTT devices; integration (zha, deconz, hue, zwave_js, matter, BLE integrations, shelly, tplink, tuya, tasmota, wled, broadlink) |
| `cloud_dependency` | integration `iot_class`: cloud_* → Cloud only; local_* → Local only, but only when the radio was recognised (a local integration like `mobile_app` says nothing about the device) |
| `device_type` | name/model keywords first (siren, smoke, carbon monoxide, leak, doorbell, plug), then battery devices with no controllable entities → Button / remote, then entity domains (siren, lock, camera, climate, cover, light, media_player, switch, sensors) |
| `power_source`, `battery_type`, `battery_quantity` | Battery Notes type sensor (e.g. `2× AAA`), else a battery-class sensor → Battery (replaceable) |

Light capability needs no scan. The integration derives it live from `supported_color_modes` and keeps the
capability groups itself.

## Procedure

1. Run the dry run (filtered if the user asked for a subset) and show the plan as a compact table: device, fields
   to fill. Flag anything that looks wrong (e.g. a "Switch / relay" that is clearly a siren by name).
2. On the user's go-ahead, re-run with `--apply`, then run the dry run again and confirm `devices_to_fill` is 0.
3. Wrong guesses: correct them per device with
   `dm.py set <device_id> '{"device_type": "Siren"}'`. If the same mistake would recur for other users, improve the
   rules in `scripts/dm.py` (`KEYWORD_TYPES`, `INTEGRATION_RULES`) and contribute it back.
4. Offer the `fill-in` skill for the fields only a human knows (description, function, exact location).
