"""
============================================================================
SHiP Anchor — tests/test_compiler_unit.py  (LAYER 1: UNIT TESTS)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
These are the "base of the pyramid": small, fast tests that check the pure
math SHiP Anchor does, with *no* Home Assistant, *no* radios, and *no* network
involved. We test the LEAT (Local Event-Action Table) builder: given a list
of "rule sheets" (a tap count, an optional time window, and what to do), does
it emit rows that line up *field for field* with the C struct the hardware
stores (`ship_leat_entry_t` in src/ship_core/ship_protocol.h)?

Keeping the LEAT rows honest to the C struct is the whole point: the ESPHome
component memcpy's these rows straight into device flash, so a single invented
field would corrupt every device.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- ``AnchorCompiler._rule_domain`` maps a rule to a domain nibble (0x1 OnOff,
  0x2 Lighting, 0x4 Water, 0x5 Lock, 0x6 HVAC).
- ``AnchorCompiler._rule_action`` packs the action into (type, param, frame).
- ``AnchorCompiler.build_leat_table`` assembles the full row, skipping
  unknown gestures instead of failing.
"""

from __future__ import annotations

import pytest

from custom_components.ship_anchor.compiler import AnchorCompiler


@pytest.fixture
def compiler() -> AnchorCompiler:
    # ELI5: build a compiler with throwaway store/hass — the pure-logic
    # methods under test never touch either.
    return AnchorCompiler(hass=None, store=None, stored_data={})


# The exact field set the C struct `ship_leat_entry_t` carries. Any row we
# emit for the device MUST be a subset of this set (plus the optional
# `frame_params` annotation the frame encoder understands).
C_STRUCT_FIELDS = {
    "group_id",
    "domain",
    "gesture",
    "action_type",
    "action_param",
    "hour_start",
    "hour_end",
    "enabled",
}


# ---------------------------------------------------------------------------
# _rule_domain — which "category icon" a rule belongs to
# ---------------------------------------------------------------------------
def test_rule_domain_lighting_combo(compiler: AnchorCompiler) -> None:
    assert AnchorCompiler._rule_domain({"brightness": 100}) == 0x2
    assert AnchorCompiler._rule_domain({"color_temp_mireds": 350}) == 0x2


def test_rule_domain_plain_onoff(compiler: AnchorCompiler) -> None:
    # ELI5: a rule with no recognised action is a plain relay on/off.
    assert AnchorCompiler._rule_domain({}) == 0x1


def test_rule_domain_valve_lock_climate(compiler: AnchorCompiler) -> None:
    assert AnchorCompiler._rule_domain({"valve_open": True}) == 0x4
    assert AnchorCompiler._rule_domain({"lock_unlock": False}) == 0x5
    assert AnchorCompiler._rule_domain({"setpoint_c": 21.5}) == 0x6
    assert AnchorCompiler._rule_domain({"fan_speed": 3}) == 0x6


# ---------------------------------------------------------------------------
# _rule_action — packing the action byte + optional frame annotation
# ---------------------------------------------------------------------------
def test_rule_action_valve_and_lock(compiler: AnchorCompiler) -> None:
    assert AnchorCompiler._rule_action({"valve_open": True}) == (3, 1, {})
    assert AnchorCompiler._rule_action({"valve_open": False}) == (3, 0, {})
    assert AnchorCompiler._rule_action({"lock_unlock": False}) == (0, 0, {})
    assert AnchorCompiler._rule_action({"lock_unlock": True}) == (0, 1, {})


def test_rule_action_climate_and_fan(compiler: AnchorCompiler) -> None:
    # ELI5: °C × 2 keeps one decimal of precision inside a single byte.
    assert AnchorCompiler._rule_action({"setpoint_c": 21.5}) == (4, 43, {})
    assert AnchorCompiler._rule_action({"fan_speed": 3}) == (4, 3, {})


def test_rule_action_plain_brightness_is_onoff(compiler: AnchorCompiler) -> None:
    # ELI5: a *plain* brightness rule (no colour temp) is treated as a relay
    # on/off action — brightness is only meaningful together with colour temp,
    # where it becomes the lighting-combo action byte.
    action_type, action_param, frame = AnchorCompiler._rule_action({"brightness": 200})
    assert (action_type, action_param) == (0, 1)
    assert frame == {}


def test_rule_action_brightness_with_colour_is_lighting_byte(
    compiler: AnchorCompiler,
) -> None:
    # ELI5: once colour temp is present, brightness (0–255) IS the lighting
    # action byte, and the richer values ride in frame_params.
    action_type, action_param, _frame = AnchorCompiler._rule_action(
        {"brightness": 200, "color_temp_mireds": 350}
    )
    assert action_type == 1
    assert action_param == 200


def test_rule_action_colour_temp_rides_in_frame_params(compiler: AnchorCompiler) -> None:
    # ELI5: colour temp (153–500 mireds) does NOT fit in the single action
    # byte, so it is carried in the frame_params annotation.
    action_type, action_param, frame = AnchorCompiler._rule_action(
        {"brightness": 100, "color_temp_mireds": 350}
    )
    assert action_type == 1
    assert action_param == 100
    assert frame["param_1"] == 100
    assert frame["param_2"] == 350


def test_rule_action_transition_uses_frame_param_3(compiler: AnchorCompiler) -> None:
    _action_type, _param, frame = AnchorCompiler._rule_action(
        {"brightness": 120, "color_temp_mireds": 300, "transition_seconds": 5}
    )
    # ELI5: fade time is stored as ×10 (5 s → 50) and capped at 255.
    assert frame["param_3"] == 50


# ---------------------------------------------------------------------------
# build_leat_table — the full device row
# ---------------------------------------------------------------------------
def test_build_leat_table_matches_c_struct(compiler: AnchorCompiler) -> None:
    rows = compiler.build_leat_table(
        [
            {"gesture": "1x Tap", "brightness": 100},
            {"gesture": "3x Tap", "valve_open": True},
        ],
        group_id=42,
    )
    assert len(rows) == 2
    for row in rows:
        assert set(row) <= C_STRUCT_FIELDS | {"frame_params"}, row
    assert rows[0]["group_id"] == 42
    assert rows[0]["gesture"] == 1
    assert rows[1]["gesture"] == 3
    assert rows[1]["action_param"] == 1


def test_build_leat_table_preserves_overnight_window(compiler: AnchorCompiler) -> None:
    rows = compiler.build_leat_table(
        [{"gesture": "5x Tap", "hour_start": 22, "hour_end": 6, "brightness": 15}],
        group_id=7,
    )
    assert len(rows) == 1
    assert rows[0]["hour_start"] == 22
    assert rows[0]["hour_end"] == 6
    assert rows[0]["enabled"] is True


def test_build_leat_table_skips_unknown_gesture_not_fatal(compiler: AnchorCompiler) -> None:
    rows = compiler.build_leat_table(
        [
            {"gesture": "not-a-real-gesture", "brightness": 50},
            {"gesture": "Hold", "fan_speed": 2},
        ],
        group_id=1,
    )
    assert len(rows) == 1
    assert rows[0]["gesture"] == 6


def test_build_leat_table_empty_rules(compiler: AnchorCompiler) -> None:
    assert compiler.build_leat_table([]) == []


def test_build_leat_table_group_id_is_one_byte(compiler: AnchorCompiler) -> None:
    rows = compiler.build_leat_table(
        [{"gesture": "1x Tap", "brightness": 10}], group_id=0x1FF
    )
    assert rows[0]["group_id"] == 0x1FF & 0xFF
