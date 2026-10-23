"""
============================================================================
SHiP Anchor — tests/test_config_flow_unit.py  (LAYER 1: UNIT TESTS)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
Base-of-the-pyramid tests for the *wizard's* pure decisions. We do NOT run
the whole wizard here (that is the integration layer); we test the three
small brain-cells that decide:

  1. ``_device_protocols_match`` — can these two devices ever talk to each
     other? (a Zigbee switch can reach a Zigbee light, but never a Z-Wave
     lock).
  2. ``_target_kind`` — what KIND of thing is the target (light, valve,
     lock, fan, …)? So the behaviour step only shows fields that device can
     actually act on.
  3. ``_infer_protocol`` — which protocol does a device speak, from its
     identifier namespace or model string?

All three are pure functions: no Home Assistant instance, no I/O.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
Fake devices are tiny objects exposing the attributes the helpers read:
``id``, ``model``, ``manufacturer``, and ``identifiers`` (a tuple of
(namespace, value) pairs). ``Protocol`` is the real enum from
``custom_components.ship_anchor.protocols``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.ship_anchor.config_flow import (
    TARGET_CAPABILITIES,
    _device_protocols_match,
    _infer_protocol,
    _target_kind,
)
from custom_components.ship_anchor.protocols import Protocol


def _device(
    id: str = "dev1",
    model: str = "",
    manufacturer: str = "",
    identifiers: tuple | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id,
        model=model,
        manufacturer=manufacturer,
        identifiers=identifiers or (),
        name_by_user=None,
        name=id,
    )


# ---------------------------------------------------------------------------
# _device_protocols_match — the cross-protocol safety guard
# ---------------------------------------------------------------------------
def test_zha_and_z2m_cross_match() -> None:
    # ELI5: ZHA and Zigbee2MQTT are BOTH Zigbee, so a switch paired under one
    # can still reach a light paired under the other.
    assert _device_protocols_match(Protocol.ZIGBEE_ZHA, Protocol.ZIGBEE_Z2M)
    assert _device_protocols_match(Protocol.ZIGBEE_Z2M, Protocol.ZIGBEE_ZHA)


def test_same_protocol_matches() -> None:
    assert _device_protocols_match(Protocol.SHELLY, Protocol.SHELLY)
    assert _device_protocols_match(Protocol.ZWAVE, Protocol.ZWAVE)


@pytest.mark.parametrize(
    "a,b",
    [
        (Protocol.ZIGBEE_ZHA, Protocol.ZWAVE),
        (Protocol.ZIGBEE_Z2M, Protocol.ZWAVE),
        (Protocol.MATTER, Protocol.SHELLY),
        (Protocol.SHELLY, Protocol.TASMOTA),
        (Protocol.ZIGBEE_ZHA, Protocol.MATTER),
    ],
)
def test_cross_protocol_rejected(a: Protocol, b: Protocol) -> None:
    # ELI5: a device on one bus can never silently drive a device on another
    # bus — the wizard must refuse the pairing.
    assert not _device_protocols_match(a, b)
    assert not _device_protocols_match(b, a)


# ---------------------------------------------------------------------------
# _target_kind — entity platform wins, then model keywords, then default
# ---------------------------------------------------------------------------
def test_target_kind_prefers_entity_platform() -> None:
    # ELI5: if Home Assistant tells us the device hosts a "climate" entity,
    # that beats whatever the model string happens to say.
    device = _device(id="d1", model="fan-lookalike")
    entity_reg = SimpleNamespace(
        entities={
            "e1": SimpleNamespace(device_id="d1", platform="climate"),
            "e2": SimpleNamespace(device_id="other", platform="fan"),
        }
    )
    assert _target_kind(device, entity_reg) == "climate"


def test_target_kind_model_keyword_fallback() -> None:
    assert _target_kind(_device(model="LCA001"), None) == "light"
    assert _target_kind(_device(model="XYZ fan 9000"), None) == "fan"
    assert _target_kind(_device(model="SmartValve V2"), None) == "valve"
    assert _target_kind(_device(model="UL395BE deadbolt"), None) == "lock"
    assert _target_kind(_device(model="Roller Shade"), None) == "cover"
    assert _target_kind(_device(model="TRV-2000 thermostat"), None) == "climate"


def test_target_kind_defaults_to_light() -> None:
    # ELI5: when nothing else matches, assume a light (the most common target).
    assert _target_kind(_device(model="Mystery-Widget"), None) == "light"
    # A None entity registry (no entities known) is handled the same way.
    assert _target_kind(_device(model="Mystery-Widget"), None) == "light"


# ---------------------------------------------------------------------------
# TARGET_CAPABILITIES — the field set the behaviour step offers per kind
# ---------------------------------------------------------------------------
def test_target_capabilities_shape() -> None:
    assert TARGET_CAPABILITIES["light"] == {"brightness", "color_temp", "transition"}
    assert TARGET_CAPABILITIES["valve"] == {"open_close"}
    assert TARGET_CAPABILITIES["lock"] == {"lock_unlock"}
    assert "speed" in TARGET_CAPABILITIES["fan"]
    assert "setpoint" in TARGET_CAPABILITIES["climate"]


# ---------------------------------------------------------------------------
# _infer_protocol — identifier namespace first, then model string
# ---------------------------------------------------------------------------
def test_infer_protocol_from_identifier_namespace() -> None:
    assert (
        _infer_protocol(
            _device(identifiers=(("zha", "00:11:22:33:44:55:66:77"),))
        )
        is Protocol.ZIGBEE_ZHA
    )
    assert (
        _infer_protocol(_device(identifiers=(("zwave_js", "1"),)))
        is Protocol.ZWAVE
    )


def test_infer_protocol_zigbee_and_z2m_namespaces() -> None:
    assert _infer_protocol(_device(identifiers=(("zigbee", "x"),))) is Protocol.ZIGBEE_ZHA
    assert (
        _infer_protocol(_device(identifiers=(("zigbee2mqtt", "x"),)))
        is Protocol.ZIGBEE_Z2M
    )


def test_infer_protocol_model_string_fallback() -> None:
    assert _infer_protocol(_device(model="Tasmota144 relay")) is Protocol.TASMOTA
    assert _infer_protocol(_device(model="Shelly1")) is Protocol.SHELLY


def test_infer_protocol_unknown() -> None:
    assert _infer_protocol(_device(model="Plain-USB-Thing")) is Protocol.UNKNOWN


# ---------------------------------------------------------------------------
# The ConfigFlow base class accepts the domain kwarg (stub contract check)
# ---------------------------------------------------------------------------
def test_config_flow_class_instantiates() -> None:
    from custom_components.ship_anchor.config_flow import ShipAnchorConfigFlow

    flow = ShipAnchorConfigFlow()
    # ELI5: the flow starts with an empty input bag — the wizard fills this
    # in step by step.
    assert flow._input == {}
