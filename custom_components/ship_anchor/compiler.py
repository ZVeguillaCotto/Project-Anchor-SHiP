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
        await self._compile_automation(parsed, result, dry_run)
        return result

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
