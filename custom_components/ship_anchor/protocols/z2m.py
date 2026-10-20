"""
SHiP Anchor Zigbee2MQTT protocol handler.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This translator sends Zigbee2MQTT a concise instruction telling a switch to
bind to a bulb or a shared group of bulbs.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Builds MQTT request payloads for device binding and routes them through the
compiler's Home Assistant MQTT transport.
"""

from __future__ import annotations

import json
from typing import Any

from . import BindingRecord, CompileResult, ProtocolHandler


class Z2mHandler(ProtocolHandler):
    async def compile(
        self, compiler: Any, cfg: dict, result: CompileResult, dry_run: bool
    ) -> None:
        source = cfg.get("source_friendly_name", "kitchen_dimmer")
        targets = cfg.get("targets", [])
        clusters = cfg.get("clusters", ["genOnOff", "genLevelCtrl", "genScenes"])
        if len(targets) > 2:
            target = cfg.get("group_friendly_name", "{}_group".format(source))
        else:
            target = (
                targets[0].get("friendly_name", "kitchen_light")
                if targets else "kitchen_light"
            )
        topic = "zigbee2mqtt/bridge/request/device/bind"
        payload = {
            "from": source,
            "from_endpoint": cfg.get("source_endpoint", 2),
            "to": target,
            "clusters": clusters,
        }
        result.dry_run_payloads.append({"topic": topic, "payload": payload})
        if not dry_run:
            await compiler._mqtt_publish(topic, json.dumps(payload))
        result.bindings_added.append(BindingRecord(
            protocol="zigbee_z2m",
            source_ieee=source,
            target_ieee=targets[0].get("friendly_name", "") if targets else "",
            clusters=[],
            group_id=None,
            preflight_done=True,
        ))

    async def erase(self, compiler: Any, binding: BindingRecord) -> bool:
        topic = "zigbee2mqtt/bridge/request/device/unbind"
        payload = {"from": binding.source_ieee, "to": binding.target_ieee}
        await compiler._mqtt_publish(topic, json.dumps(payload))
        return True
