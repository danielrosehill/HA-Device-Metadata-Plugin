---
name: fill-in
description: Interview the user to fill device location, purpose and description in Device Metadata, in batches by area or integration.
---

# Fill in what only the user knows

Scanning can't know where a device is mounted or what it is for. This skill collects that from the user
efficiently and writes it to Device Metadata. Connection setup is as in the `install` skill.

## Find the gaps

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/dm.py gaps                                  # description, function, location_detail
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/dm.py gaps --integration mqtt --fields location_detail,function
```

Each row has `device_id`, `name`, `area`, `model`, `missing` and `known` (everything already recorded, including the
values that come from Home Assistant).

## Interview

- Work in batches of 5–10 devices that belong together (same area, same integration, same kind), not one long list.
- For each device, show what is known (name, model, type, area) and propose a draft from the name and model, e.g.
  "kitchen_fire: photoelectric smoke detector, 2× AA → description *Smoke detector*, function *Kitchen fire alarm;
  triggers the sirens*, location *Kitchen ceiling?*". The user then only corrects.
- Accept terse answers ("ceiling above stove", "skip", "same as previous") and map them to fields:
  `location_detail` (exact spot), `mounting` (enum), `description` (what it is), `function` (what it is for and
  what depends on it), `criticality` (Critical / Important / Convenience / Experimental).
- Enum fields must use the exact option text. Read the options from `dm.py` `get` output or the integration's
  `device_metadata/schema` websocket call.
- If the user mentions a hardware fact the scan missed (battery type, plug type, bulb base, USB connector), record it
  too.

## Write

```bash
uv run ${CLAUDE_PLUGIN_ROOT}/scripts/dm.py set <device_id> '{"location_detail": "Kitchen ceiling, above the stove", "mounting": "Ceiling", "function": "Kitchen fire alarm"}'
```

Merges with what is stored. An empty string removes a field. After each batch, re-run `gaps` with the same filter
and report how many are left.

If the user would rather type into a form, point them at the **Device Metadata** panel in the Home Assistant sidebar.
