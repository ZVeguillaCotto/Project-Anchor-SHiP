"""
SHiP v0.1 — Home Assistant compiler orchestration.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This is the coordinator: it reads Home Assistant automations, recognizes the
device ecosystem, asks that ecosystem's translator to build instructions, and
saves the results so they can later be undone.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
AnchorCompiler normalizes automation data, validates HA-specific registry
requirements, dispatches to isolated protocol handlers, persists records, and
provides the shared HA/MQTT/WebSocket/HTTP transports.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from .automation import parse_ha_automation_dict
from .protocols import (
    BindingRecord,
    CompileResult,
    PROTOCOL_ALIASES,
    Protocol,
    ProtocolDispatcher,
)
from .protocols.matter import (
    MATTER_CLUSTER_ACCESS_CONTROL,
    MATTER_CLUSTER_BINDING,
    MATTER_CLUSTER_LEVEL_CTRL,
    MATTER_CLUSTER_ONOFF,
    MATTER_CLUSTER_SCENES_MGMT,
    MATTER_CLUSTER_SWITCH,
    MATTER_WS_URL,
)
from .protocols.zwave import (
    ZWAVE_NONE,
    ZWAVE_S0_LEGACY,
    ZWAVE_S2_ACCESS_CONTROL,
    ZWAVE_S2_AUTHENTICATED,
    ZWAVE_S2_UNAUTHENTICATED,
    ZWAVE_SECURITY_RANK,
)

_LOGGER = logging.getLogger(__name__)


class AnchorCompiler:
    """Coordinate automation processing, protocol dispatch, and persistence."""

    def __init__(self, hass: Any, store: Any, stored_data: dict) -> None:
        self._hass = hass
        self._store = store
        self._stored_data = stored_data
        self._dispatcher = ProtocolDispatcher()
        from .quirks_inspector import QuirksInspector

        self._inspector = QuirksInspector()

    async def async_compile(
        self, automation_ids: list[str], dry_run: bool = True
    ) -> CompileResult:
        result = CompileResult()
        automations = await self._fetch_automations(automation_ids)
        if not automations:
            result.warnings.append(
                "ELI5: No eligible automations found. Make sure your automations "
                "have a device trigger (like a button press) and a device action "
                "(like turning on a light)."
            )
            return result

        for auto_config in automations:
            await self._compile_automation(auto_config, result, dry_run)
        if not dry_run:
            self._stored_data.setdefault("bindings", []).extend(
                asdict(binding) for binding in result.bindings_added
            )
            await self._store.async_save(self._stored_data)
        return result

    async def async_compile_config(
        self, config: dict, dry_run: bool = True
    ) -> CompileResult:
        """Compile one normalized config or Home Assistant automation."""
        result = CompileResult()
        parsed = parse_ha_automation_dict(
            config, self._get_device_registry(), self._get_entity_registry()
        )
        protocol = self._detect_protocol(parsed)
        if protocol == Protocol.UNKNOWN:
            result.success = False
            result.errors.append(
                "Unable to identify a supported protocol from this automation. "
                "Include protocol metadata or a device trigger/action domain."
            )
            return result
        parsed["protocol"] = protocol.name
        # ELI5: if the wizard collected gesture + time-window "rule sheets",
        # compile them into LEAT rows BEFORE dispatching the protocol
        # binding — so the hardware gets both the binding AND the cheat
        # sheet that decides what each tap does at each hour.
        rules = config.get("rules") or parsed.get("rules") or []
        if rules:
            leat = self.build_leat_table(rules)
            if leat:
                result.warnings.append(
                    "LEAT: compiled {} rule(s) into {} device row(s)".format(
                        len(rules), len(leat)
                    )
                )
                for row in leat:
                    result.dry_run_payloads.append({"step": "leat_write", "row": row})
            else:
                result.warnings.append(
                    "LEAT: no supported rules to compile (all gestures unknown)"
                )
        await self._compile_automation(parsed, result, dry_run)
        return result

    # ------------------------------------------------------------------
    # Multi-hop chain compilation  (v0.1.14)
    # ELI5: a physical chain like "switch → ceiling canopy → fan" needs the
    # gesture + action to travel through an intermediate module before it
    # reaches the load. We build the payload chain hop-by-hop, in order, so
    # the LEAT rows and the binding payloads line up with the real wiring.
    # ------------------------------------------------------------------
    def _hop_protocol(self, device: dict) -> Protocol:
        """ELI5: a hop's protocol is the one its *source* device speaks —
        a Z-Wave switch drives the first hop; a Zigbee canopy drives the
        second. Unknown protocols are tolerated: the per-hop compile just
        reports an error and the remaining hops still produce a preview."""
        for identifier in (device.get("identifiers") or []):
            if not isinstance(identifier, (tuple, list)) or not identifier:
                continue
            namespace = str(identifier[0]).upper()
            if namespace in PROTOCOL_ALIASES:
                return PROTOCOL_ALIASES[namespace]
        return Protocol.UNKNOWN

    def _hop_source_config(self, device: dict) -> dict:
        """ELI5: map a wizard device entry to the config keys each protocol
        handler expects. For bus protocols (ZHA/Z2M/Matter/Z-Wave) the IEEE
        / node id is what matters; for IP protocols (Shelly/Tasmota) the IP
        address or a device name is the fallback."""
        ieee = str(device.get("ieee") or "")
        model = str(device.get("model") or "")
        manufacturer = str(device.get("manufacturer") or "")
        identifier_value = ""
        identifiers = device.get("identifiers") or []
        if identifiers:
            first = identifiers[0]
            if isinstance(first, (tuple, list)) and len(first) > 1:
                identifier_value = str(first[1])
        return {
            "source_ieee": ieee,
            "source_model": model,
            "source_manufacturer": manufacturer,
            "shelly_ip": identifier_value,
            "controller_ip": identifier_value,
            "group_name": str(device.get("device_id") or "anchor_grp_1"),
        }

    async def async_compile_multihop(
        self,
        chain: list[dict],
        rules: list[dict] | None = None,
        dry_run: bool = True,
        group_name: str = "",
    ) -> CompileResult:
        """
        ELI5: compile a "chain" of devices — e.g. a switch, an optional
        intermediate canopy module, and a target load — into a single set of
        payloads, one per hop, that can be replayed in order.

        `chain` is a list of wizard device entries (as produced by the
        wizard), in the order the gesture should travel through them.
        `rules` are LEAT rule sheets; they are compiled once and attached
        to every hop so the LEAT rows are identical at each stage of the
        chain.
        """
        result = CompileResult()
        if not chain:
            result.success = False
            result.errors.append(
                "ELI5: no hops to compile — pass at least one device in the chain."
            )
            return result

        # LEAT rows are compiled once up front so every hop carries the
        # same cheat-sheet — the device that actually runs the gesture
        # (usually the first hop) can share it with the rest of the chain
        # at frame-encode time.
        compiled_leat: list[dict] = []
        if rules:
            compiled_leat = self.build_leat_table(rules)
            if compiled_leat:
                result.warnings.append(
                    "LEAT: compiled {} rule(s) into {} device row(s) "
                    "(attached to every hop)".format(
                        len(rules), len(compiled_leat)
                    )
                )
                for row in compiled_leat:
                    result.dry_run_payloads.append({"step": "leat_write", "row": row})

        hop_count = max(1, len(chain) - 1)
        for index in range(len(chain) - 1):
            source = chain[index]
            target = chain[index + 1]
            hop_config: dict[str, Any] = dict(self._hop_source_config(source))
            hop_config["targets"] = [dict(target)]
            # ELI5: IP-protocol legs address the *next* hop by its own
            # identifier value (the device's IP, or IEEE as a fallback).
            next_value = ""
            identifiers = target.get("identifiers") or []
            if identifiers and isinstance(identifiers[0], (tuple, list)) and len(identifiers[0]) > 1:
                next_value = str(identifiers[0][1])
            if next_value:
                hop_config.setdefault("target_ip", next_value)
                hop_config.setdefault("target_ips", [next_value])
                hop_config.setdefault("next_hop_ip", next_value)
            if group_name:
                hop_config["group_name"] = group_name
            hop_protocol = self._hop_protocol(source)
            hop_config["protocol"] = hop_protocol.name
            # ELI5: run this hop through the same single-hop compile the
            # wizard already uses — the per-protocol handler builds the
            # exact payloads (ZHA service calls, Shelly HTTP, Tasmota
            # Backlog, Matter WS, Z-Wave association, …).
            try:
                parsed = parse_ha_automation_dict(
                    hop_config, self._get_device_registry(),
                    self._get_entity_registry(),
                )
            except Exception as exc:  # noqa: BLE001
                result.success = False
                result.errors.append("Hop {} → {}: {}".format(index + 1, index + 2, exc))
                continue
            parsed["protocol"] = hop_protocol.name
            await self._compile_automation(parsed, result, dry_run)
            # Tag each payload with its hop index so the UI can group the
            # dry-run output into "Hop 1", "Hop 2", … sections.
            for payload in result.dry_run_payloads:
                if isinstance(payload, dict) and "hop_index" not in payload:
                    payload["hop_index"] = index + 1
            if not result.success:
                # ELI5: the whole chain only works when every hop compiles.
                # Stop early and report which hop broke so the user can
                # fix it and retry without recompiling the others.
                result.errors.append(
                    "ELI5: multi-hop chain aborted at hop {} → {}. "
                    "Fix that hop's protocol or devices, then retry.".format(
                        index + 1, index + 2,
                    )
                )
                return result

        # ELI5: summary line so the wizard can show "2 hops, 3 rules".
        result.warnings.append(
            "Multi-hop chain compiled: {} hop(s) across {} device(s).".format(
                hop_count, len(chain)
            )
        )
        return result

    # ------------------------------------------------------------------
    # LEAT (Local Event-Action Table) compilation
    # ELI5: the wizard can collect "rule sheets" — a gesture (tap count /
    # hold), an optional time-of-day window, and action parameters
    # (brightness, colour temp, valve open/close, lock state, …). This
    # section turns those rows into the exact LEAT table layout that the
    # C core (`ship_leat_entry_t` in ship_protocol.h) stores on hardware.
    # Because LEAT lives on the device, the rules keep working even when
    # the hub is offline.
    # ------------------------------------------------------------------
    _GESTURE_TO_NIBBLE = {
        "1x Tap": 1, "2x Tap": 2, "3x Tap": 3, "4x Tap": 4,
        "5x Tap": 5, "Hold": 6,
    }
    # ELI5: which domain nibble a rule "belongs to" depends on what the
    # rule does. A brightness rule is a lighting-combo domain (0x2); a
    # plain on/off rule is OnOff (0x1); valve rules are Water (0x4);
    # lock rules are Smart Lock (0x5). This mirrors ship_domain_t.
    @staticmethod
    def _rule_domain(rule: dict) -> int:
        if "color_temp_mireds" in rule or "brightness" in rule:
            return 0x2  # SHIP_DOMAIN_LIGHTING_COMBO
        if "valve_open" in rule:
            return 0x4  # SHIP_DOMAIN_WATER_VALVE
        if "lock_unlock" in rule:
            return 0x5  # SHIP_DOMAIN_SMART_LOCK
        if "setpoint_c" in rule:
            return 0x6  # SHIP_DOMAIN_HVAC_CLIMATE
        if "fan_speed" in rule:
            return 0x6  # SHIP_DOMAIN_HVAC_CLIMATE (fan speed param_2)
        return 0x1      # SHIP_DOMAIN_ONOFF (plain on/off)

    @staticmethod
    def _rule_action(rule: dict) -> tuple[int, int, dict]:
        """
        ELI5: pack a rule's action for the LEAT row.

        The C struct (`ship_leat_entry_t`) stores exactly ONE action byte
        (`action_param`) per rule, plus an `action_type` tag (0=relay,
        1=brightness, 2=scene, 3=valve, 4=climate). Richer values that do
        not fit in one byte (colour temperature in mireds is 153–500!) are
        NOT invented into the struct — they are carried in the
        `frame_params` annotation, which the frame encoder uses when the
        19-byte packet is actually sent (frame param_1/param_2/param_3).

        Returns (action_type, action_param 0–255, frame_params annotation).
        """
        frame_params: dict = {}
        if "valve_open" in rule:
            return 3, (1 if rule["valve_open"] else 0), frame_params
        if "lock_unlock" in rule:
            return 0, (1 if rule["lock_unlock"] else 0), frame_params
        if "setpoint_c" in rule:
            # ELI5: °C × 2 to keep one decimal of precision inside a byte.
            return 4, int(round(float(rule["setpoint_c"]) * 2)) & 0xFF, frame_params
        if "fan_speed" in rule:
            # ELI5: fan speed 0–5 fits easily in one byte.
            return 4, int(rule["fan_speed"]) & 0xFF, frame_params
        brightness = int(rule.get("brightness", 1))
        # ELI5: brightness (0–255) IS the LEAT action byte. Colour temp and
        # fade time only fit in the *frame* parameters — keep them there.
        if "color_temp_mireds" in rule:
            frame_params["param_1"] = brightness & 0xFF
            frame_params["param_2"] = int(rule["color_temp_mireds"]) & 0xFFFF
        if "transition_seconds" in rule:
            frame_params["param_3"] = min(255, int(rule["transition_seconds"]) * 10)
        if frame_params:
            return 1, brightness & 0xFF, frame_params
        return 0, (1 if brightness > 0 else 0), frame_params

    def build_leat_table(
        self, rules: list[dict], group_id: int = 0
    ) -> list[dict]:
        """
        ELI5: turn the wizard's rule list into LEAT rows that exactly
        match `ship_leat_entry_t` (ship_protocol.h):
          {group_id, domain, gesture, action_type, action_param,
           hour_start, hour_end, enabled}
        Values that need more than one byte (colour temperature in mireds,
        fade time) are NOT forced into the struct — they ride along in an
        optional `frame_params` annotation for the 19-byte frame encoder.
        """
        table: list[dict] = []
        for rule in rules:
            gesture_label = str(rule.get("gesture", ""))
            gesture = self._GESTURE_TO_NIBBLE.get(gesture_label)
            if gesture is None:
                # ELI5: a row with a gesture we don't support is skipped,
                # not fatal — the remaining rules still compile.
                _LOGGER.debug(
                    "LEAT: skipping rule with unknown gesture %r", gesture_label
                )
                continue
            domain = self._rule_domain(rule)
            action_type, action_param, frame_params = self._rule_action(rule)
            hour_start = int(rule.get("hour_start", 0) or 0) % 24
            hour_end = int(rule.get("hour_end", 0) or 0) % 24
            # ELI5: the row mirrors `ship_leat_entry_t` field-for-field so
            # the ESPHome component can memcpy it straight into the table.
            row = {
                "group_id": int(group_id) & 0xFF,
                "domain": domain,
                "gesture": gesture,
                "action_type": action_type,
                "action_param": action_param & 0xFF,
                "hour_start": hour_start,
                "hour_end": hour_end,
                "enabled": True,
            }
            # ELI5: optional annotation for the 19-byte frame encoder
            # (colour temp / fade time). Not part of the C LEAT struct.
            if frame_params:
                row["frame_params"] = frame_params
            table.append(row)
        return table

    def list_bindings(self) -> list[BindingRecord]:
        """
        ELI5: return every binding that SHiP Anchor has previously saved
        (whether from a wizard run or a service call). Used by the
        options flow to let the user *see* what is currently baked in.
        """
        out: list[BindingRecord] = []
        for raw in self._stored_data.get("bindings", []):
            try:
                out.append(BindingRecord(**{
                    key: value for key, value in raw.items()
                    if key in BindingRecord.__dataclass_fields__
                }))
            except (TypeError, ValueError):
                _LOGGER.warning("Skipping malformed binding record: %s", raw)
        return out

    async def async_erase_binding(
        self, source_ieee: str, target_ieee: str, protocol: str
    ) -> bool:
        """
        ELI5: remove one specific binding (matched by source IEEE +
        target IEEE + protocol) from the persistent store. If the protocol
        handler supports an undo, call it first so the device itself is
        reset. Returns True if the record was removed, False if it was
        not found.
        """
        key = (protocol.lower(), source_ieee.lower(), target_ieee.lower())
        remaining = []
        removed: BindingRecord | None = None
        for raw in self._stored_data.get("bindings", []):
            try:
                binding = BindingRecord(**{
                    k: v for k, v in raw.items()
                    if k in BindingRecord.__dataclass_fields__
                })
            except (TypeError, ValueError):
                remaining.append(raw)
                continue
            if (
                binding.protocol.lower() == key[0]
                and binding.source_ieee.lower() == key[1]
                and binding.target_ieee.lower() == key[2]
            ):
                removed = binding
                continue
            remaining.append(raw)
        if removed is None:
            return False
        # Attempt a protocol-specific undo (e.g. ZHA unbind, Matter
        # AccessControl clear). If the protocol has no undo, we still
        # remove the local record so the UI reflects the new state.
        try:
            await self._erase_binding(removed)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Protocol erase hook failed for %s", removed)
        self._stored_data["bindings"] = remaining
        await self._store.async_save(self._stored_data)
        _LOGGER.info(
            "SHiP Anchor: erased binding %s %s → %s",
            removed.protocol, removed.source_ieee, removed.target_ieee,
        )
        return True

    async def async_erase_all(self) -> None:
        """Remove all offline bindings previously programmed.

        ELI5: "Erase everything" is a user action, not a per-device
        negotiation. We try a best-effort device reset for each saved
        binding, but ANY per-binding problem (unsupported protocol,
        malformed record, device offline, transport error) must NOT
        prevent the binding list from being cleared. Previously a single
        failing undo raised here, so the store was never saved and the
        user saw a long traceback instead of a clean success. Now the
        list is always cleared and saved; per-binding problems are only
        reported as warnings.
        """
        bindings_raw = list(self._stored_data.get("bindings", []))
        skipped = 0
        for raw in bindings_raw:
            try:
                binding = BindingRecord(**{
                    key: value for key, value in raw.items()
                    if key in BindingRecord.__dataclass_fields__
                })
                supported = await self._erase_binding(binding)
            except Exception as exc:  # noqa: BLE001 — erase must be resilient
                _LOGGER.warning(
                    "Erase: best-effort device reset failed for %s: %s",
                    raw if isinstance(raw, dict) else type(raw).__name__,
                    exc,
                )
                supported = False
            if not supported:
                skipped += 1
                _LOGGER.warning(
                    "Erase: no device-side undo available for binding %s "
                    "(record removed from the local store).",
                    raw if isinstance(raw, dict) else type(raw).__name__,
                )
        # ELI5: the local record is cleared unconditionally — that is what
        # "erase all" means to the user and to the storage file.
        self._stored_data["bindings"] = []
        await self._store.async_save(self._stored_data)
        if skipped:
            _LOGGER.warning(
                "SHiP Anchor: all %d offline binding(s) erased from the local "
                "store (%d had no supported device-side undo).",
                len(bindings_raw), skipped,
            )
        else:
            _LOGGER.warning(
                "SHiP Anchor: all %d offline binding(s) erased.",
                len(bindings_raw),
            )

    async def _fetch_automations(self, ids: list[str]) -> list[dict]:
        """Retrieve configured automations from Home Assistant."""
        if self._hass is None or not hasattr(self._hass, "config"):
            return []
        try:
            from homeassistant.util.yaml import load_yaml  # type: ignore[import]
            path = self._hass.config.path("automations.yaml")
        except ImportError as exc:
            raise RuntimeError("Home Assistant YAML loader is unavailable") from exc

        configs = await asyncio.to_thread(load_yaml, path)
        if isinstance(configs, dict):
            configs = [configs]
        if not isinstance(configs, list):
            raise ValueError(
                "automations.yaml must contain a list of automation dictionaries"
            )
        selected_ids = set(ids)
        states = self._hass.states.async_all("automation")
        device_registry = self._get_device_registry()
        entity_registry = self._get_entity_registry()
        results = []
        for config in configs:
            if not isinstance(config, dict):
                continue
            automation_id = str(config.get("id", ""))
            alias = str(config.get("alias", ""))
            state_id_match = any(
                state.object_id in selected_ids
                and (
                    str(state.attributes.get("id", "")) == automation_id
                    or str(state.name) == alias
                )
                for state in states
            )
            if selected_ids and not (
                automation_id in selected_ids
                or alias in selected_ids
                or state_id_match
            ):
                continue
            results.append(
                parse_ha_automation_dict(config, device_registry, entity_registry)
            )
        return results

    def _get_device_registry(self) -> Any:
        if self._hass is None:
            return None
        try:
            from homeassistant.helpers import device_registry  # type: ignore[import]
        except ImportError:
            return None
        return device_registry.async_get(self._hass)

    def _get_entity_registry(self) -> Any:
        if self._hass is None:
            return None
        try:
            from homeassistant.helpers import entity_registry  # type: ignore[import]
        except ImportError:
            return None
        return entity_registry.async_get(self._hass)

    async def _compile_automation(
        self, auto_config: dict, result: CompileResult, dry_run: bool
    ) -> None:
        """Normalize one automation, fan out targets, then dispatch handlers."""
        config = parse_ha_automation_dict(
            auto_config, self._get_device_registry(), self._get_entity_registry()
        )
        protocol = self._detect_protocol(config)
        config["protocol"] = protocol.name
        automation_label = str(
            auto_config.get("alias")
            or auto_config.get("id")
            or auto_config.get("automation_id")
            or "unknown"
        )
        _LOGGER.debug(
            "SHiP Anchor: compiling automation '%s' → protocol=%s",
            automation_label,
            protocol.name,
        )
        has_ha_steps = any(
            key in config for key in ("trigger", "triggers", "action", "actions")
        )

        if protocol == Protocol.MATTER and has_ha_steps:
            targets = config.get("targets", [])
            if (
                config.get("source_node_id") is None
                or not targets
                or any(target.get("node_id") is None for target in targets)
            ):
                result.success = False
                result.errors.append(
                    "Matter automation needs source and target Node IDs from the "
                    "device registry before bindings can be compiled."
                )
                return
            for target in targets:
                target_config = dict(config)
                target_config["target_node_id"] = target["node_id"]
                target_config["target_endpoint"] = target.get(
                    "endpoint", config.get("target_endpoint", 1)
                )
                await self._dispatcher.compile(
                    protocol, self, target_config, result, dry_run
                )
            return

        if protocol == Protocol.ZWAVE and has_ha_steps:
            targets = config.get("targets", [])
            if (
                config.get("source_node_id") is None
                or not config.get("source_security")
                or not targets
                or any(
                    target.get("node_id") is None
                    or not (
                        target.get("security_class")
                        or target.get("security")
                        or config.get("target_security")
                    )
                    for target in targets
                )
            ):
                result.success = False
                result.errors.append(
                    "Z-Wave automation needs source/target node IDs and matching "
                    "security classes before direct associations can be compiled."
                )
                return
            for target in targets:
                target_config = dict(config)
                target_config["target_node_id"] = target["node_id"]
                target_config["target_security"] = (
                    target.get("security_class")
                    or target.get("security")
                    or config["target_security"]
                )
                await self._dispatcher.compile(
                    protocol, self, target_config, result, dry_run
                )
            return

        if protocol == Protocol.UNKNOWN:
            result.success = False
            result.errors.append(
                "Automation '{}' — protocol could not be detected. "
                "Use a device trigger with a supported domain (zha, zwave_js, "
                "matter, shelly, tasmota, zigbee2mqtt), or add an explicit "
                "'protocol:' key to the automation.".format(automation_label)
            )
            return
        await self._dispatcher.compile(protocol, self, config, result, dry_run)

    def _detect_protocol(self, config: dict) -> Protocol:
        parsed = parse_ha_automation_dict(
            config, self._get_device_registry(), self._get_entity_registry()
        )
        return PROTOCOL_ALIASES.get(
            str(parsed.get("protocol", "")).upper(), Protocol.UNKNOWN
        )

    async def _erase_binding(self, binding: BindingRecord) -> bool:
        return await self._dispatcher.erase(self, binding)

    async def _hass_service_call(self, service: str, data: dict) -> None:
        if not self._hass:
            raise RuntimeError("Home Assistant is unavailable for service calls")
        domain, service_name = service.split(".", 1)
        await self._hass.services.async_call(domain, service_name, data)

    async def _mqtt_publish(self, topic: str, payload: str) -> None:
        if not self._hass:
            raise RuntimeError("Home Assistant is unavailable for MQTT publishing")
        from homeassistant.components import mqtt  # type: ignore[import]

        await mqtt.async_publish(self._hass, topic, payload, qos=1)

    async def _matter_ws_send(self, message: dict) -> None:
        import websockets  # type: ignore[import]

        async with websockets.connect(MATTER_WS_URL, open_timeout=5) as ws:
            await ws.send(json.dumps(message))
            response = await asyncio.wait_for(ws.recv(), timeout=10)
            _LOGGER.debug("Matter WS response: %s", response)

    async def _http_post_json(self, url: str, body: dict) -> None:
        import aiohttp  # type: ignore[import]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                response.raise_for_status()

    async def _http_get_params(self, url: str, params: dict) -> None:
        import aiohttp  # type: ignore[import]

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                response.raise_for_status()


__all__ = [
    "AnchorCompiler",
    "BindingRecord",
    "CompileResult",
    "Protocol",
    "parse_ha_automation_dict",
]
