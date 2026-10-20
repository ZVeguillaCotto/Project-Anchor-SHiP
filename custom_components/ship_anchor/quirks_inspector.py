"""
============================================================================
SHiP v0.1 — custom_components/ship_anchor/quirks_inspector.py
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
Before SHiP Anchor can "wire" a switch directly to a light (so they talk to
each other without needing a hub), it must do some homework to find out what
"language" each specific device brand speaks.

Think of it like this: if you want to connect a German intercom to a French
doorbell, you first need to know:
  - Which button on the German intercom sends the ring signal?
  - Which wire on the French doorbell receives it?
  - Do you need to flip any special switches on either device first?

This file is the "device translator." It reads the instruction manuals for
every known Zigbee device (from the open-source zigpy/zha-device-handlers
library) and automatically answers three pre-flight questions:

  PRE-FLIGHT 1 — "SMART BULB / DECOUPLED MODE":
    Some smart switches will cut power to the bulb when you press them (which
    is bad — it kills the bulb's radio and it goes offline). We need to tell
    those switches "please don't cut power — just send a message instead."
    This is called "Smart Bulb Mode" (Inovelli), "Decoupled Mode" (Aqara),
    or "Command Mode" (Tuya).

  PRE-FLIGHT 2 — "WHICH SOCKET ON THE DEVICE":
    Every Zigbee device has numbered connection sockets (called "endpoints").
    Philips Hue bulbs use socket #11. Most other bulbs use socket #1.
    Water valves and shades also use specific sockets. This inspector finds
    the right socket number automatically so users don't have to look it up.

  PRE-FLIGHT 3 — "ANTI-POPCORN GROUPING":
    If you want to control MORE than 2 lights from one switch, sending them
    all separate messages one after another causes a "popcorn effect" — lights
    turn on one at a time with a visible delay. Instead, we put them all in a
    "Zigbee group" and the switch broadcasts ONE message to the whole group,
    making all lights turn on simultaneously.

============================================================================

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
Inspects zigpy.zcl cluster descriptors and zigpy.quirks device registries
at runtime (no subprocess, no network calls — pure Python introspection).
Falls back to a curated static device map if zigpy is not installed (allows
compiler.py to run in unit test / CI environments without HA dependencies).
============================================================================
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CLUSTER IDs (Zigbee Cluster Library — ZCL)
# ELI5: Each "cluster" is a standardised feature set. Think of cluster IDs as
# ISBN numbers for capability categories that all Zigbee devices understand.
# ---------------------------------------------------------------------------
CLUSTER_GROUPS         = 0x0004   # 4   — Group management (anti-popcorn binding)
CLUSTER_SCENES         = 0x0005   # 5   — Scene recall
CLUSTER_ONOFF          = 0x0006   # 6   — On/Off/Toggle
CLUSTER_LEVEL_CTRL     = 0x0008   # 8   — Dimmer / level control
CLUSTER_WINDOW_COVER   = 0x0102   # 258 — Motorised roller shades
CLUSTER_THERMOSTAT     = 0x0201   # 513 — HVAC / thermostat
CLUSTER_COLOR_CTRL     = 0x0300   # 768 — Colour and colour temperature
CLUSTER_IAS_ZONE       = 0x0500   # 1280 — Leak / safety sensors

# Manufacturer-specific cluster IDs
MFR_CLUSTER_INOVELLI   = 0xFC31   # 64561 — Inovelli config cluster
MFR_CLUSTER_AQARA      = 0xFCC0   # 64704 — Aqara/Opple config cluster

# ---------------------------------------------------------------------------
# STANDARD BINDABLE CLUSTERS (used when generating binding payloads)
# ---------------------------------------------------------------------------
STANDARD_BIND_CLUSTERS = [
    CLUSTER_GROUPS,
    CLUSTER_SCENES,
    CLUSTER_ONOFF,
    CLUSTER_LEVEL_CTRL,
    CLUSTER_WINDOW_COVER,
    CLUSTER_THERMOSTAT,
    CLUSTER_COLOR_CTRL,
]

# ---------------------------------------------------------------------------
# PREFLIGHT RESULT DATACLASS
# ELI5: A tidy summary of everything the inspector discovered about a device.
# ---------------------------------------------------------------------------
@dataclass
class DevicePreflightResult:
    """Results of the pre-flight inspection for one Zigbee device."""

    ieee: str
    """IEEE 802.15.4 MAC address (e.g. "00:0d:6f:00:05:7d:2d:34")."""

    model: str
    """Device model identifier string."""

    manufacturer: str
    """Device manufacturer string."""

    # Pre-flight 1: special mode configuration
    needs_smart_bulb_mode: bool = False
    """Inovelli VZM31-SN/VZM30-SN/VZM35-SN: must set attribute 0x0012 = 1."""

    needs_decoupled_mode: bool = False
    """Aqara H1 / Opple: must set attribute 0x0200 = 0 (decoupled)."""

    needs_command_mode: bool = False
    """Tuya TS004F / MOES knobs: must set attribute 0x8004 = 0 (command mode)."""

    needs_detach_relay: bool = False
    """SONOFF ZBMINIR2 Extreme: must enable detach_relay mode."""

    preflight_service_calls: list[dict] = field(default_factory=list)
    """Ready-to-emit HA service call dicts for pre-flight attribute writes."""

    # Pre-flight 2: endpoint resolution
    source_endpoint: int = 1
    """The endpoint on the SOURCE device (switch/sensor) that emits commands."""

    target_endpoint: int = 1
    """The endpoint on the TARGET device (bulb/valve/shade) that receives commands."""

    bindable_clusters: list[int] = field(default_factory=list)
    """List of ZCL cluster IDs available for direct binding on this device."""

    # Pre-flight 3: grouping decision
    requires_group_binding: bool = False
    """True if this device should use ZCL Group Binding (>2 targets)."""

    group_id: Optional[int] = None
    """ZHA Group ID to use for group binding (if requires_group_binding is True)."""

    warnings: list[str] = field(default_factory=list)
    """Human-readable ELI5 warning messages."""


# ---------------------------------------------------------------------------
# STATIC DEVICE QUIRKS DATABASE
# ELI5: A hand-curated table of well-known device quirks, used as fallback
# when the live zigpy library is not installed (e.g., in CI environments).
# ---------------------------------------------------------------------------
STATIC_DEVICE_QUIRKS: dict[str, dict] = {
    # Inovelli Blue Series 2-1 Smart Switch (VZM31-SN)
    "VZM31-SN": {
        "manufacturer":       "Inovelli",
        "source_endpoint":    2,    # Endpoint 2 has genOnOff Out + genLevelCtrl Out
        "target_endpoint":    1,    # Standard ZCL target default
        "needs_smart_bulb":   True,
        "smart_bulb_cluster": 0xFC31,  # 64561
        "smart_bulb_mfr_code":0x122F,  # 4655
        "smart_bulb_attr":    0x0012,  # 18
        "smart_bulb_value":   1,
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL, CLUSTER_SCENES],
    },
    # Inovelli Blue Series Fan+Light (VZM30-SN)
    "VZM30-SN": {
        "manufacturer":       "Inovelli",
        "source_endpoint":    2,
        "target_endpoint":    1,
        "needs_smart_bulb":   True,
        "smart_bulb_cluster": 0xFC31,
        "smart_bulb_mfr_code":0x122F,
        "smart_bulb_attr":    0x0012,
        "smart_bulb_value":   1,
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL, CLUSTER_SCENES],
    },
    # Inovelli Blue Series Fan Switch (VZM35-SN)
    "VZM35-SN": {
        "manufacturer":       "Inovelli",
        "source_endpoint":    2,
        "target_endpoint":    1,
        "needs_smart_bulb":   True,
        "smart_bulb_cluster": 0xFC31,
        "smart_bulb_mfr_code":0x122F,
        "smart_bulb_attr":    0x0012,
        "smart_bulb_value":   1,
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL],
    },
    # Aqara H1 Wireless Mini Switch
    "WXKG13LM": {
        "manufacturer":       "Aqara",
        "source_endpoint":    1,
        "target_endpoint":    1,
        "needs_decoupled":    True,
        "decoupled_cluster":  0xFCC0,  # 64704
        "decoupled_mfr_code": 0x115F,  # 4447
        "decoupled_attr":     0x0200,
        "decoupled_value":    0,       # 0 = decoupled mode
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL],
    },
    # Aqara Opple 6-button switch
    "WXCJKG13LM": {
        "manufacturer":       "Aqara",
        "source_endpoint":    1,
        "target_endpoint":    1,
        "needs_decoupled":    True,
        "decoupled_cluster":  0xFCC0,
        "decoupled_mfr_code": 0x115F,
        "decoupled_attr":     0x0200,
        "decoupled_value":    0,
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL, CLUSTER_SCENES],
    },
    # Tuya / MOES 4-button scene switch (TS004F)
    "TS004F": {
        "manufacturer":       "Tuya",
        "source_endpoint":    1,
        "target_endpoint":    1,
        "needs_command_mode": True,
        "cmd_mode_cluster":   CLUSTER_ONOFF,   # 0x0006
        "cmd_mode_attr":      0x8004,           # 32772
        "cmd_mode_value":     0,               # 0 = Command mode (offline binding OK)
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL, CLUSTER_SCENES],
    },
    # SONOFF ZBMINI R2 Extreme
    "ZBMINIR2": {
        "manufacturer":       "SONOFF",
        "source_endpoint":    1,
        "target_endpoint":    1,
        "needs_detach_relay": True,
        "bindable_clusters":  [CLUSTER_ONOFF],
    },
    # Philips Hue A19 / E26 bulb — target endpoint 11
    "LCA001": {
        "manufacturer":       "Signify",
        "source_endpoint":    1,
        "target_endpoint":    11,   # Hue bulbs use endpoint 11
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL, CLUSTER_COLOR_CTRL],
    },
    "LCA003": {
        "manufacturer":       "Signify",
        "source_endpoint":    1,
        "target_endpoint":    11,
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL, CLUSTER_COLOR_CTRL],
    },
    # Innr standard Zigbee bulb — target endpoint 1
    "RB 185 C": {
        "manufacturer":       "Innr",
        "source_endpoint":    1,
        "target_endpoint":    1,
        "bindable_clusters":  [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL, CLUSTER_COLOR_CTRL],
    },
    # Generic Zigbee water valve (various brands)
    "GENERIC_VALVE": {
        "manufacturer":       "Generic",
        "source_endpoint":    1,
        "target_endpoint":    1,
        "bindable_clusters":  [CLUSTER_ONOFF],
    },
    # Zigbee motorised window shade
    "GENERIC_SHADE": {
        "manufacturer":       "Generic",
        "source_endpoint":    1,
        "target_endpoint":    1,
        "bindable_clusters":  [CLUSTER_WINDOW_COVER],
    },
}


class QuirksInspector:
    """
    ELI5: The 'device translator.' Given a device's IEEE address, model,
    and manufacturer string, it figures out all the pre-flight steps and
    endpoint numbers needed to wire the device directly to its targets.

    Under the hood: attempts live zigpy/zha-device-handlers introspection
    first; falls back to the static STATIC_DEVICE_QUIRKS table if zigpy is
    unavailable (e.g., CI/unit-test environments).
    """

    def __init__(self) -> None:
        self._zigpy_available = False
        try:
            import zigpy.quirks   # type: ignore[import]
            import zigpy.zcl      # type: ignore[import]
            self._zigpy_quirks = zigpy.quirks
            self._zigpy_zcl    = zigpy.zcl
            self._zigpy_available = True
            _LOGGER.debug("QuirksInspector: zigpy available — live introspection enabled")
        except ImportError:
            _LOGGER.debug("QuirksInspector: zigpy not found — using static quirks DB")

    def inspect(
        self,
        ieee: str,
        model: str,
        manufacturer: str,
        target_count: int = 1,
        target_models: Optional[list[str]] = None,
    ) -> DevicePreflightResult:
        """
        ELI5: Feed in an IEEE address and model name; get back a fully filled-out
        DevicePreflightResult with pre-flight service calls ready to emit.

        Args:
            ieee:           IEEE 802.15.4 address of the SOURCE device (switch/sensor).
            model:          Model string (e.g. "VZM31-SN", "TS004F").
            manufacturer:   Manufacturer string.
            target_count:   Number of target devices this source will control.
            target_models:  List of target device model strings (to resolve endpoints).

        Returns:
            DevicePreflightResult with all pre-flight steps populated.
        """
        result = DevicePreflightResult(
            ieee=ieee,
            model=model,
            manufacturer=manufacturer,
        )

        # Try live zigpy introspection first
        if self._zigpy_available:
            self._introspect_zigpy(result, model, manufacturer)
        else:
            self._apply_static_quirks(result, model)

        # PRE-FLIGHT 3: Anti-Popcorn Group Binding escalation
        # ELI5: More than 2 targets = use group binding to avoid popcorn effect.
        if target_count > 2:
            result.requires_group_binding = True
            result.group_id = self._assign_group_id(ieee)
            result.warnings.append(
                f"ELI5: {target_count} target devices detected. SHiP Anchor will "
                f"automatically use Zigbee Group Binding (group_id={result.group_id}) "
                f"instead of sending {target_count} separate messages. This prevents "
                f"the 'popcorn effect' where lights turn on one at a time."
            )

        # Resolve target endpoints from target_models list
        if target_models:
            result.target_endpoint = self._resolve_target_endpoint(target_models[0])

        # Build pre-flight HA service call list
        self._build_preflight_calls(result)

        return result

    def _apply_static_quirks(self, result: DevicePreflightResult, model: str) -> None:
        """Apply quirks from the static database when zigpy is unavailable."""
        quirk = STATIC_DEVICE_QUIRKS.get(model)
        if not quirk:
            _LOGGER.debug("QuirksInspector: model %s not in static DB — using defaults", model)
            result.bindable_clusters = [CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL]
            return

        result.source_endpoint  = quirk.get("source_endpoint", 1)
        result.target_endpoint  = quirk.get("target_endpoint", 1)
        result.bindable_clusters = list(quirk.get("bindable_clusters", [CLUSTER_ONOFF]))

        if quirk.get("needs_smart_bulb"):
            result.needs_smart_bulb_mode = True
        if quirk.get("needs_decoupled"):
            result.needs_decoupled_mode = True
        if quirk.get("needs_command_mode"):
            result.needs_command_mode = True
        if quirk.get("needs_detach_relay"):
            result.needs_detach_relay = True

    def _introspect_zigpy(
        self,
        result: DevicePreflightResult,
        model: str,
        manufacturer: str,
    ) -> None:
        """
        ELI5: Ask the live zigpy library for the device's full specification.
        Looks up the device in zigpy's registry of known device quirks (zha-device-handlers)
        and extracts endpoint IDs and cluster lists dynamically.
        """
        try:
            # Get the device class from zigpy's quirks registry
            device_cls = self._zigpy_quirks.get_device(manufacturer, model)
            if not device_cls:
                # Fallback to static if not registered
                self._apply_static_quirks(result, model)
                return

            # Inspect endpoint descriptors
            ep_descs = getattr(device_cls, "replacement", {}).get("endpoints", {})
            for ep_id, ep_info in ep_descs.items():
                out_clusters = ep_info.get("output_clusters", [])
                if CLUSTER_ONOFF in out_clusters or CLUSTER_LEVEL_CTRL in out_clusters:
                    result.source_endpoint = ep_id
                    result.bindable_clusters = [
                        c for c in out_clusters
                        if c in STANDARD_BIND_CLUSTERS
                    ]
                    break

            # Apply manufacturer-specific mode overrides from static DB
            # (live zigpy doesn't store runtime attribute pre-flight values)
            self._apply_static_quirks(result, model)

        except Exception as exc:  # pylint: disable=broad-except
            _LOGGER.warning(
                "QuirksInspector: live zigpy introspection failed for %s/%s: %s — "
                "falling back to static DB",
                manufacturer, model, exc,
            )
            self._apply_static_quirks(result, model)

    def _resolve_target_endpoint(self, target_model: str) -> int:
        """
        ELI5: For well-known bulb families, return the correct socket number.
        Philips Hue bulbs use socket 11. Everything else uses socket 1.
        """
        quirk = STATIC_DEVICE_QUIRKS.get(target_model)
        if quirk:
            return quirk.get("target_endpoint", 1)
        # Hue bulb models tend to start with "LC" (LCA001, LCB001, etc.)
        if target_model.startswith("LC"):
            return 11
        return 1

    def _assign_group_id(self, ieee: str) -> int:
        """
        ELI5: Generate a stable ZHA Group ID from the source device's IEEE
        address (so the same switch always uses the same group number).
        """
        # Simple deterministic hash: sum of nibble pairs, modulo (0xFFFE - 0x0001)
        hex_digits = ieee.replace(":", "")
        total = sum(int(hex_digits[i:i+2], 16) for i in range(0, len(hex_digits), 2))
        return (total % 0xFFFD) + 1  # Group IDs 1..0xFFFD

    def _build_preflight_calls(self, result: DevicePreflightResult) -> None:
        """
        ELI5: Translate the pre-flight flags into ready-to-send HA service calls.
        The compiler will emit these calls in order before issuing bindings.
        """
        calls: list[dict] = []

        if result.needs_smart_bulb_mode:
            # Inovelli: Write Smart Bulb Mode attribute on Manufacturer Cluster 0xFC31
            calls.append({
                "action": "zha.set_zigbee_cluster_attribute",
                "description": (
                    "ELI5: Tell the Inovelli Blue switch NOT to cut power to "
                    "the bulb when pressed — instead just send a radio message. "
                    "This keeps the bulb's radio alive at all times."
                ),
                "data": {
                    "ieee": result.ieee,
                    "endpoint_id": 1,
                    "cluster_id": 0xFC31,       # Inovelli mfr cluster (64561)
                    "cluster_type": "in",
                    "attribute": 0x0012,         # smart_bulb_mode (18)
                    "value": 1,
                    "manufacturer": 0x122F,      # Inovelli mfr code (4655)
                },
            })

        if result.needs_decoupled_mode:
            # Aqara: Write operation_mode = 0 (decoupled) on 0xFCC0
            calls.append({
                "action": "zha.set_zigbee_cluster_attribute",
                "description": (
                    "ELI5: Tell the Aqara switch to work in 'decoupled mode' — "
                    "so pressing the button sends a radio message but doesn't "
                    "directly switch the relay. The bulb will handle on/off itself."
                ),
                "data": {
                    "ieee": result.ieee,
                    "endpoint_id": 1,
                    "cluster_id": 0xFCC0,       # Aqara mfr cluster (64704)
                    "cluster_type": "in",
                    "attribute": 0x0200,         # operation_mode
                    "value": 0,                  # 0 = decoupled
                    "manufacturer": 0x115F,      # Aqara mfr code (4447)
                },
            })

        if result.needs_command_mode:
            # Tuya TS004F: Write switch_mode = 0 (Command mode) on cluster 0x0006
            calls.append({
                "action": "zha.set_zigbee_cluster_attribute",
                "description": (
                    "ELI5: Tell the Tuya/MOES button to work in 'Command mode' — "
                    "so it sends standard Zigbee on/off commands directly to bulbs "
                    "without going through a coordinator (hub). This is required for "
                    "offline direct binding to work."
                ),
                "data": {
                    "ieee": result.ieee,
                    "endpoint_id": 1,
                    "cluster_id": CLUSTER_ONOFF,  # 0x0006 (6)
                    "cluster_type": "in",
                    "attribute": 0x8004,           # switch_mode (32772)
                    "value": 0,                    # 0 = Command mode
                    "manufacturer": None,
                },
            })

        if result.needs_detach_relay:
            calls.append({
                "action": "zha.set_zigbee_cluster_attribute",
                "description": (
                    "ELI5: Tell the SONOFF ZBMINIR2 to keep its internal relay "
                    "connected to the load even when the hub is offline, while "
                    "still forwarding switch presses over the Zigbee mesh."
                ),
                "data": {
                    "ieee": result.ieee,
                    "endpoint_id": 1,
                    "cluster_id": CLUSTER_ONOFF,
                    "cluster_type": "in",
                    "attribute": 0x8002,  # detach_relay
                    "value": 1,
                    "manufacturer": None,
                },
            })

        result.preflight_service_calls = calls
