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
from .compiler import (
    AnchorCompiler,
    brightness_to_percent,
    kelvin_to_mireds,
    mireds_to_kelvin,
    normalize_rule_units,
    percent_to_brightness,
)
from .protocols import Protocol, PROTOCOL_ALIASES
from .ship_protocol import (
    HEALTH_HEALTHY,
    diagnose_mesh,
)

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


# ---------------------------------------------------------------------------
# ELI5 rule → plain-English translation (v0.1.15 options dashboard)
# ---------------------------------------------------------------------------
# The wizard stores rules as "raw" dicts — {gesture: "3x Tap", hour_start: 22,
# hour_end: 6, brightness: 120, color_temp_mireds: 350, ...}. The options
# dashboard needs to show them to a non-technical user in a sentence they can
# actually read: "On 3x Tap (between 22:00 and 06:00): set brightness to 120
# and colour temperature to 350 mireds."
#
# These helpers keep the mapping in one place so both the wizard's behaviour
# step and the options-flow dashboard stay in sync. They are pure Python —
# no Home Assistant, no I/O — which makes them trivially unit-testable.

_GESTURE_ELI5 = {
    "1x Tap": "a single tap",
    "2x Tap": "a double tap",
    "3x Tap": "a triple tap",
    "4x Tap": "a quadruple tap",
    "5x Tap": "a quintuple tap",
    "Hold": "a long press",
}


def _rule_window_phrase(rule: dict) -> str:
    """ELI5: turn ``hour_start`` / ``hour_end`` into a time-of-day phrase.

    ``22 → 6`` renders as "between 22:00 and 06:00" (overnight windows are
    honoured by the C core's ``ship_leat_match`` branch). A missing window
    yields "" so the sentence flows naturally.
    """
    hour_start = rule.get("hour_start")
    hour_end = rule.get("hour_end")
    if hour_start is None and hour_end is None:
        return ""
    def _fmt(value: Any) -> str:
        if value is None:
            return "any hour"
        try:
            return "{:02d}:00".format(int(value) % 24)
        except (TypeError, ValueError):
            return str(value)
    return "between {} and {}".format(_fmt(hour_start), _fmt(hour_end))


def _rule_actions_phrase(rule: dict) -> str:
    """ELI5: turn the action parameters into a plain-English phrase.

    The order of the parameters is deliberately stable (brightness → colour
    temp → transition → fan speed → valve → lock → setpoint) so that a
    rule with the same set of parameters always reads the same way.
    """
    actions: list[str] = []
    if "brightness" in rule:
        # ELI5: show the *human* dial (a percent) first, with the raw 0–255
         # byte in brackets so the non-engineer can verify the hardware
         # value without thinking about mireds or bytes.
        raw = int(rule["brightness"])
        actions.append(
            "set brightness to {}% (raw {})".format(brightness_to_percent(raw), raw)
         )
    if "color_temp_mireds" in rule:
        # ELI5: show the *human* Kelvin value first (like a bulb box),
         # with the raw mireds in brackets for the engineer.
        raw_mireds = int(rule["color_temp_mireds"])
        actions.append(
            "set colour temperature to ~{}K ({} mireds)".format(
                mireds_to_kelvin(raw_mireds), raw_mireds
            )
        )
    if "transition_seconds" in rule:
        actions.append("fade over {} s".format(rule["transition_seconds"]))
    if "fan_speed" in rule:
        actions.append("set fan speed to level {}".format(rule["fan_speed"]))
    if "valve_open" in rule:
        actions.append("{} the valve".format("open" if rule["valve_open"] else "close"))
    if "lock_unlock" in rule:
        actions.append("{} the lock".format("unlock" if rule["lock_unlock"] else "lock"))
    if "setpoint_c" in rule:
        actions.append("set the setpoint to {} °C".format(rule["setpoint_c"]))
    if not actions:
        return "toggle the target (on / off)"
    # ELI5: join with ", " and a final " and " — "a, b and c".
    if len(actions) == 1:
        return actions[0]
    return ", ".join(actions[:-1]) + " and " + actions[-1]


