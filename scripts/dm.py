#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["websockets>=12"]
# ///
"""Device Metadata helper: scan Home Assistant devices and fill the Device Metadata integration.

Connection: HA_URL (e.g. http://homeassistant.local:8123) and HA_TOKEN (long-lived access token) from the
environment, or --url / --token-env. Output is JSON on stdout.

Commands
  check                  is the integration installed and set up? (HACS state, config entry, websocket)
  scan [--apply]         infer fields from the registry (protocol, hub, cloud dependency, device type,
                         power source, battery type/quantity). Dry run unless --apply. Never overwrites a
                         value already stored; only fills empty fields.
  gaps [--fields a,b]    devices missing human-only fields (default: description,function,location_detail)
  set DEVICE_ID JSON     merge values into one device (JSON object of field -> value)
  get DEVICE_ID          one record
  export                 full export (custom fields + records)

Filters for scan/gaps: --integration DOMAIN (repeatable), --search TEXT (name/model/area substring),
--include-services, --include-disabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys

import websockets

# --------------------------------------------------------------------------- inference rules

# integration domain -> fields. "hub" is the radio/bridge the device talks through.
INTEGRATION_RULES: dict[str, dict] = {
    "zha": {"protocols": ["Zigbee"], "hub": "ZHA"},
    "deconz": {"protocols": ["Zigbee"], "hub": "deCONZ"},
    "hue": {"protocols": ["Zigbee"], "hub": "Philips Hue bridge"},
    "tradfri": {"protocols": ["Zigbee"], "hub": "IKEA TRADFRI gateway"},
    "zwave_js": {"protocols": ["Z-Wave"], "hub": "Z-Wave JS"},
    "matter": {"protocols": ["Matter"], "hub": "Matter Server"},
    "homekit_controller": {},
    "bthome": {"protocols": ["BLE"]},
    "xiaomi_ble": {"protocols": ["BLE"]},
    "govee_ble": {"protocols": ["BLE"]},
    "switchbot": {"protocols": ["BLE"]},
    "ibeacon": {"protocols": ["BLE"]},
    "shelly": {"protocols": ["Wi-Fi 2.4 GHz"]},
    "tplink": {"protocols": ["Wi-Fi 2.4 GHz"]},
    "tuya": {"protocols": ["Wi-Fi 2.4 GHz"]},
    "localtuya": {"protocols": ["Wi-Fi 2.4 GHz"]},
    "tasmota": {"protocols": ["Wi-Fi 2.4 GHz"]},
    "wled": {"protocols": ["Wi-Fi 2.4 GHz"]},
    "broadlink": {"protocols": ["Wi-Fi 2.4 GHz", "Infrared"]},
}

# name/model keywords -> device type, checked before entity domains (Zigbee2MQTT exposes sirens and
# smoke/CO alarms as switches)
KEYWORD_TYPES = [
    (("smoke", "carbon monoxide", "co alarm", "co sensor", "gas detector", "gas sensor", "leak", "water sensor",
      "flood"), "Sensor"),
    (("siren",), "Siren"),
    (("doorbell",), "Doorbell"),
    (("smart plug", "socket", "power plug"), "Smart plug"),
]

# entity domain -> device type, in priority order (first match wins)
DOMAIN_TYPES = [
    ("siren", "Siren"),
    ("lock", "Lock"),
    ("camera", "Camera"),
    ("climate", "Thermostat / HVAC"),
    ("water_heater", "Thermostat / HVAC"),
    ("cover", "Blind / curtain motor"),
    ("light", "Light"),
    ("media_player", "Speaker"),
    ("vacuum", "Appliance"),
    ("lawn_mower", "Appliance"),
    ("fan", "Appliance"),
    ("humidifier", "Appliance"),
]

BATTERY_TYPES = [
    "CR2032", "CR2450", "CR2477", "CR123A", "CR2", "CR1632", "AA", "AAA", "9V", "18650",
]


def _battery_type(raw: str | None) -> tuple[str | None, int | None]:
    """Map a Battery Notes type string ('CR2032', '2× AAA', 'Rechargeable') to (option, quantity)."""
    if not raw or raw in ("unknown", "unavailable"):
        return None, None
    s = raw.strip()
    qty = None
    m = re.match(r"^(\d+)\s*[x×]\s*(.+)$", s, re.I)
    if m:
        qty, s = int(m.group(1)), m.group(2).strip()
    up = s.upper().replace(" ", "")
    for opt in sorted(BATTERY_TYPES, key=len, reverse=True):
        if up == opt.upper():
            return opt, qty
    if up in ("RECHARGEABLE", "IRREPLACEABLE", "MANUAL", "LI-ION", "LIPO", "BUILT-IN"):
        return "Built-in Li-ion / LiPo", qty
    return "Other", qty


def infer(dev: dict, entities: list[dict], states: dict, manifests: dict) -> dict[str, tuple]:
    """Return {field: (value, reason)} for one device."""
    ha = dev["ha"]
    out: dict[str, tuple] = {}
    integrations = ha["integrations"]
    is_z2m = "mqtt" in integrations and any("zigbee2mqtt" in str(i).lower() for i in dev.get("_identifiers", []))
    z2m_bridge = is_z2m and any("bridge" in str(i).lower() for i in dev.get("_identifiers", []))

    # protocol + hub
    if is_z2m:
        out["protocols"] = (["Zigbee"], "Zigbee2MQTT device (mqtt identifiers)")
        out["hub"] = ("Zigbee2MQTT", "Zigbee2MQTT device")
        if z2m_bridge:
            out["device_type"] = ("Coordinator / radio", "Zigbee2MQTT bridge device")
            out.pop("hub")
    else:
        for domain in integrations:
            rule = INTEGRATION_RULES.get(domain)
            if rule:
                if rule.get("protocols"):
                    out["protocols"] = (rule["protocols"], f"integration {domain}")
                if rule.get("hub"):
                    out["hub"] = (rule["hub"], f"integration {domain}")
                break

    # cloud dependency from integration iot_class: only for single-integration devices whose radio we
    # recognised (a local_push integration such as mobile_app says nothing about the device itself),
    # or when the integration is cloud-only.
    if len(integrations) == 1:
        iot = (manifests.get(integrations[0]) or {}).get("iot_class", "")
        if iot.startswith("local") and ("protocols" in out or is_z2m):
            out["cloud_dependency"] = ("Local only", f"{integrations[0]} iot_class {iot}")
        elif iot.startswith("cloud"):
            out["cloud_dependency"] = ("Cloud only", f"{integrations[0]} iot_class {iot}")

    # entities: primary (non config/diagnostic) domains + battery evidence
    primary = [e for e in entities if not e.get("entity_category")]
    domains = {e["entity_id"].split(".")[0] for e in primary}
    battery_entity = None
    bn_type = None
    for e in entities:
        st = states.get(e["entity_id"], {})
        attrs = st.get("attributes", {})
        dc = e.get("device_class") or e.get("original_device_class") or attrs.get("device_class")
        if dc == "battery" and e["entity_id"].split(".")[0] in ("sensor", "binary_sensor") and e.get("platform") != "battery_notes":
            battery_entity = e["entity_id"]
        if e.get("platform") == "battery_notes" and e["entity_id"].endswith("_battery_type"):
            bn_type = (st.get("state"), attrs.get("battery_quantity"))

    text = f"{ha['name']} {ha.get('model') or ''}".lower()
    has_battery = battery_entity is not None or bool(bn_type and bn_type[0])
    if "device_type" not in out:
        dtype = None
        for words, label in KEYWORD_TYPES:
            if any(w in text for w in words):
                dtype = label
                break
        if dtype is None and has_battery and not (domains - {"event", "sensor"}) and (
            not domains or "event" in domains or "button" in text or "remote" in text
        ):
            dtype = "Button / remote"
        if dtype is None:
            for d, label in DOMAIN_TYPES:
                if d in domains:
                    dtype = label
                    break
        if dtype == "Speaker":
            if any((states.get(e["entity_id"], {}).get("attributes", {}).get("device_class") == "tv") for e in primary):
                dtype = "Display"
        if dtype is None:
            ev_classes = {
                states.get(e["entity_id"], {}).get("attributes", {}).get("device_class")
                for e in primary if e["entity_id"].startswith("event.")
            }
            if "doorbell" in ev_classes:
                dtype = "Doorbell"
            elif domains and domains <= {"event", "button", "sensor"} and "event" in domains:
                dtype = "Button / remote"
            elif "switch" in domains:
                dtype = "Smart plug" if "plug" in text or "socket" in text else "Switch / relay"
            elif domains and domains <= {"sensor", "binary_sensor"}:
                dtype = "Sensor"
        if dtype:
            out["device_type"] = (dtype, f"entity domains {sorted(domains)}")

    # power
    if bn_type and bn_type[0]:
        opt, qty = _battery_type(bn_type[0])
        if opt:
            out["battery_type"] = (opt, f"Battery Notes type {bn_type[0]!r}")
            q = qty or (int(bn_type[1]) if str(bn_type[1] or "").isdigit() else None)
            if q:
                out["battery_quantity"] = (q, "Battery Notes quantity")
            rechargeable = opt == "Built-in Li-ion / LiPo"
            out["power_source"] = (
                "Battery (rechargeable)" if rechargeable else "Battery (replaceable)",
                f"Battery Notes type {bn_type[0]!r}",
            )
    elif battery_entity:
        out["power_source"] = ("Battery (replaceable)", f"battery entity {battery_entity}")
    return out


# --------------------------------------------------------------------------- websocket


class HA:
    def __init__(self, url: str, token: str):
        self.url = re.sub(r"^http", "ws", url.rstrip("/")) + "/api/websocket"
        self.token = token
        self.n = 0

    async def __aenter__(self):
        self.ws = await websockets.connect(self.url, max_size=64 * 1024 * 1024)
        await self.ws.recv()
        await self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        msg = json.loads(await self.ws.recv())
        if msg.get("type") != "auth_ok":
            raise SystemExit(f"auth failed: {msg}")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def call(self, type_: str, **kw):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "type": type_, **kw}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n and msg.get("type") == "result":
                if not msg.get("success"):
                    raise RuntimeError(f"{type_}: {msg.get('error')}")
                return msg["result"]


def _filter(devices, args):
    out = []
    q = (args.search or "").lower()
    for d in devices:
        ha = d["ha"]
        if ha["service"] and not args.include_services:
            continue
        if ha["disabled"] and not args.include_disabled:
            continue
        if args.integration and not set(args.integration) & set(ha["integrations"]):
            continue
        if q and q not in " ".join(str(x) for x in (ha["name"], ha.get("model"), ha.get("area"), ha.get("manufacturer"))).lower():
            continue
        out.append(d)
    return out


async def cmd_check(ha: HA, args):
    res = {"websocket_api": False, "config_entry": False, "hacs_installed": None}
    entries = await ha.call("config_entries/get", domain="device_metadata")
    res["config_entry"] = bool(entries)
    try:
        schema = await ha.call("device_metadata/schema")
        res["websocket_api"] = True
        res["custom_fields"] = len(schema["custom_fields"])
        lst = await ha.call("device_metadata/list")
        res["devices"] = len(lst["devices"])
        res["with_metadata"] = sum(1 for d in lst["devices"] if d["values"])
        res["orphans"] = len(lst["orphans"])
    except RuntimeError as err:
        res["error"] = str(err)
    for prefix in ("marketplace", "hacs"):
        try:
            repos = await ha.call(f"{prefix}/repositories/list", categories=["integration"])
            for r in repos:
                if r["full_name"].lower() == "danielrosehill/ha-device-metadata":
                    res["hacs_installed"] = r["installed"]
                    res["hacs_repository_id"] = r["id"]
                    res["installed_version"] = r.get("installed_version")
                    res["available_version"] = r.get("available_version")
            res["hacs_ws_prefix"] = prefix
            break
        except RuntimeError:
            continue
    return res


async def cmd_scan(ha: HA, args):
    lst = await ha.call("device_metadata/list")
    regdevs = {d["id"]: d for d in await ha.call("config/device_registry/list")}
    ents = await ha.call("config/entity_registry/list")
    states = {s["entity_id"]: s for s in await ha.call("get_states")}
    manifests = {m["domain"]: m for m in await ha.call("manifest/list")}
    by_dev: dict[str, list] = {}
    for e in ents:
        if e.get("device_id"):
            by_dev.setdefault(e["device_id"], []).append(e)
    plan = []
    for d in _filter(lst["devices"], args):
        d["_identifiers"] = [":".join(i) for i in regdevs.get(d["device_id"], {}).get("identifiers", [])]
        inferred = infer(d, by_dev.get(d["device_id"], []), states, manifests)
        if args.protocol and args.protocol not in (inferred.get("protocols", ([],))[0] or []):
            continue
        fill = {k: v for k, v in inferred.items() if k not in d["values"]}
        if fill:
            plan.append({
                "device_id": d["device_id"],
                "name": d["ha"]["name"],
                "area": d["ha"]["area"],
                "fill": {k: v[0] for k, v in fill.items()},
                "why": {k: v[1] for k, v in fill.items()},
            })
    applied = 0
    if args.apply:
        for p in plan:
            await ha.call("device_metadata/set", device_id=p["device_id"], values=p["fill"])
            applied += 1
    return {"devices_considered": len(_filter(lst["devices"], args)), "devices_to_fill": len(plan),
            "applied": applied, "dry_run": not args.apply, "plan": plan}


async def cmd_gaps(ha: HA, args):
    fields = [f.strip() for f in (args.fields or "description,function,location_detail").split(",") if f.strip()]
    lst = await ha.call("device_metadata/list")
    rows = []
    for d in _filter(lst["devices"], args):
        missing = [f for f in fields if f not in d["effective"]]
        if missing:
            rows.append({"device_id": d["device_id"], "name": d["ha"]["name"], "area": d["ha"]["area"],
                         "model": " ".join(x for x in (d["ha"].get("manufacturer"), d["ha"].get("model")) if x),
                         "integrations": d["ha"]["integrations"], "missing": missing,
                         "known": {k: v["value"] for k, v in d["effective"].items()}})
    return {"fields": fields, "count": len(rows), "devices": rows}


async def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["check", "scan", "gaps", "set", "get", "export"])
    p.add_argument("rest", nargs="*")
    p.add_argument("--url", default=os.environ.get("HA_URL"))
    p.add_argument("--token-env", default="HA_TOKEN")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--fields")
    p.add_argument("--integration", action="append")
    p.add_argument("--search")
    p.add_argument("--protocol", help="scan: only devices whose inferred protocols include this (e.g. Zigbee)")
    p.add_argument("--include-services", action="store_true")
    p.add_argument("--include-disabled", action="store_true")
    args = p.parse_args()
    token = os.environ.get(args.token_env)
    if not args.url or not token:
        raise SystemExit(f"Set HA_URL (or --url) and {args.token_env}.")
    async with HA(args.url, token) as ha:
        if args.command == "check":
            res = await cmd_check(ha, args)
        elif args.command == "scan":
            res = await cmd_scan(ha, args)
        elif args.command == "gaps":
            res = await cmd_gaps(ha, args)
        elif args.command == "set":
            res = await ha.call("device_metadata/set", device_id=args.rest[0], values=json.loads(args.rest[1]))
        elif args.command == "get":
            res = await ha.call("device_metadata/get", device_id=args.rest[0])
        else:
            res = await ha.call("device_metadata/export")
    json.dump(res, sys.stdout, indent=1, ensure_ascii=False)
    print()


if __name__ == "__main__":
    asyncio.run(main())
