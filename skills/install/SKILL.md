---
name: install
description: Install or update the Device Metadata integration on Home Assistant via HACS, set it up and verify the panel and light groups.
---

# Install / update Device Metadata

Integration repo: `https://github.com/danielrosehill/HA-Device-Metadata` (HACS category *integration*, domain
`device_metadata`). This skill puts it on the user's Home Assistant and confirms it works.

## Connection

The helper script needs `HA_URL` (e.g. `http://homeassistant.local:8123`) and `HA_TOKEN` (a long-lived access
token of an **admin** user) in the environment. Settings persist in:

```
${CLAUDE_USER_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/claude-plugins}/ha-device-metadata/config.json
```

as `{"ha_url": "...", "token_env": "HA_TOKEN", "token_command": "<optional command that prints the token>"}`.
Read it first. If it is missing, ask the user for the URL and how to get the token (env var, or a command from
their secret manager), and write it. Never store the token itself in the file.

Script: `${CLAUDE_PLUGIN_ROOT}/scripts/dm.py` (a `uv` script, so run it with `uv run`; Python 3.11+).

## Steps

1. **Check current state**
   `uv run ${CLAUDE_PLUGIN_ROOT}/scripts/dm.py check` prints JSON: `hacs_installed`, `installed_version`,
   `available_version`, `config_entry`, `websocket_api`, `with_metadata`. `hacs_ws_prefix` is `hacs` or `marketplace`
   (newer HACS renamed its websocket commands). Use that prefix below.
2. **Add the custom repository** if HACS doesn't list it (`hacs_installed` is null): websocket
   `<prefix>/repositories/add` with `{"repository": "danielrosehill/HA-Device-Metadata", "category": "integration"}`.
   Then re-run `check` to get `hacs_repository_id`.
3. **Install or update**: `<prefix>/repository/install` with `{"repository": "<hacs_repository_id>", "version": "<tag>"}`
   (latest tag from `available_version`). Without HACS: copy `custom_components/device_metadata` from the repo's
   latest release into `/config/custom_components/`.
4. **Restart Home Assistant core.** It's needed the first time and whenever Python files changed. Tell the user before
   restarting: automations pause for about a minute. Wait until `GET /api/` answers 200.
5. **Create the config entry** (first install only): `POST /api/config/config_entries/flow` with
   `{"handler": "device_metadata"}`, then POST `{}` to `/api/config/config_entries/flow/<flow_id>`.
6. **Verify**: `check` again shows `websocket_api: true` and `config_entry: true`. The sidebar has **Device
   Metadata** (`/device-metadata`). Light groups: `group.lights_onoff|dimmable|white|color` exist and list lights.

## Light capability groups: watch for clashes

The integration maintains `group.lights_onoff`, `group.lights_dimmable`, `group.lights_white` and
`group.lights_color` with `group.set`. If the user already has something writing those object ids (an automation or
blueprint calling `group.set`, or YAML `group:` entries with the same ids), the two will overwrite each other. Find it
and ask the user to choose: retire the old one (disable the automation), or turn off **Maintain light capability
groups** in the integration options. Status LEDs that show up as lights: set that device's **Light capability**
to *Not a light (indicator / status LED)*.

## Then

Offer the `scan` skill to pre-fill records from the registry.