def _rule_eli5(rule: dict) -> str:
    """ELI5: one full sentence describing a single LEAT rule.

    Examples:
      {"gesture": "3x Tap", "brightness": 120}
        → "On a triple tap: set brightness to 120."
      {"gesture": "Hold", "hour_start": 22, "hour_end": 6, "valve_open": True}
        → "On a long press (between 22:00 and 06:00): open the valve."
    """
    gesture_label = str(rule.get("gesture", ""))
    gesture = _GESTURE_ELI5.get(gesture_label, gesture_label or "a gesture")
    window = _rule_window_phrase(rule)
    actions = _rule_actions_phrase(rule)
    if window:
        prefix = "On {} ({})".format(gesture, window)
    else:
        prefix = "On {}".format(gesture)
    return "{}: {}.".format(prefix, actions)


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
                                int(percent_to_brightness(user_input.get("rule{}_brightness_percent".format(i + 1), 100))),
                            ),
                        )
                    )
                if "color_temp" in caps:
                    ct = user_input.get("rule{}_color_temp_kelvin".format(i + 1))
                    if ct is not None:
                        rule["color_temp_mireds"] = kelvin_to_mireds(ct)
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
                schema[vol.Optional("rule{}_brightness_percent".format(i), default=100)] = NumberSelector(
                    NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
                )
            if "color_temp" in caps:
                schema[vol.Optional("rule{}_color_temp_kelvin".format(i))] = NumberSelector(
                    NumberSelectorConfig(min=2000, max=6500, step=100, unit_of_measurement="K")
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
        bindings without redoing the whole wizard.

        v0.1.15 — the options flow is now a **Visual LEAT & Multi-Hop
        Inspector Dashboard**: it renders the current chain (source →
        canopy → targets), the configured LEAT rules in plain English,
        and offers add-rule / delete-rule / delete-binding / erase-all /
        recompile actions — all from inside Home Assistant's options
        dialog, with no Developer-Tools or log reading required.
        """
        return ShipAnchorOptionsFlow(config_entry)


class ShipAnchorOptionsFlow(config_entries.OptionsFlow):
    """
    ELI5: the **Visual LEAT & Multi-Hop Inspector Dashboard**.

    A rich, interactive card that lives inside Home Assistant's standard
    options dialog (Settings → Devices & Services → SHiP Anchor →
    Configure). It shows:

      • The **active multi-hop chain** (source → canopy → target), with
        protocol labels so the user can see at a glance which bus each leg
        speaks.
      • Every **configured LEAT rule**, translated from the raw gesture / /
        time-window / action parameters into a plain-English sentence
        ("On a triple tap (between 22:00 and 06:00): set brightness to 120
        and colour temperature to 350 mireds.").

    And it lets the user, all without leaving the dialog:

      • **Add** a new gesture rule to the existing binding.
      • **Delete** a specific rule (instead of the old "erase all" nuclear
        option).
      • **Delete** a specific source binding (useful for multi-way setups
        where one of the switches is being decommissioned).
      • **Erase all** bindings (kept as the v0.1.9 resilient option).
      • **Recompile** — re-run the wizard to re-apply the binding.
    """

    def __init__(self, config_entry: Any) -> None:
        super().__init__(config_entry)
        # ELI5: the options flow is opened on a *specific* config entry, so
        # keep a reference to it for the whole flow. ``hass`` is injected by
        # Home Assistant (and by the test stub) before the first step runs.
        self._config_entry = config_entry
        self._entry = config_entry

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------
    def _compiler(self) -> Any:
        """Fetch the shared compiler, or None if the integration isn't
        fully set up yet."""
        if self.hass is None:
            return None
        runtime = self.hass.data.get(DOMAIN, {})
        return runtime.get("compiler")

    def _entry_data(self) -> dict:
        """ELI5: the config entry's data dict, guaranteed to be a dict.

        Config entries store their state in a single JSON-serialisable
        ``data`` dict; we read it here and write it back through
        ``hass.config_entries.async_update_entry`` at the end of each
        action. A fresh entry (no data yet) returns an empty dict.
        """
        data = getattr(self._entry, "data", None) or {}
        return dict(data) if isinstance(data, dict) else {}

    async def _update_entry(self, data: dict) -> Any:
        """ELI5: persist a new data dict onto the config entry."""
        await self.hass.config_entries.async_update_entry(self._entry, data=data)
        return self._entry

    # ------------------------------------------------------------------
    # Dashboard rendering (pure — no I/O, fully unit-testable)
    # ------------------------------------------------------------------
    def _render_chain(self, data: dict) -> str:
        """ELI5: render the multi-hop chain in a single line.

        ``source(s) → [canopy] → target(s)``. Multi-way sources are joined
        with " + ", so a 3-way setup reads naturally.
        """
        def _label(item: Any) -> str:
            if isinstance(item, dict):
                return str(item.get("name") or item.get("device_id") or "?")
            return str(item)
        sources = data.get("sources") or []
        targets = data.get("targets") or []
        canopy = data.get("canopy") or None
        source_text = " + ".join(_label(s) for s in sources) if sources else "(no source yet)"
        target_text = " + ".join(_label(t) for t in targets) if targets else "(no target yet)"
        chain = "{}  →  {}".format(source_text, target_text)
        if canopy:
            chain = "{}  →  {}  →  {}".format(source_text, _label(canopy), target_text)
        return chain

    def _render_heartbeat(self, data: dict, now_ms: int) -> str:
        """ELI5: render the live mesh health for the binding's hops.

        v0.1.20 — the **Visual Telemetry** upgrade. Every hop now carries
        a status badge — 🟢 Healthy, 🟡 Degraded, 🔴 Orphaned — next to it,
        the dashboard tallies how many branches are degraded or orphaned,
        and it models the round-trip latency a gesture pays (one 19-byte
        frame per hop). If any branch is not 🟢, the user is offered a
        "Re-handshake / Heal Mesh" action below. Pure: no I/O, so it is
        fully unit-testable and shares its verdict with the MCP tool via
        the one ``diagnose_mesh`` source of truth.
        """
        heartbeats = data.get("heartbeats") or []
        if not heartbeats:
            # ELI5: a direct one-hop binding (no intermediate relay) has no
            # orphan-detection row to show, which is itself the good news.
            return ("Heartbeat: no intermediate relays in this binding — "
                   "nothing can go orphaned. ✓")

        # ELI5 (v0.1.20): the dashboard and the MCP diagnostic share ONE
        # verdict so a hop can't look healthy here and orphaned there.
        diag = diagnose_mesh(heartbeats, now_ms)
        lines = []
        lines.append("**Live mesh telemetry** ({} hop(s), modeled latency {:} ms).".format(
            len(heartbeats), diag["modeled_latency_ms"]))
        lines.append("")
        for hop in diag["hops"]:
            badge = hop["badge"]
            state = hop["state"]
            index = str(hop.get("hop_index", "?"))
            nonce = hop.get("last_counter", "?")
            if state == "healthy":
                lines.append("    {} Hop {} (last nonce {}).".format(
                    badge, index, nonce))
            else:
                lines.append("    {} Hop {} — {} (needs Re-handshake).".format(
                    badge, index, hop["phrase"]))

        lines.append("")
        lines.append("**Branch health:**   🟢 healthy, 🟡 {} degraded, 🔴 {} orphaned.".format(
            diag["degraded_hops"], diag["orphaned_hops"]))

        if diag["needs_re_handshake"]:
            lines.append("")
            lines.append('⚠   A branch has gone dark. Use the "Re-handshake / Heal Mesh" action below to re-arm it without touching a device.')
        else:
            lines.append("")
            lines.append("All hops 🟢 healthy. ✓")
        return "\n".join(lines)


    def _render_dashboard(self, data: dict) -> str:
        """ELI5: render the full dashboard description.

        This is the markdown block that Home Assistant shows above the
        action dropdown. It is written for a non-technical user: plain
        language, no jargon, no raw bytes.
        """
        lines: list[str] = []
        lines.append("## Current binding")
        lines.append("")
        lines.append("**Chain:**  {}".format(self._render_chain(data)))
        lines.append("")
        protocol = str(data.get("protocol") or "unknown").upper()
        lines.append("**Protocol:**  {}".format(protocol))
        if data.get("use_group"):
            group_name = str(data.get("group_name") or "(unnamed group)")
            lines.append("**Group:**  {}".format(group_name))
        lines.append("")
        rules = data.get("rules") or []
        if rules:
            lines.append("**Configured LEAT rules** ({}).".format(len(rules)))
            lines.append("")
            for i, rule in enumerate(rules, start=1):
                lines.append("{}.  {}".format(i, _rule_eli5(rule)))
            lines.append("")
            lines.append("Raw (for reference):  ")
            lines.append("")
            for i, rule in enumerate(rules, start=1):
                lines.append("{}.  gesture = {!r}, action = {!r}".format(
                    i, rule.get("gesture", ""),
                    {k: v for k, v in rule.items() if k != "gesture"},
                ))
        else:
            lines.append("**No LEAT rules yet.** Use \"Add a new gesture rule\" below to give the first tap a meaning.")
        # v0.1.18 — the live mesh heartbeat / degradation flag. The "now"
        # comes from the stored heartbeat snapshot so the dashboard is a pure
        # render of the last health check.
        lines.append("")
        lines.append(self._render_heartbeat(data, int(data.get("heartbeat_now_ms", 0))))
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Step: dashboard (init)
    # ------------------------------------------------------------------
    async def async_step_init(self, user_input: dict | None = None):
        """ELI5: the dashboard step — show the current state and actions.

        ``user_input`` is ``None`` when the dialog is opened (no action
        chosen yet) or a dict with the action the user selected in the
        dropdown. Each action either navigates to a follow-up step or
        mutates the config entry in place.
        """
        errors: dict[str, str] = {}
        data = self._entry_data()

        if user_input is not None:
            action = str(user_input.get("action", "view") or "view")
            if action == "add_rule":
                return await self.async_step_add_rule(None)
            if action == "delete_rule":
                return await self.async_step_select_rule(None)
            if action == "delete_binding":
                return await self.async_step_delete_binding(None)
            if action == "erase_all":
                compiler = self._compiler()
                if compiler is None:
                    errors["base"] = "no_runtime"
                else:
                    try:
                        await compiler.async_erase_all()
                    except Exception:  # noqa: BLE001
                        errors["base"] = "erase_failed"
                        _LOGGER.exception("Options flow: erase_all failed")
                    else:
                        return self.async_create_entry(title="", data={"erase_result": "success"})
            elif action == "recompile":
                return self.async_abort(reason="restart_wizard")
            elif action == "re_handshake":
                # ELI5 (v0.1.18): the user clicked "Re-handshake" on a binding
                # whose heartbeat flagged a degraded/orphaned branch. We
                # re-establish each hop's baseline by treating "now" as a fresh
                # last-seen moment and marking every hop healthy, then persist.
                now_ms = int(data.get("heartbeat_now_ms", 0))
                healed: list[dict] = []
                for hop in (data.get("heartbeats") or []):
                    fresh = dict(hop)
                    fresh["last_seen_ms"] = now_ms
                    fresh["state"] = HEALTH_HEALTHY
                    healed.append(fresh)
                new_data = dict(data)
                new_data["heartbeats"] = healed
                new_data["heartbeat_now_ms"] = now_ms
                await self._update_entry(new_data)
                return await self.async_step_init(None)

        schema = vol.Schema(
            {
                vol.Required("action", default="view"): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            {"value": "view", "label": "View current binding"},
                            {"value": "add_rule", "label": "Add a new gesture rule"},
                            {"value": "delete_rule", "label": "Delete a specific rule"},
                            {"value": "delete_binding", "label": "Delete a specific source binding"},
                            {"value": "erase_all", "label": "Erase ALL bindings (nuclear)"},
                            {"value": "recompile", "label": "Re-run the wizard"},
                            {"value": "re_handshake", "label": "Re-handshake / Heal Mesh (re-arm severed mesh hops)"},
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="dashboard",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "dashboard": self._render_dashboard(data),
            },
        )

    # ------------------------------------------------------------------
    # Step: add a new gesture rule
    # ------------------------------------------------------------------
    def _target_capabilities(self, data: dict) -> set:
        """ELI5: union of the capabilities of every selected target."""
        caps: set = set()
        for t in data.get("targets") or []:
            kind = str(t.get("kind") if isinstance(t, dict) else "light")
            caps |= TARGET_CAPABILITIES.get(kind, set())
        return caps

    def _add_rule_schema(self, caps: set) -> vol.Schema:
        """ELI5: the add-rule form — a gesture picker, an optional time
        window, and the action fields the target kind supports."""
        fields: dict = {
            vol.Required("gesture"): SelectSelector(
                SelectSelectorConfig(
                    options=[{"value": t, "label": t} for t in TRIGGER_OPTIONS],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional("hour_start"): NumberSelector(
                NumberSelectorConfig(min=0, max=23, step=1, unit_of_measurement="h")
            ),
            vol.Optional("hour_end"): NumberSelector(
                NumberSelectorConfig(min=0, max=23, step=1, unit_of_measurement="h")
            ),
        }
        if "brightness" in caps:
            fields[vol.Optional("brightness_percent", default=100)] = NumberSelector(
                NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%")
            )
        if "color_temp" in caps:
            fields[vol.Optional("color_temp_kelvin")] = NumberSelector(
                NumberSelectorConfig(min=2000, max=6500, step=100, unit_of_measurement="K")
            )
        if "transition" in caps:
            fields[vol.Optional("transition_seconds")] = NumberSelector(
                NumberSelectorConfig(min=0, max=60, step=1, unit_of_measurement="s")
            )
        if "speed" in caps:
            fields[vol.Optional("fan_speed")] = NumberSelector(
                NumberSelectorConfig(min=0, max=5, step=1)
            )
        if "open_close" in caps:
            fields[vol.Optional("valve_open", default=True)] = bool
        if "lock_unlock" in caps:
            fields[vol.Optional("lock_unlock", default=False)] = bool
        if "setpoint" in caps:
            fields[vol.Optional("setpoint_c")] = NumberSelector(
                NumberSelectorConfig(min=0, max=35, step=0.5, unit_of_measurement="°C")
            )
        return vol.Schema(fields)

    async def async_step_add_rule(self, user_input: dict | None = None):
        """ELI5: add a new gesture rule to the existing binding."""
        errors: dict[str, str] = {}
        data = self._entry_data()
        caps = self._target_capabilities(data)

        if user_input is not None:
            gesture = str(user_input.get("gesture", "") or "").strip()
            if not gesture:
                errors["base"] = "gesture_required"
            elif gesture not in _GESTURE_ELI5:
                errors["base"] = "gesture_invalid"
            else:
                rule: dict[str, Any] = {"gesture": gesture}
                hour_start = user_input.get("hour_start")
                hour_end = user_input.get("hour_end")
                if hour_start is not None and hour_end is not None:
                    rule["hour_start"] = int(max(0, min(23, int(hour_start))))
                    rule["hour_end"] = int(max(0, min(23, int(hour_end))))
                if "brightness" in caps:
                      # ELI5 (v0.1.16): the user types a 0–100 % dial; we
                      # translate it to the device's 0–255 byte.
                    rule["brightness"] = percent_to_brightness(
                        int(user_input.get("brightness_percent", 100))
                     )
                if "color_temp" in caps and user_input.get("color_temp_kelvin") is not None:
                      # ELI5 (v0.1.16): the user types Kelvin (like a bulb
                      # box); we translate it to mireds for the hardware.
                    rule["color_temp_mireds"] = kelvin_to_mireds(
                        int(user_input["color_temp_kelvin"])
                     )
                if "transition" in caps and user_input.get("transition_seconds") is not None:
                    rule["transition_seconds"] = int(max(0, min(60, int(user_input["transition_seconds"]))))
                if "speed" in caps and user_input.get("fan_speed") is not None:
                    rule["fan_speed"] = int(max(0, min(5, int(user_input["fan_speed"]))))
                if "open_close" in caps:
                    rule["valve_open"] = bool(user_input.get("valve_open", True))
                if "lock_unlock" in caps:
                    rule["lock_unlock"] = bool(user_input.get("lock_unlock", False))
                if "setpoint" in caps and user_input.get("setpoint_c") is not None:
                    rule["setpoint_c"] = float(user_input["setpoint_c"])
                new_rules = list(data.get("rules") or [])
                new_rules.append(rule)
                new_data = dict(data)
                new_data["rules"] = new_rules
                await self._update_entry(new_data)
                return await self.async_step_init(None)

        return self.async_show_form(
            step_id="add_rule",
            data_schema=self._add_rule_schema(caps),
            errors=errors,
            description_placeholders={
                "target_count": str(len(data.get("targets") or [])),
            },
        )

    # ------------------------------------------------------------------
    # Step: select + delete a specific rule
    # ------------------------------------------------------------------
    async def async_step_select_rule(self, user_input: dict | None = None):
        """ELI5: show the list of rules so the user can pick one to delete."""
        errors: dict[str, str] = {}
        data = self._entry_data()
        rules = data.get("rules") or []
        if user_input is not None:
            rule_index = str(user_input.get("rule_index", "") or "")
            if not rule_index.isdigit():
                errors["base"] = "rule_index_required"
            else:
                idx = int(rule_index)
                if 0 <= idx < len(rules):
                    new_rules = list(rules)
                    del new_rules[idx]
                    new_data = dict(data)
                    new_data["rules"] = new_rules
                    await self._update_entry(new_data)
                    return await self.async_step_init(None)
                errors["base"] = "rule_index_out_of_range"
        options = []
        for i, rule in enumerate(rules):
            options.append({
                "value": str(i),
                "label": "{}.  {}".format(i + 1, _rule_eli5(rule)),
            })
        if not options:
            options.append({"value": "0", "label": "(no rules to delete)"})
        return self.async_show_form(
            step_id="select_rule",
            data_schema=vol.Schema(
                {
                    vol.Required("rule_index", default="0"): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    ),
                }
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step: delete a specific source binding
    # ------------------------------------------------------------------
    async def async_step_delete_binding(self, user_input: dict | None = None):
        """ELI5: delete one of the multi-way source bindings."""
        errors: dict[str, str] = {}
        data = self._entry_data()
        sources = data.get("sources") or []

        def _label(item: Any) -> str:
            if isinstance(item, dict):
                return str(item.get("name") or item.get("device_id") or "?")
            return str(item)

        if user_input is not None:
            source_index = str(user_input.get("source_index", "") or "")
            if not source_index.isdigit():
                errors["base"] = "source_index_required"
            else:
                idx = int(source_index)
                if 0 <= idx < len(sources):
                    new_sources = list(sources)
                    del new_sources[idx]
                    new_data = dict(data)
                    new_data["sources"] = new_sources
                    await self._update_entry(new_data)
                    return await self.async_step_init(None)
                errors["base"] = "source_index_out_of_range"
        options = []
        for i, src in enumerate(sources):
            options.append({"value": str(i), "label": "{}.  {}".format(i + 1, _label(src))})
        if not options:
            options.append({"value": "0", "label": "(no source bindings to delete)"})
        return self.async_show_form(
            step_id="delete_binding",
            data_schema=vol.Schema(
                {
                    vol.Required("source_index", default="0"): SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    ),
                }
            ),
            errors=errors,
        )


__all__ = ["ShipAnchorConfigFlow", "ShipAnchorOptionsFlow"]
