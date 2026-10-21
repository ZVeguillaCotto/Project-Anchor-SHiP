"""
SHiP Anchor Home Assistant automation normalization.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
Home Assistant automations name devices in several different ways. This
normalizer translates those names into consistent device and protocol details.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Extracts trigger/action targets, resolves device and entity registries, derives
IEEE/node/security metadata, and preserves explicitly supplied configuration.
"""

from __future__ import annotations

import re
from typing import Any

from .protocols import PROTOCOL_ALIASES, Protocol


def parse_ha_automation_dict(
    auto_dict: dict, device_registry: Any, entity_registry: Any = None
) -> dict:
    """Normalize Home Assistant automation YAML while retaining direct configs."""
    normalized = dict(auto_dict)
    declared_protocol = str(auto_dict.get("protocol", "")).upper()
    contains_ha_steps = any(
        key in auto_dict for key in ("trigger", "triggers", "action", "actions")
    )
    if declared_protocol in PROTOCOL_ALIASES and not contains_ha_steps:
        normalized["protocol"] = PROTOCOL_ALIASES[declared_protocol].name
        return normalized

    def as_sequence(value: Any) -> list:
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    def lookup_device(device_id: Any) -> dict:
        if not device_id:
            return {}
        if hasattr(device_registry, "async_get"):
            device = device_registry.async_get(device_id)
        elif isinstance(device_registry, dict):
            device = device_registry.get(device_id)
        else:
            device = None
        if device is None:
            return {"device_id": str(device_id)}
        if isinstance(device, dict):
            info = dict(device)
        else:
            info = {
                name: getattr(device, name, None)
                for name in (
                    "id", "ieee", "model", "manufacturer", "identifiers",
                    "connections", "name", "name_by_user", "config_entry_id",
                    "security_class", "security",
                )
            }
        identifiers = info.get("identifiers") or ()
        connections = info.get("connections") or ()
        ieee = info.get("ieee")
        if not ieee:
            for identifier in identifiers:
                if isinstance(identifier, (tuple, list)) and len(identifier) > 1:
                    candidate = str(identifier[1])
                    if re.fullmatch(
                        r"(?:[0-9a-fA-F]{16}|(?:[0-9a-fA-F]{2}:){7}[0-9a-fA-F]{2})",
                        candidate,
                    ):
                        ieee = candidate
                        break
        if not ieee:
            for connection in connections:
                if isinstance(connection, (tuple, list)) and len(connection) > 1:
                    candidate = str(connection[1])
                    if re.fullmatch(
                        r"(?:[0-9a-fA-F]{16}|(?:[0-9a-fA-F]{2}:){7}[0-9a-fA-F]{2})",
                        candidate,
                    ):
                        ieee = candidate
                        break
        info["device_id"] = str(device_id)
        info["ieee"] = ieee or ""
        if info.get("node_id") is None:
            for identifier in identifiers:
                if not isinstance(identifier, (tuple, list)) or len(identifier) < 2:
                    continue
                namespace = str(identifier[0]).lower()
                if "matter" not in namespace and "zwave" not in namespace:
                    continue
                node_match = re.fullmatch(
                    r"(?:node(?:[_ -]?id)?[:_-]?)?(\d+)",
                    str(identifier[1]),
                    re.IGNORECASE,
                )
                if node_match:
                    info["node_id"] = int(node_match.group(1))
                    break
        return info

    def ids_from(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return [str(item) for item in value if isinstance(item, str)]
        return []

    def device_for_entity(entity_id: str) -> dict:
        if hasattr(entity_registry, "async_get"):
            entity = entity_registry.async_get(entity_id)
        elif isinstance(entity_registry, dict):
            entity = entity_registry.get(entity_id)
        else:
            entity = None
        if isinstance(entity, dict):
            device_id = entity.get("device_id")
        else:
            device_id = getattr(entity, "device_id", None)
        device = lookup_device(device_id) if device_id else {}
        device["entity_id"] = entity_id
        return device

    triggers = as_sequence(auto_dict.get("trigger", auto_dict.get("triggers")))
    actions = as_sequence(auto_dict.get("action", auto_dict.get("actions")))
    source_trigger = next(
        (item for item in triggers if isinstance(item, dict)
         and item.get("platform") == "device"),
        next((item for item in triggers if isinstance(item, dict)), {}),
    )
    source = lookup_device(source_trigger.get("device_id"))

    target_device_ids: list[str] = []
    target_entity_ids: list[str] = []
    protocol_hints = [
        declared_protocol,
        source_trigger.get("domain"),
        source_trigger.get("integration"),
    ]
    for identifier in source.get("identifiers") or ():
        if isinstance(identifier, (tuple, list)) and identifier:
            protocol_hints.append(str(identifier[0]))
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_name = str(action.get("action", action.get("service", "")))
        if "." in action_name:
            protocol_hints.append(action_name.split(".", 1)[0])
        target = action.get("target") or {}
        if isinstance(target, dict):
            target_device_ids.extend(ids_from(target.get("device_id")))
            target_entity_ids.extend(ids_from(target.get("entity_id")))
        target_device_ids.extend(ids_from(action.get("device_id")))
        target_entity_ids.extend(ids_from(action.get("entity_id")))

    targets = [
        lookup_device(device_id) for device_id in dict.fromkeys(target_device_ids)
    ]
    targets.extend(
        device_for_entity(entity_id) for entity_id in dict.fromkeys(target_entity_ids)
    )
    supplied_targets = normalized.get("targets", [])
    if isinstance(supplied_targets, list):
        merged_targets = []
        for target in targets:
            match = next(
                (
                    item for item in supplied_targets
                    if isinstance(item, dict) and (
                        item.get("device_id") == target.get("device_id")
                        or item.get("entity_id") == target.get("entity_id")
                    )
                ),
                {},
            )
            merged = dict(match)
            merged.update({
                key: value for key, value in target.items()
                if value not in (None, "")
            })
            merged_targets.append(merged)
        targets = merged_targets or list(supplied_targets)

    source_signature = "{} {}".format(
        source.get("manufacturer", ""), source.get("model", "")
    ).lower()
    if "tasmota" in source_signature:
        protocol_hints.append("TASMOTA")
    if "shelly" in source_signature:
        protocol_hints.append("SHELLY")
    for target in targets:
        signature = "{} {}".format(
            target.get("manufacturer", ""), target.get("model", "")
        ).lower()
        if "tasmota" in signature:
            protocol_hints.append("TASMOTA")
        if "shelly" in signature:
            protocol_hints.append("SHELLY")

    # ELI5: If the trigger's domain didn't tell us which protocol this
    # automation talks to (e.g., the automation uses an entity-state trigger
    # like "when the door sensor is on"), we look at every entity in the
    # automation and ask Home Assistant's entity registry which integration
    # each one belongs to. "zha", "zwave_js", "matter", etc. map 1-to-1 to
    # the protocols SHiP Anchor knows how to program.
    def _fallback_protocol_from_entities() -> Protocol:
        entity_map = {
            "zha": "ZIGBEE_ZHA",
            "zigbee2mqtt": "ZIGBEE_Z2M",
            "zwave_js": "ZWAVE",
            "zwave": "ZWAVE",
            "matter": "MATTER",
            "shelly": "SHELLY",
            "tasmota": "TASMOTA",
        }
        seen: set = set()
        for trigger in triggers:
            if not isinstance(trigger, dict):
                continue
            for entity_id in ids_from(trigger.get("entity_id")):
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                if hasattr(entity_registry, "async_get"):
                    entity = entity_registry.async_get(entity_id)
                elif isinstance(entity_registry, dict):
                    entity = entity_registry.get(entity_id)
                else:
                    continue
                if not entity:
                    continue
                integration = (
                    entity.get("platform") if isinstance(entity, dict)
                    else getattr(entity, "platform", None)
                )
                if integration in entity_map:
                    return PROTOCOL_ALIASES[entity_map[integration]]
        for action in actions:
            if not isinstance(action, dict):
                continue
            target = action.get("target") or {}
            if isinstance(target, dict):
                entity_ids = ids_from(target.get("entity_id"))
            else:
                entity_ids = []
            for entity_id in entity_ids or ids_from(action.get("entity_id")):
                if entity_id in seen:
                    continue
                seen.add(entity_id)
                if hasattr(entity_registry, "async_get"):
                    entity = entity_registry.async_get(entity_id)
                elif isinstance(entity_registry, dict):
                    entity = entity_registry.get(entity_id)
                else:
                    continue
                if not entity:
                    continue
                integration = (
                    entity.get("platform") if isinstance(entity, dict)
                    else getattr(entity, "platform", None)
                )
                if integration in entity_map:
                    return PROTOCOL_ALIASES[entity_map[integration]]
        return Protocol.UNKNOWN

    protocol = next(
        (
            PROTOCOL_ALIASES[hint.upper()]
            for hint in protocol_hints
            if isinstance(hint, str) and hint.upper() in PROTOCOL_ALIASES
        ),
        _fallback_protocol_from_entities(),
    )
    normalized.update({
        "protocol": protocol.name,
        "source_ieee": normalized.get("source_ieee") or source.get("ieee", ""),
        "source_model": normalized.get("source_model") or source.get("model") or "",
        "source_manufacturer": normalized.get("source_manufacturer")
            or source.get("manufacturer") or "",
        "targets": targets,
    })
    if normalized.get("source_node_id") is None and source.get("node_id") is not None:
        normalized["source_node_id"] = source["node_id"]
    if targets and normalized.get("target_node_id") is None:
        first_target = targets[0]
        if first_target.get("node_id") is not None:
            normalized["target_node_id"] = first_target["node_id"]
    if normalized.get("source_security") is None:
        normalized["source_security"] = (
            source.get("security_class") or source.get("security")
        )
    if source_trigger.get("subtype") is not None:
        normalized["trigger_subtype"] = source_trigger["subtype"]
    if source_trigger.get("domain") == "zha":
        normalized.setdefault("source_endpoint", source_trigger.get("endpoint_id", 1))
    return normalized


__all__ = ["parse_ha_automation_dict"]
