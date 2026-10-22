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


class ShipAnchorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Interactive wizard that builds one offline binding per config entry."""

    VERSION = 1

    # ------------------------------------------------------------------
    # Step 1 — pick the SOURCE device
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input: dict | None = None):
        """
        ELI5: show the user a dropdown of every switch / button / dimmer
        in their home and let them pick the one they want to anchor.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            source_device_id = str(user_input.get("source_device", ""))
            device_reg = dr.async_get(self.hass)
            source = device_reg.async_get(source_device_id)
            if source is None:
                errors["base"] = "source_not_found"
            else:
                self._input = {  # type: ignore[attr-defined]
                    "source_device_id": source_device_id,
                    "source_name": _device_name(source),
                    "source_manufacturer": _device_manufacturer(source),
                    "source_model": _device_model(source),
                    "source_ieee": _device_ieee(source),
                    "protocol": _infer_protocol(source).name,
                }
                return await self.async_step_target()
        else:
            device_reg = dr.async_get(self.hass)
            entity_reg = er.async_get(self.hass)
            sources = self._candidate_sources(device_reg, entity_reg)
            if not sources:
                sources = [
                    (dev.id, _device_name(dev)) for dev in device_reg.devices.values()
                ]
            schema = vol.Schema(
                {
                    vol.Required("source_device"): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                {"value": dev_id, "label": name}
                                for dev_id, name in sources
                            ],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            )
            return self.async_show_form(
                step_id="user",
                data_schema=schema,
                errors=errors,
                description_placeholders={"mode": "pick the source"},
            )

    # ------------------------------------------------------------------
    # Step 2 — pick the TARGET device(s)
    # ------------------------------------------------------------------
    async def async_step_target(self, user_input: dict | None = None):
        """
        ELI5: let the user choose one or more lights / valves / locks that
        the source should control, plus an optional ZCL group name.
        """
        errors: dict[str, str] = {}
        inp = self._input  # type: ignore[attr-defined]

        if user_input is not None:
            raw_targets = user_input.get("targets", []) or []
            if isinstance(raw_targets, str):
                raw_targets = [raw_targets]
            target_ids = [str(item) for item in raw_targets if item]
            use_group = bool(user_input.get("use_group", False))
            group_name = str(user_input.get("group_name", "") or "").strip()

            if not target_ids and not (use_group and group_name):
                errors["base"] = "target_required"
            else:
                device_reg = dr.async_get(self.hass)
                targets = []
                for tid in target_ids:
                    dev = device_reg.async_get(tid)
                    if dev is not None:
                        targets.append({
                            "device_id": tid,
                            "name": _device_name(dev),
                            "ieee": _device_ieee(dev),
                            "model": _device_model(dev),
                            "manufacturer": _device_manufacturer(dev),
                        })
                inp.update({  # type: ignore[attr-defined]
                    "targets": targets,
                    "use_group": use_group,
                    "group_name": group_name,
                })
                return await self.async_step_behavior()

        device_reg = dr.async_get(self.hass)
        entity_reg = er.async_get(self.hass)
        targets = self._candidate_targets(device_reg, entity_reg)
        target_options = [
            {"value": dev_id, "label": name} for dev_id, name in targets
        ]

        # ELI5: multi-select — the user can pick any number of target
        # devices in one go. SHiP Anchor will automatically escalate to a
        # ZCL group binding if more than two targets are chosen (to avoid
        # the "popcorn effect" where lights turn on one at a time).
        schema = vol.Schema(
            {
                vol.Required("targets"): SelectSelector(
                    SelectSelectorConfig(
                        options=target_options,
                        mode=SelectSelectorMode.MULTIPLE,
                    )
                ),
                vol.Optional("use_group", default=False): bool,
                vol.Optional("group_name", default=""): str,
            }
        )
        return self.async_show_form(
            step_id="target",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "source": inp.get("source_name", "the source"),
            },
        )

    # ------------------------------------------------------------------
    # Step 3 — behaviour mapping
    # ------------------------------------------------------------------
    async def async_step_behavior(self, user_input: dict | None = None):
        """
        ELI5: ask what each tap / hold should do, plus optional brightness
        and colour temperature for the target light(s).
        """
        errors: dict[str, str] = {}
        inp = self._input  # type: ignore[attr-defined]

        if user_input is not None:
            trigger = str(user_input.get("trigger", "1x Tap"))
            brightness = int(user_input.get("brightness", 100))
            brightness = max(0, min(255, brightness))
            color_temp = user_input.get("color_temp_mireds")
            if color_temp is not None:
                color_temp = int(max(153, min(500, color_temp)))
            transition = user_input.get("transition_seconds")
            if transition is not None:
                transition = int(max(0, min(60, transition)))

            inp.update({  # type: ignore[attr-defined]
                "trigger": trigger,
                "brightness": brightness,
                "color_temp_mireds": color_temp,
                "transition_seconds": transition,
            })
            return await self.async_step_finish()

        schema = vol.Schema(
            {
                vol.Required("trigger", default="1x Tap"): SelectSelector(
                    SelectSelectorConfig(
                        options=[{"value": t, "label": t} for t in TRIGGER_OPTIONS],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required("brightness", default=100): NumberSelector(
                    NumberSelectorConfig(min=0, max=255, step=1, unit_of_measurement="%")
                ),
                vol.Optional("color_temp_mireds"): NumberSelector(
                    NumberSelectorConfig(min=153, max=500, step=1, unit_of_measurement="mireds")
                ),
                vol.Optional("transition_seconds"): NumberSelector(
                    NumberSelectorConfig(min=0, max=60, step=1, unit_of_measurement="s")
                ),
            }
        )
        return self.async_show_form(
            step_id="behavior",
            data_schema=schema,
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
            source_ieee = inp.get("source_ieee", "")
            resolved_targets = []
            for t in targets:
                resolved = dict(t)
                if not resolved.get("ieee") and source_ieee:
                    resolved["ieee"] = source_ieee
                resolved_targets.append(resolved)

            config: dict[str, Any] = {
                "protocol": protocol.name,
                "source_ieee": source_ieee,
                "source_model": inp.get("source_model", ""),
                "source_manufacturer": inp.get("source_manufacturer", ""),
                "targets": resolved_targets,
            }
            if inp.get("use_group") and inp.get("group_name"):
                config["group_name"] = inp["group_name"]
            if inp.get("trigger"):
                config["trigger_subtype"] = inp["trigger"]
            if inp.get("brightness") is not None:
                config["brightness"] = inp["brightness"]
            if inp.get("color_temp_mireds") is not None:
                config["color_temp_mireds"] = inp["color_temp_mireds"]
            if inp.get("transition_seconds") is not None:
                config["transition_seconds"] = inp["transition_seconds"]

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
                inp.get("source_name", "source"), len(targets) or 1
            )
            data: dict[str, Any] = {
                "source_device_id": inp.get("source_device_id", ""),
                "source_name": inp.get("source_name", ""),
                "source_ieee": source_ieee,
                "protocol": protocol_name,
                "targets": targets,
                "use_group": bool(inp.get("use_group", False)),
                "group_name": inp.get("group_name", ""),
                "trigger": inp.get("trigger", ""),
                "brightness": inp.get("brightness"),
                "color_temp_mireds": inp.get("color_temp_mireds"),
                "transition_seconds": inp.get("transition_seconds"),
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
