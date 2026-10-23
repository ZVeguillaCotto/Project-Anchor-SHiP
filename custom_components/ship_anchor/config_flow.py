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
    # Step 1 — choose a DIRECTION  (v0.1.14: bidirectional wizard)
    # ELI5: the wizard now lets the user start from EITHER end of the chain.
    #   • "target" — I already know what I want to control (the light /
    #     fan / valve / lock).
    #   • "source" — I already know which switch I want to use.
    # Whichever way the user starts, the wizard then offers the OTHER end
    # filtered by protocol, so a Zigbee switch can never be paired with a
    # Z-Wave lock.
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input: dict | None = None):
        """ELI5: the entry step — ask the user which end to start from."""
        errors: dict[str, str] = {}
        if user_input is not None:
            direction = str(user_input.get("direction", "") or "").strip()
            if direction not in ("source", "target"):
                errors["base"] = "direction_required"
                user_input = None
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(
                    {
                        vol.Required("direction"): SelectSelector(
                            SelectSelectorConfig(
                                options=[
                                    {"value": "target", "label": "I want to control a device (target-first)"},
                                    {"value": "source", "label": "I have a switch / button (source-first)"},
                                ],
                                mode=SelectSelectorMode.DROPDOWN,
                            )
                        )
                    }
                ),
                errors=errors,
            )
        self._input = {"direction": direction}  # type: ignore[attr-defined]
        if direction == "source":
            return await self.async_step_anchor("source")
        return await self.async_step_anchor("target")

    # ------------------------------------------------------------------
    # Step 2 — anchor the FIRST device (bidirectional)
    # ELI5: whether the user chose "source-first" or "target-first", this
    # step collects the device(s) on that end and records the protocol they
    # speak. The next step (the OTHER end) is filtered by that protocol, so
    # a cross-bus pairing can never slip through.
    # ------------------------------------------------------------------
    async def async_step_anchor(self, side: str, user_input: dict | None = None):
        """
        ELI5: collect the anchor-side device(s). ``side`` is either
        "source" (switches / buttons) or "target" (lights / valves / …).
        The step is direction-agnostic so the wizard can drive it from
        EITHER end of the chain.
        """
        errors: dict[str, str] = {}
        key = "sources" if side == "source" else "targets"
        label = "Source device(s)" if side == "source" else "Target device(s)"
        if self._input is None:
            self._input = {}  # type: ignore[attr-defined]

        if user_input is not None:
            raw_ids = user_input.get(key, []) or []
            if isinstance(raw_ids, str):
                raw_ids = [raw_ids]
            device_ids = [str(item) for item in raw_ids if item]
            use_group = bool(user_input.get("use_group", False))
            group_name = str(user_input.get("group_name", "") or "").strip()

            device_reg = dr.async_get(self.hass)
            entity_reg = er.async_get(self.hass)
            anchor_entries = []
            for device_id in device_ids:
                dev = device_reg.async_get(device_id)
                if dev is None:
                    continue
                entry: dict[str, Any] = {
                    "device_id": device_id,
                    "name": _device_name(dev),
                    "ieee": _device_ieee(dev),
                    "model": _device_model(dev),
                    "manufacturer": _device_manufacturer(dev),
                    "identifiers": [
                        [str(namespace), str(value)]
                        for namespace, value in (dev.identifiers or ())
                    ],
                }
                if side == "target":
                    entry["kind"] = _target_kind(dev, entity_reg)
                    entry["protocol"] = _infer_protocol(dev).name
                anchor_entries.append(entry)

            if not anchor_entries:
                errors["base"] = "source_required" if side == "source" else "target_required"
            else:
                # ELI5: the protocol of the FIRST anchor device becomes the
                # wizard's anchor protocol — every later device (the other
                # end, plus any canopy module) is filtered against it.
                protocol = _infer_protocol(
                    device_reg.async_get(device_ids[0])
                ) if device_ids else Protocol.UNKNOWN
                self._input.update(  # type: ignore[union-attr]
                    **{key: anchor_entries},
                    use_group=use_group,
                    group_name=group_name,
                    protocol=protocol.name,
                )

            if errors:
                candidates = self._candidates_for_side(
                    side, device_reg, entity_reg, protocol
                )
                return self.async_show_form(
                    step_id=side,
                    data_schema=self._anchor_schema(key, label, candidates),
                    errors=errors,
                    description_placeholders={
                        "protocol": protocol.name,
                        "side": label,
                        "count": str(len(candidates)),
                    },
                )

            # ELI5: now that both ends of the chain are known, go to the
            # behaviour step. If only one end is collected yet, walk to the
            # OTHER end (filtered by protocol) — source → target OR
            # target → source.
            have_sources = bool(self._input.get("sources"))  # type: ignore[union-attr]
            have_targets = bool(self._input.get("targets"))  # type: ignore[union-attr]
            if have_sources and have_targets:
                return await self.async_step_behavior()
            other_side = "target" if side == "source" else "source"
            return await self.async_step_anchor(other_side)

        # Show the picker for this side, filtered by the anchor protocol.
        device_reg = dr.async_get(self.hass)
        entity_reg = er.async_get(self.hass)
        anchor_protocol = PROTOCOL_ALIASES.get(
            str(self._input.get("protocol", "")), Protocol.UNKNOWN
        )
        candidates = self._candidates_for_side(
            side, device_reg, entity_reg, anchor_protocol
        )
        return self.async_show_form(
            step_id=side,
            data_schema=self._anchor_schema(key, label, candidates),
            description_placeholders={
                "protocol": anchor_protocol.name,
                "side": label,
                "count": str(len(candidates)),
            },
        )

    def _anchor_schema(self, key: str, label: str, candidates: list) -> vol.Schema:
        """ELI5: build the anchor picker form (multi-select + group toggle)."""
        options = [{"value": dev_id, "label": name} for dev_id, name in candidates]
        return vol.Schema(
            {
                vol.Required(key): SelectSelector(
                    SelectSelectorConfig(
                        options=options, mode=SelectSelectorMode.MULTIPLE
                    )
                ),
                vol.Optional("use_group", default=False): bool,
                vol.Optional("group_name", default=""): str,
            }
        )

    def _candidates_for_side(
        self,
        side: str,
        device_reg: Any,
        entity_reg: Any,
        anchor_protocol: Protocol,
    ) -> list:
        """
        ELI5: return the picker's device list for one side of the wizard,
        filtered by the anchor protocol so a cross-bus pairing can never be
        offered. When no compatible device exists we fall back to the full
        list and let the description placeholder explain why.
        """
        if side == "source":
            all_candidates = self._candidate_sources(device_reg, entity_reg)
        else:
            all_candidates = self._candidate_targets(device_reg, entity_reg)
        if anchor_protocol == Protocol.UNKNOWN:
            return all_candidates
        compatible = []
        for dev_id, name in all_candidates:
            dev = device_reg.async_get(dev_id)
            if dev is None:
                continue
            if _device_protocols_match(_infer_protocol(dev), anchor_protocol):
                compatible.append((dev_id, name))
        return compatible if compatible else all_candidates

    # Back-compat aliases — the wizard used to have separate "target" and
    # "source" steps; those now both funnel through ``async_step_anchor``.
    # Keeping the names around so any external code (tests, MCP bridges)
    # that calls ``flow.async_step_target(...)`` keeps working unchanged.
    async def async_step_target(self, user_input: dict | None = None):
        return await self.async_step_anchor("target", user_input)

    async def async_step_source(self, user_input: dict | None = None):
        return await self.async_step_anchor("source", user_input)

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
                # ELI5: after the rule sheet, ask (optionally) about an
                # intermediate canopy / relay module before the final
                # preview step.
                return await self.async_step_canopy()

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
    # Step 4 — optional INTERMEDIATE CANOPY / RELAY  (v0.1.14)
    # ELI5: some rooms route a switch's signal through an intermediate
    # "canopy" module (a ceiling-fan canopy, a relay box, a mesh repeater)
    # before the action reaches the actual load (bulb, fan, valve). If the
    # user has one, they pick it here and the compiler will build a
    # two-hop chain:  source → canopy → target.
    #
    # This step is OPTIONAL. Leaving the picker empty skips the canopy and
    # the binding stays a plain one-hop source → target (exactly the
    # v0.1.13 behaviour).
    # ------------------------------------------------------------------
    async def async_step_canopy(self, user_input: dict | None = None):
        """ELI5: offer an optional intermediate relay / canopy module."""
        errors: dict[str, str] = {}
        inp = self._input  # type: ignore[attr-defined]
        anchor_protocol = PROTOCOL_ALIASES.get(
            str(inp.get("protocol", "")), Protocol.UNKNOWN
        )
        device_reg = dr.async_get(self.hass)
        entity_reg = er.async_get(self.hass)

        # ELI5: a canopy is any device that is NOT the source and NOT the
        # target — typically a relay, repeater, or a second switch on the
        # same bus. We list every protocol-compatible device and let the
        # user pick one (or none).
        all_candidates = self._candidates_for_side(
            "source", device_reg, entity_reg, anchor_protocol
        )
        used_ids = {str(s.get("device_id")) for s in inp.get("sources", []) or []}
        used_ids |= {str(t.get("device_id")) for t in inp.get("targets", []) or []}
        candidates = [
            (dev_id, name)
            for dev_id, name in all_candidates
            if dev_id not in used_ids
        ]

        if user_input is not None:
            raw_canopy = user_input.get("canopy", "") or ""
            if isinstance(raw_canopy, (list, tuple)):
                raw_canopy = raw_canopy[0] if raw_canopy else ""
            canopy_id = str(raw_canopy or "").strip()
            if not canopy_id or canopy_id == "none":
                inp["canopy"] = None
            else:
                dev = device_reg.async_get(canopy_id)
                if dev is None:
                    errors["base"] = "canopy_not_found"
                else:
                    inp["canopy"] = {
                        "device_id": canopy_id,
                        "name": _device_name(dev),
                        "ieee": _device_ieee(dev),
                        "model": _device_model(dev),
                        "manufacturer": _device_manufacturer(dev),
                        "identifiers": [
                            [str(namespace), str(value)]
                            for namespace, value in (dev.identifiers or ())
                        ],
                    }
            if errors:
                return self.async_show_form(
                    step_id="canopy",
                    data_schema=self._canopy_schema(candidates),
                    errors=errors,
                )
            return await self.async_step_finish()

        return self.async_show_form(
            step_id="canopy",
            data_schema=self._canopy_schema(candidates),
            description_placeholders={
                "protocol": anchor_protocol.name,
                "canopy_count": str(len(candidates)),
            },
        )

    def _canopy_schema(self, candidates: list) -> vol.Schema:
        """ELI5: the canopy picker — a single-select dropdown with an
        explicit "(no intermediate module)" entry at the top."""
        options = [
            {"value": "", "label": "(no intermediate module — direct binding)"}
        ] + [
            {"value": dev_id, "label": name} for dev_id, name in candidates
        ]
        return vol.Schema(
            {
                vol.Required("canopy", default=""): SelectSelector(
                    SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                ),
            }
        )

    # ------------------------------------------------------------------
    # Step 5 — validate, compile, create the config entry
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

            # ELI5 (v0.1.14): if the user picked an intermediate canopy /
            # relay, build a two-hop chain (source → canopy → target) and
            # compile it with the multi-hop compiler. When there is no
            # canopy, fall back to the plain one-hop compile (v0.1.13).
            canopy = inp.get("canopy") or None
            chain: list[dict] | None = None
            if canopy and primary:
                chain = [dict(primary), dict(canopy)]
                for t in resolved_targets:
                    chain.append(dict(t))

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
                    if chain:
                        result = await compiler.async_compile_multihop(
                            chain=chain,
                            rules=list(inp.get("rules", [])),
                            dry_run=dry_run,
                            group_name=str(inp.get("group_name", "") or ""),
                        )
                    else:
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

            canopy = inp.get("canopy") or None
            if canopy:
                title = "{} → {} → {} target(s)".format(
                    primary.get("name", "source"),
                    canopy.get("name", "canopy"),
                    len(targets) or 1,
                )
            else:
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
                "canopy": canopy,              # v0.1.14 — intermediate relay (or None)
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
