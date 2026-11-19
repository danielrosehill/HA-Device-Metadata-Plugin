# HA Device Metadata (agent plugin)

Agent skills for the [Device Metadata](https://github.com/danielrosehill/HA-Device-Metadata) Home Assistant
integration. That integration stores structured hardware records for each device (identity, purpose, location,
power, connectivity, lighting, lifecycle, custom fields) and maintains light capability groups.

## Skills

- `install`: install or update the integration through HACS, create the config entry, verify the panel and light
  groups, and flag clashes with existing `group.lights_*` writers.
- `scan`: infer protocol, hub, device type, power source, battery type and quantity, and cloud dependency from the
  device and entity registries (plus Battery Notes). Dry run first, never overwrites stored values.
- `fill-in`: interview the user in batches for location, function and description, and write the answers.

## Helper script

`scripts/dm.py` is a self-contained `uv` script that talks to Home Assistant over websocket. It needs `HA_URL` and
`HA_TOKEN` (an admin long-lived token).

```bash
uv run scripts/dm.py check
uv run scripts/dm.py scan --protocol Zigbee          # dry run
uv run scripts/dm.py scan --protocol Zigbee --apply
uv run scripts/dm.py gaps --integration mqtt
uv run scripts/dm.py set <device_id> '{"location_detail": "Hallway ceiling"}'
```

## Installation

```bash
claude plugins install ha-device-metadata@danielrosehill
```

## License

MIT
