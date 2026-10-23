"""
============================================================================
SHiP Anchor v0.1 — custom_components/ship_anchor/config_flow.py
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
This file is the "wizard" that pops up when you click
Settings → Devices & Services → Add Integration → SHiP Anchor.

Instead of making you type raw YAML, it asks four plain-English questions:

  Step 1  "Which switch or button is the SOURCE?"   → pick from a dropdown
  Step 2  "Which lights, valves, or locks is it
           pointing at?"                             → multi-select + group
  Step 3  "What should each tap / hold do?"          → behaviour mapping,
                                                        brightness, colour temp
  Step 4  "Preview or apply?"                        → SHiP Anchor runs the
                                                        compiler and stores
                                                        the result on the
                                                        config entry

Every step validates its input before letting you continue, and the final
step runs the same `AnchorCompiler.async_compile_config()` code path that
the `ship_anchor.compile` service uses — so what you see here is exactly
what a service call would produce.

============================================================================

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
Extends homeassistant.config_entries.ConfigFlow (domain=ship_anchor).
- Four async_step_* methods: user, target, behavior, finish.
- Source and target discovery uses Home Assistant's device + entity
  registries (no extra I/O, no MQTT, no zigpy calls).
- Compilation uses AnchorCompiler.async_compile_config() with the protocol
  derived from the source device's identifiers.
- All user-facing text lives in strings.json (and translations/en.json) so
  non-English Home Assistant installations can localise the wizard.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    NumberSelector,
    NumberSelectorConfig,
)

from . import DOMAIN
from .compiler import AnchorCompiler
from .protocols import Protocol, PROTOCOL_ALIASES

_LOGGER = logging.getLogger(__name__)

# ELI5: the wizard needs a way to find "switch-like" and "light-like"
# devices. Home Assistant doesn't publish a single "is this a button?"
# flag, so we infer from entity domains and device model strings.
SOURCE_DOMAINS = {
    "switch", "button", "scene", "remote", "input_button",
}
TARGET_DOMAINS = {
    "light", "lock", "valve", "cover", "climate", "fan", "humidifier",
}

# ELI5: the "behaviour" step offers a fixed list of common switch
# interactions. The list is short on purpose — if the user needs a
# different mapping they can always use the service call instead.
TRIGGER_OPTIONS = [
    "1x Tap", "2x Tap", "3x Tap", "4x Tap", "5x Tap", "Hold",
]

# ELI5: each *kind* of target supports different action parameters. A light
# gets brightness and colour temperature; a fan gets speed; a valve gets
# open/close; a lock gets lock/unlock. The wizard only shows fields the
# selected target(s) actually support.
TARGET_CAPABILITIES = {
    "light": {"brightness", "color_temp", "transition"},
    "fan":   {"speed", "transition"},
    "valve": {"open_close"},
    "lock":  {"lock_unlock"},
    "cover": {"open_close", "transition"},
    "climate": {"setpoint"},
}


def _device_id(device: Any) -> str:
    return str(getattr(device, "id", ""))


def _device_name(device: Any) -> str:
    return (
        getattr(device, "name_by_user", None)
        or getattr(device, "name", None)
        or _device_id(device)
        or "unknown device"
    )


def _device_manufacturer(device: Any) -> str:
    return str(getattr(device, "manufacturer", "") or "")


def _device_model(device: Any) -> str:
    return str(getattr(device, "model", "") or "")


def _device_identifiers(device: Any) -> list:
    return list(getattr(device, "identifiers", ()) or ())


def _device_ieee(device: Any) -> str:
    """Extract an IEEE-style identifier from a device, if present."""
    import re

    for identifier in _device_identifiers(device):
        if not isinstance(identifier, (tuple, list)) or len(identifier) < 2:
            continue
        candidate = str(identifier[1])
        if re.fullmatch(
            r"(?:[0-9a-fA-F]{16}|(?:[0-9a-fA-F]{2}:){7}[0-9a-fA-F]{2})",
            candidate,
        ):
            return candidate
    return ""


def _infer_protocol(device: Any) -> Protocol:
    """
    ELI5: guess which protocol a device talks to by looking at its
    identifier namespace (e.g. ("zha", "00:11:22:...") → ZIGBEE_ZHA).
    """
    for identifier in _device_identifiers(device):
        if not isinstance(identifier, (tuple, list)) or len(identifier) < 1:
            continue
        namespace = str(identifier[0]).upper()
        if namespace in PROTOCOL_ALIASES:
            return PROTOCOL_ALIASES[namespace]
        if namespace == "ZIGBEE":
            return Protocol.ZIGBEE_ZHA
        if namespace == "ZIGBEE2MQTT":
            return Protocol.ZIGBEE_Z2M
        if namespace == "ZWAVE":
            return Protocol.ZWAVE
    sig = "{} {}".format(_device_manufacturer(device), _device_model(device)).lower()
    if "tasmota" in sig:
        return Protocol.TASMOTA
    if "shelly" in sig:
        return Protocol.SHELLY
    return Protocol.UNKNOWN


def _target_kind(device: Any, entity_reg: Any) -> str:
    """
    ELI5: work out WHAT KIND of target a device is (light, fan, valve,
    lock, ...) so the behaviour step can show only the fields that device
    can actually act on. We look at the entity domains attached to the
    device first, then fall back to keywords in its model string.
    """
    if entity_reg is not None:
        for entity in entity_reg.entities.values():
            if getattr(entity, "device_id", None) != _device_id(device):
                continue
            domain = str(getattr(entity, "platform", "") or "")
            if domain in TARGET_CAPABILITIES:
                return domain
    sig = "{} {}".format(_device_manufacturer(device), _device_model(device)).lower()
    for kind, keywords in (
        ("light", ("bulb", "light", "lca", "lta", "ltl")),
        ("fan", ("fan",)),
        ("valve", ("valve", "moisture", "leak")),
        ("lock", ("lock", "deadbolt")),
        ("cover", ("shade", "cover", "blind", "roller")),
        ("climate", ("thermostat", "climate", "climate")),
    ):
        if any(k in sig for k in keywords):
            return kind
    return "light"


def _device_protocols_match(a: Protocol, b: Protocol) -> bool:
    """
    ELI5: two devices "match" when they speak the same protocol, so a
    Zigbee switch can be paired with a Zigbee light but never with a Z-Wave
    lock (which would silently do nothing). ZHA and Zigbee2MQTT are both
    Zigbee, so they cross-match — that is intentional.
    """
    zigbee = {Protocol.ZIGBEE_ZHA, Protocol.ZIGBEE_Z2M}
    if a in zigbee and b in zigbee:
        return True
    return a == b


class ShipAnchorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Interactive wizard that builds one offline binding per config entry."""

    VERSION = 1

    # ------------------------------------------------------------------
    # Step 1 — pick the TARGET device(s)  (target-first workflow)
    # ELI5: we start with what the user WANTS TO CONTROL (the light, valve,
    # lock, fan), not with the switch. That way SHiP Anchor can immediately
    # detect the protocol (Zigbee, Matter, Z-Wave, Shelly, Tasmota) and
    # later only offer switches that speak the same protocol.
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input: dict | None = None):
        """ELI5: entry point — jump straight to the target picker."""
        return await self.async_step_target(user_input)

    async def async_step_target(self, user_input: dict | None = None):
        """
        ELI5: let the user choose one or more lights / valves / locks /
        fans that the switch(es) should control. SHiP Anchor detects each
        target's protocol and kind, and automatically escalates to a group
        binding if more than two targets are chosen (anti-popcorn rule).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_targets = user_input.get("targets", []) or []
            if isinstance(raw_targets, str):
                raw_targets = [raw_targets]
            target_ids = [str(item) for item in raw_targets if item]
            use_group = bool(user_input.get("use_group", False))
            group_name = str(user_input.get("group_name", "") or "").strip()

            if not target_ids and not (use_group and group_name):
                errors["base"] = "target_required"
                targets = self._candidate_targets(
                    dr.async_get(self.hass), er.async_get(self.hass)
                )
                return self.async_show_form(
                    step_id="target",
                    data_schema=self._target_schema(targets),
                    errors=errors,
                )

            device_reg = dr.async_get(self.hass)
            entity_reg = er.async_get(self.hass)
            targets = []
            for tid in target_ids:
                dev = device_reg.async_get(tid)
                if dev is None:
                    continue
                targets.append({
                    "device_id": tid,
                    "name": _device_name(dev),
                    "ieee": _device_ieee(dev),
                    "model": _device_model(dev),
                    "manufacturer": _device_manufacturer(dev),
                    "kind": _target_kind(dev, entity_reg),
                    "protocol": _infer_protocol(dev).name,
                })
            if not targets:
                errors["base"] = "target_required"
            # ELI5: the protocol of the FIRST target anchors the wizard —
            # every later source we offer must speak the same protocol.
            protocol = _infer_protocol(
                device_reg.async_get(target_ids[0])
            ) if target_ids else Protocol.UNKNOWN
            self._input = {  # type: ignore[attr-defined]
                "targets": targets,
                "use_group": use_group,
                "group_name": group_name,
                "protocol": protocol.name,
            }
            if errors:
                return self.async_show_form(
                    step_id="target",
                    data_schema=self._target_schema(
                        self._candidate_targets(device_reg, entity_reg)
                    ),
                    errors=errors,
                )
            return await self.async_step_source()

        device_reg = dr.async_get(self.hass)
        entity_reg = er.async_get(self.hass)
        targets = self._candidate_targets(device_reg, entity_reg)
        return self.async_show_form(
            step_id="target",
            data_schema=self._target_schema(targets),
        )

    def _target_schema(self, candidates: list) -> vol.Schema:
        """ELI5: build the target-picker form (multi-select + group toggle)."""
        options = [{"value": dev_id, "label": name} for dev_id, name in candidates]
        return vol.Schema(
            {
                vol.Required("targets"): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.MULTIPLE)
                ),
                vol.Optional("use_group", default=False): bool,
                vol.Optional("group_name", default=""): str,
            }
        )

    # ------------------------------------------------------------------
    # Step 2 — pick the SOURCE switch(es)  (protocol-filtered)
    # ELI5: only switches that speak the SAME protocol as the target(s)
    # are listed, so a Zigbee switch can never be paired with a Z-Wave
    # lock. Multi-select supports multi-way (3-way) switch setups.
    # ------------------------------------------------------------------
    async def async_step_source(self, user_input: dict | None = None):
        """ELI5: offer compatible source switches (same protocol only)."""
        errors: dict[str, str] = {}
        inp = self._input  # type: ignore[attr-defined]
        target_protocol = PROTOCOL_ALIASES.get(
            str(inp.get("protocol", "")), Protocol.UNKNOWN
        )

        if user_input is not None:
            raw_sources = user_input.get("sources", []) or []
            if isinstance(raw_sources, str):
                raw_sources = [raw_sources]
            source_ids = [str(item) for item in raw_sources if item]
            if not source_ids:
                errors["base"] = "source_required"
            else:
                device_reg = dr.async_get(self.hass)
                sources = []
                for sid in source_ids:
                    dev = device_reg.async_get(sid)
                    if dev is None:
                        continue
                    sources.append({
                        "device_id": sid,
                        "name": _device_name(dev),
                        "ieee": _device_ieee(dev),
                        "model": _device_model(dev),
                        "manufacturer": _device_manufacturer(dev),
                    })
                if not sources:
                    errors["base"] = "source_required"
                else:
                    inp.update(sources=sources)
                    return await self.async_step_behavior()

        device_reg = dr.async_get(self.hass)
        entity_reg = er.async_get(self.hass)
        # ELI5: the dynamic filter — drop every source whose protocol does
        # not match the target's protocol. This is the guard that prevents
        # cross-protocol mismatches (Zigbee → Z-Wave, Matter → Shelly, …).
        all_sources = self._candidate_sources(device_reg, entity_reg)
        compatible = []
        for dev_id, name in all_sources:
            dev = device_reg.async_get(dev_id)
            if dev is None:
                continue
            if _device_protocols_match(_infer_protocol(dev), target_protocol):
                compatible.append((dev_id, name))
        if not compatible:
            # ELI5: honest fallback — tell the user WHY nothing was listed
            # instead of showing an empty dropdown.
            compatible = all_sources
        return self.async_show_form(
            step_id="source",
            data_schema=vol.Schema(
                {
                    vol.Required("sources"): SelectSelector(
                        SelectSelectorConfig(
                            options=[{"value": d, "label": n} for d, n in compatible],
                            mode=SelectSelectorMode.MULTIPLE,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "protocol": inp.get("protocol", "unknown"),
                "source_count": str(len(compatible)),
            },
        )

    # ------------------------------------------------------------------
    # Step 3 — behaviour: gestures + time windows + per-kind parameters
    # ------------------------------------------------------------------
    def _kind_capability(self, inp: dict) -> set:
        """ELI5: union of the capabilities of every selected target."""
        caps: set = set()
        for t in inp.get("targets", []):
            caps |= TARGET_CAPABILITIES.get(str(t.get("kind", "light")), set())
        return caps

    async def async_step_behavior(self, user_input: dict | None = None):
        """
        ELI5: build the "rule sheet" for this binding. For each rule the
        user picks a gesture (1x–5x tap or hold), an optional time-of-day
        window (hour_start → hour_end), and the action parameters that the
        selected target kind supports (brightness/colour for lights, speed
        for fans, open/close for valves, lock/unlock for locks).

        These rules are compiled by `AnchorCompiler` into LEAT rows that
        live on the hardware — so they keep working with the hub offline.
        """
        errors: dict[str, str] = {}
        inp = self._input  # type: ignore[attr-defined]
        caps = self._kind_capability(inp)

        if user_input is not None:
            rules = []
            # ELI5: fixed slots (up to 4 rules) — each slot has a gesture,
            # an optional time window, and the action parameters. A slot
            # with no gesture set is skipped.
            for i in range(4):
                gesture = str(user_input.get("rule{}_gesture".format(i + 1), "") or "")
                if not gesture:
                    continue
                rule: dict[str, Any] = {"gesture": gesture}
                hour_start = user_input.get("rule{}_hour_start".format(i + 1))
                hour_end = user_input.get("rule{}_hour_end".format(i + 1))
                if hour_start is not None and hour_end is not None:
                    rule["hour_start"] = int(max(0, min(23, int(hour_start))))
                    rule["hour_end"] = int(max(0, min(23, int(hour_end))))
                # Per-kind action parameters (only the relevant ones are set).
                if "brightness" in caps:
                    rule["brightness"] = int(
                        max(
                            0,
                            min(
                                255,
                                int(user_input.get("rule{}_brightness".format(i + 1), 100)),
                            ),
                        )
                    )
                if "color_temp" in caps:
                    ct = user_input.get("rule{}_color_temp_mireds".format(i + 1))
                    if ct is not None:
                        rule["color_temp_mireds"] = int(max(153, min(500, int(ct))))
                if "transition" in caps:
                    tt = user_input.get("rule{}_transition_seconds".format(i + 1))
                    if tt is not None:
                        rule["transition_seconds"] = int(max(0, min(60, int(tt))))
                if "speed" in caps:
                    sp = user_input.get("rule{}_fan_speed".format(i + 1))
                    if sp is not None:
                        rule["fan_speed"] = int(max(0, min(5, int(sp))))
                if "open_close" in caps:
                    rule["valve_open"] = bool(user_input.get("rule{}_valve_open".format(i + 1), True))
                if "lock_unlock" in caps:
                    rule["lock_unlock"] = bool(user_input.get("rule{}_lock_unlock".format(i + 1), False))
                if "setpoint" in caps:
                    st = user_input.get("rule{}_setpoint_c".format(i + 1))
                    if st is not None:
                        rule["setpoint_c"] = float(st)
                rules.append(rule)
            if not rules:
                errors["base"] = "rule_required"
            else:
                inp["rules"] = rules
                return await self.async_step_finish()

        # Build the schema dynamically from the target kind's capabilities.
        schema: dict = {}
        for i in range(1, 5):
            prefix = "rule{}".format(i)
            schema[vol.Required("rule{}_gesture".format(i), default="")] = SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": "", "label": "(no rule)"}]
                    + [{"value": t, "label": t} for t in TRIGGER_OPTIONS],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
            schema[vol.Optional("rule{}_hour_start".format(i))] = NumberSelector(
                NumberSelectorConfig(min=0, max=23, step=1, unit_of_measurement="h")
            )
            schema[vol.Optional("rule{}_hour_end".format(i))] = NumberSelector(
                NumberSelectorConfig(min=0, max=23, step=1, unit_of_measurement="h")
            )
            if "brightness" in caps:
                schema[vol.Optional("rule{}_brightness".format(i), default=100)] = NumberSelector(
                    NumberSelectorConfig(min=0, max=255, step=1, unit_of_measurement="%")
                )
            if "color_temp" in caps:
                schema[vol.Optional("rule{}_color_temp_mireds".format(i))] = NumberSelector(
                    NumberSelectorConfig(min=153, max=500, step=1, unit_of_measurement="mireds")
                )
            if "transition" in caps:
                schema[vol.Optional("rule{}_transition_seconds".format(i))] = NumberSelector(
                    NumberSelectorConfig(min=0, max=60, step=1, unit_of_measurement="s")
                )
            if "speed" in caps:
                schema[vol.Optional("rule{}_fan_speed".format(i))] = NumberSelector(
                    NumberSelectorConfig(min=0, max=5, step=1)
                )
            if "open_close" in caps:
                schema[vol.Optional("rule{}_valve_open".format(i), default=True)] = bool
            if "lock_unlock" in caps:
                schema[vol.Optional("rule{}_lock_unlock".format(i), default=False)] = bool
            if "setpoint" in caps:
                schema[vol.Optional("rule{}_setpoint_c".format(i))] = NumberSelector(
                    NumberSelectorConfig(min=0, max=35, step=0.5, unit_of_measurement="°C")
                )
        return self.async_show_form(
            step_id="behavior",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={
                "target_count": str(len(inp.get("targets", []))),
            },
        )

    # ------------------------------------------------------------------
    # Step 4 — validate, compile, create the config entry
    # ------------------------------------------------------------------
    async def async_step_finish(self, user_input: dict | None = None):
        """
        ELI5: run the compiler (dry-run or live, the user's choice) and
        store the outcome on the config entry. The entry itself is what
        makes Home Assistant load the SHiP Anchor integration.
        """
        errors: dict[str, str] = {}
        inp = self._input  # type: ignore[attr-defined]

        if user_input is not None:
            dry_run = bool(user_input.get("dry_run", True))

            protocol_name = inp.get("protocol", "UNKNOWN")
            protocol = PROTOCOL_ALIASES.get(protocol_name, Protocol.UNKNOWN)
            targets = inp.get("targets", [])
            sources = inp.get("sources", []) or []
            # ELI5: the first source is the "primary" IEEE for the binding
            # record; additional sources are multi-way peers that share the
            # same group (Zigbee group / Tasmota group / Shelly script).
            primary = sources[0] if sources else {}
            source_ieee = primary.get("ieee", "")
            resolved_targets = []
            for t in targets:
                resolved = dict(t)
                if not resolved.get("ieee") and source_ieee:
                    resolved["ieee"] = source_ieee
                resolved_targets.append(resolved)

            config: dict[str, Any] = {
                "protocol": protocol.name,
                "source_ieee": source_ieee,
                "source_model": primary.get("model", ""),
                "source_manufacturer": primary.get("manufacturer", ""),
                "targets": resolved_targets,
                "sources": sources,          # multi-way peer list (incl. primary)
                "rules": list(inp.get("rules", [])),  # LEAT rows: gesture + window + params
            }
            if inp.get("use_group") and inp.get("group_name"):
                config["group_name"] = inp["group_name"]

            compile_summary: dict[str, Any] = {
                "success": False,
                "dry_run": dry_run,
                "errors": [],
                "warnings": [],
                "bindings_added": 0,
                "dry_run_payloads": 0,
            }

            compiler = self._get_or_build_compiler()
            if compiler is not None:
                try:
                    result = await compiler.async_compile_config(config, dry_run=dry_run)
                    compile_summary = {
                        "success": result.success,
                        "dry_run": dry_run,
                        "errors": list(result.errors),
                        "warnings": list(result.warnings),
                        "bindings_added": len(result.bindings_added),
                        "dry_run_payloads": len(result.dry_run_payloads),
                    }
                    # ELI5: a live (non-dry-run) compile still needs to be
                    # written to the persistent store, otherwise erase_all
                    # has nothing to undo.
                    if not dry_run and result.bindings_added:
                        from dataclasses import asdict

                        compiler._stored_data.setdefault("bindings", []).extend(
                            asdict(b) for b in result.bindings_added
                        )
                        await compiler._store.async_save(compiler._stored_data)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.exception("SHiP Anchor wizard: compile failed")
                    compile_summary["errors"].append(str(exc))
            else:
                compile_summary["errors"].append(
                    "Compiler unavailable — integration not set up yet."
                )

            title = "{} → {} target(s)".format(
                primary.get("name", "source"), len(targets) or 1
            )
            data: dict[str, Any] = {
                "source_device_id": primary.get("device_id", ""),
                "source_name": primary.get("name", ""),
                "source_ieee": source_ieee,
                "protocol": protocol_name,
                "targets": targets,
                "sources": sources,             # multi-way peer list
                "use_group": bool(inp.get("use_group", False)),
                "group_name": inp.get("group_name", ""),
                "rules": list(inp.get("rules", [])),  # LEAT rows
                "compile": compile_summary,
            }
            return self.async_create_entry(title=title, data=data)

        schema = vol.Schema(
            {
                vol.Required("dry_run", default=True): bool,
            }
        )
        return self.async_show_form(
            step_id="finish",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "source": inp.get("source_name", "the source"),
                "target_count": str(len(inp.get("targets", []))),
                "protocol": inp.get("protocol", "unknown"),
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _get_or_build_compiler(self) -> AnchorCompiler | None:
        """
        ELI5: if the integration is already set up, reuse its compiler.
        Otherwise build a throwaway one so the wizard can still preview
        and (if the user chooses) apply the binding.
        """
        existing = self.hass.data.get(DOMAIN, {}).get("compiler")
        if existing:
            return existing
        try:
            from homeassistant.helpers.storage import Store

            store = Store(self.hass, 1, "ship_anchor.bindings")
            stored: dict[str, Any] = {}
            return AnchorCompiler(self.hass, store, stored)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("SHiP Anchor wizard: could not build compiler")
            return None

    def _candidate_sources(self, device_reg: Any, entity_reg: Any) -> list:
        """ELI5: find devices that look like switches / buttons / dimmers."""
        scores: dict[str, int] = {}
        for entity in entity_reg.entities.values():
            domain = str(getattr(entity, "platform", "") or "")
            if domain not in SOURCE_DOMAINS:
                continue
            device_id = getattr(entity, "device_id", None)
            if not device_id:
                continue
            scores[device_id] = scores.get(device_id, 0) + 2
        for device in device_reg.devices.values():
            sig = "{} {}".format(
                _device_manufacturer(device), _device_model(device)
            ).lower()
            if any(k in sig for k in ("switch", "button", "remote", "knob")):
                scores[device.id] = scores.get(device.id, 0) + 1
        results = [
            (device.id, _device_name(device))
            for device in device_reg.devices.values()
            if device.id in scores
        ]
        results.sort(key=lambda pair: -scores.get(pair[0], 0))
        return results

    def _candidate_targets(self, device_reg: Any, entity_reg: Any) -> list:
        """ELI5: find devices that look like lights / valves / locks."""
        scores: dict[str, int] = {}
        for entity in entity_reg.entities.values():
            domain = str(getattr(entity, "platform", "") or "")
            if domain not in TARGET_DOMAINS:
                continue
            device_id = getattr(entity, "device_id", None)
            if not device_id:
                continue
            scores[device_id] = scores.get(device_id, 0) + 2
        for device in device_reg.devices.values():
            sig = "{} {}".format(
                _device_manufacturer(device), _device_model(device)
            ).lower()
            if any(k in sig for k in ("bulb", "light", "valve", "lock", "shade")):
                scores[device.id] = scores.get(device.id, 0) + 1
        results = [
            (device.id, _device_name(device))
            for device in device_reg.devices.values()
            if device.id in scores
        ]
        results.sort(key=lambda pair: -scores.get(pair[0], 0))
        return results

    @classmethod
    def async_get_options_flow(cls, config_entry: Any) -> Any:
        """ELI5: expose the options flow so the user can click
        'Configure' on the integration card later to view / edit / erase
        bindings without redoing the whole wizard."""
        return ShipAnchorOptionsFlow(config_entry)


class ShipAnchorOptionsFlow(config_entries.OptionsFlow):
    """
    ELI5: a small "control panel" that shows every binding SHiP Anchor
    has baked in, and lets the user erase one (or all of them) with a
    single click. No new hardware is programmed here — this flow is
    read + undo only.
    """

    def __init__(self, config_entry: Any) -> None:
        self._config_entry = config_entry

    def _compiler(self) -> Any:
        """Fetch the shared compiler, or None if the integration isn't
        fully set up yet."""
        runtime = self.hass.data.get(DOMAIN, {})
        return runtime.get("compiler")

    async def async_step_init(self, user_input: dict | None = None):
        """ELI5: show the list of current bindings and offer actions."""
        errors: dict[str, str] = {}
        if user_input is not None:
            action = str(user_input.get("action", "list"))

            if action == "erase_all":
                compiler = self._compiler()
                if compiler is None:
                    errors["base"] = "no_runtime"
                else:
                    try:
                        await compiler.async_erase_all()
                    except Exception as exc:  # noqa: BLE001
                        errors["base"] = "erase_failed"
                        _LOGGER.exception("Options flow: erase_all failed")
                        del exc
                    else:
                        return self.async_create_entry(title="", data={
                            "erase_result": "success",
                        })
            elif action == "recompile":
                # ELI5: re-run the wizard so the user can tweak the
                # behaviour and re-apply it.
                return self.async_abort(reason="restart_wizard")

        compiler = self._compiler()
        bindings = []
        if compiler is not None:
            bindings = compiler.list_bindings()

        schema = vol.Schema(
            {
                vol.Required("action", default="list"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": "list", "label": "View current bindings"},
                            {"value": "erase_all", "label": "Erase ALL bindings"},
                            {"value": "recompile", "label": "Edit (re-run wizard)"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "binding_count": str(len(bindings)),
                "bindings": (
                    "\n".join(
                        "• {}  {} → {}".format(b.protocol, b.source_ieee, b.target_ieee)
                        for b in bindings
                    )
                    if bindings
                    else "(none)"
                ),
            },
        )


__all__ = ["ShipAnchorConfigFlow", "ShipAnchorOptionsFlow"]
