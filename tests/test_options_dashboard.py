"""
============================================================================
SHiP Anchor — tests/test_options_dashboard.py  (LAYER 1 + 2, v0.1.15)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
Tests for the **Visual LEAT & Multi-Hop Inspector Dashboard** — the upgraded
options flow that lets a user inspect and manage their binding *inside* Home
Assistant's options dialog, with no Developer Tools or log reading.

Layer 1 (pure logic):
  • ``_rule_eli5`` / ``_rule_actions_phrase`` / ``_rule_window_phrase`` —
    the raw LEAT rule dict → plain-English sentence translation.

Layer 2 (integration):
  • The dashboard step renders the chain + rules.
  • **Add rule** → the config entry's ``rules`` list grows by one.
  • **Delete rule** → the chosen rule is removed from the entry.
  • **Delete binding** → one of the multi-way sources is removed.
  • **Erase all** → still resilient (compiler store cleared).

All with Home Assistant stubbed out (see ``conftest.py``).

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- A fake ``config_entry`` is a plain object with a ``data`` dict (the wizard's
  entry payload: ``sources``, ``canopy``, ``targets``, ``rules``, ``protocol``).
- The dashboard persists via ``hass.config_entries.async_update_entry``
  (recorded by the fake harness in ``conftest.py``).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import custom_components.ship_anchor.config_flow as config_flow
from custom_components.ship_anchor.config_flow import (
    ShipAnchorOptionsFlow,
    _rule_actions_phrase,
    _rule_eli5,
    _rule_window_phrase,
)
from tests.conftest import _make_fake_hass, _make_fake_store


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _entry(data: dict) -> SimpleNamespace:
    """ELI5: a fake Home Assistant config entry carrying a ``data`` dict."""
    return SimpleNamespace(domain="ship_anchor", data=data, options={})


def _base_entry_data() -> dict:
    return {
        "source_device_id": "sw_a",
        "source_name": "Hall Switch",
        "source_ieee": "001788fffe00aa11",
        "protocol": "ZIGBEE_ZHA",
        "sources": [{"device_id": "sw_a", "name": "Hall Switch"}],
        "canopy": None,
        "targets": [{"device_id": "light1", "name": "Kitchen Light", "kind": "light"}],
        "use_group": False,
        "group_name": "",
        "rules": [
            {"gesture": "1x Tap", "brightness": 100},
            {"gesture": "3x Tap", "brightness": 40, "color_temp_mireds": 350},
        ],
    }


def _flow_with_entry(data: dict, with_compiler: bool = True) -> tuple[ShipAnchorOptionsFlow, object]:
    fake_hass = _make_fake_hass()
    if with_compiler:
        from custom_components.ship_anchor.compiler import AnchorCompiler
        fake_hass.data["ship_anchor"] = {
            "compiler": AnchorCompiler(fake_hass, _make_fake_store(), stored_data={}),
        }
    entry = _entry(data)
    flow = ShipAnchorOptionsFlow(entry)
    flow.hass = fake_hass
    return flow, entry


# ---------------------------------------------------------------------------
# LAYER 1 — the ELI5 rule → sentence translation
# ---------------------------------------------------------------------------
def test_rule_eli5_single_action() -> None:
    # ELI5 (v0.1.16): the sentence now shows the *human* percent first,
    # with the raw 0–255 byte in brackets. 128 raw ≈ 50 %.
    assert (
        _rule_eli5({"gesture": "1x Tap", "brightness": 128})
        == "On a single tap: set brightness to 50% (raw 128)."
    )


def test_rule_eli5_multi_action_joining() -> None:
    sentence = _rule_eli5(
        {"gesture": "3x Tap", "brightness": 40, "color_temp_mireds": 350}
    )
    # ELI5: two actions are joined with " and " so it reads like a sentence.
    # The colour temp now shows Kelvin (~2857 K) with raw mireds in
    # brackets; the brightness shows its percent with the raw byte.
    assert sentence.startswith("On a triple tap:")
    assert "set brightness to 16% (raw 40)" in sentence
    assert "2857K" in sentence
    assert "350 mireds" in sentence

def test_rule_eli5_overnight_window() -> None:
    sentence = _rule_eli5(
        {"gesture": "Hold", "hour_start": 22, "hour_end": 6, "valve_open": True}
    )
    assert "(between 22:00 and 06:00)" in sentence
    assert sentence.endswith("open the valve.")


def test_rule_window_phrase_no_window() -> None:
    assert _rule_window_phrase({"brightness": 100}) == ""


def test_rule_actions_phrase_empty_falls_back() -> None:
    # ELI5: a rule with no recognised action is a plain on/off toggle.
    assert _rule_actions_phrase({"gesture": "2x Tap"}) == "toggle the target (on / off)"


def test_rule_actions_phrase_valve_and_lock() -> None:
    assert _rule_actions_phrase({"valve_open": True}) == "open the valve"
    assert _rule_actions_phrase({"valve_open": False}) == "close the valve"
    assert _rule_actions_phrase({"lock_unlock": True}) == "unlock the lock"
    assert _rule_actions_phrase({"lock_unlock": False}) == "lock the lock"


# ---------------------------------------------------------------------------
# LAYER 2 — the dashboard renders the chain + rules
# ---------------------------------------------------------------------------
def test_dashboard_renders_chain_and_rules() -> None:
    data = _base_entry_data()
    flow, _entry_ = _flow_with_entry(data)
    rendered = flow._render_dashboard(data)
    # ELI5: the chain, the protocol, and both rules (in plain English) show.
    assert "Kitchen Light" in rendered
    assert "Hall Switch" in rendered
    assert "ZIGBEE_ZHA" in rendered
    # ELI5 (v0.1.16): the first rule now reads in human units (100 raw ≈ 39 %).
    assert "On a single tap: set brightness to 39% (raw 100)." in rendered
    assert "On a triple tap:" in rendered

def test_dashboard_renders_two_hop_chain_with_canopy() -> None:
    data = _base_entry_data()
    data["canopy"] = {"device_id": "canopy1", "name": "Ceiling Canopy"}
    flow, _entry_ = _flow_with_entry(data)
    chain = flow._render_chain(data)
    # ELI5: source → canopy → target, in that order.
    assert "Hall Switch" in chain
    assert "Ceiling Canopy" in chain
    assert "Kitchen Light" in chain
    assert chain.index("Hall Switch") < chain.index("Ceiling Canopy") < chain.index("Kitchen Light")


def test_dashboard_step_is_showable() -> None:
    data = _base_entry_data()
    flow, _entry_ = _flow_with_entry(data)
    form = _run(flow.async_step_init(None))
    assert form["step_id"] == "dashboard"
    # ELI5: the action dropdown offers the full inspector menu.
    schema = form["data_schema"]._schema
    action_key = next(k for k in schema if k.key == "action")
    option_values = {opt["value"] for opt in schema[action_key].config.options}
    assert {"view", "add_rule", "delete_rule", "delete_binding", "erase_all", "recompile"} <= option_values


# ---------------------------------------------------------------------------
# LAYER 2 — add a rule
# ---------------------------------------------------------------------------
def test_add_rule_grows_rules_list() -> None:
    data = _base_entry_data()
    flow, entry = _flow_with_entry(data)

    # ELI5: navigate dashboard → add_rule → submit a new gesture.
    _run(flow.async_step_init({"action": "add_rule"}))
    result = _run(flow.async_step_add_rule({
        "gesture": "5x Tap",
         "brightness_percent": 40,
         "color_temp_kelvin": 3700,
        "transition_seconds": 3,
    }))
    assert result["step_id"] == "dashboard"
    # ELI5: the entry now has THREE rules, the new one last.
    assert len(entry.data["rules"]) == 3
    new_rule = entry.data["rules"][-1]
    assert new_rule["gesture"] == "5x Tap"
    assert new_rule["brightness"] == 102
    assert new_rule["color_temp_mireds"] == 270
    assert new_rule["transition_seconds"] == 3
    # ELI5: the harness recorded the persistence call.
    assert len(flow.hass.config_entries.updates) == 1


def test_add_rule_rejects_unknown_gesture() -> None:
    data = _base_entry_data()
    flow, entry = _flow_with_entry(data)
    result = _run(flow.async_step_add_rule({"gesture": "12x Tap"}))
    assert result["errors"]["base"] == "gesture_invalid"
    # ELI5: the rule list is unchanged.
    assert len(entry.data["rules"]) == 2


# ---------------------------------------------------------------------------
# LAYER 2 — delete a rule
# ---------------------------------------------------------------------------
def test_delete_rule_removes_chosen_rule() -> None:
    data = _base_entry_data()
    flow, entry = _flow_with_entry(data)

    # ELI5: the select_rule dropdown lists every rule in plain English.
    form = _run(flow.async_step_select_rule(None))
    assert form["step_id"] == "select_rule"

    # ELI5: delete rule index 0 (the "1x Tap" brightness rule).
    result = _run(flow.async_step_select_rule({"rule_index": "0"}))
    assert result["step_id"] == "dashboard"
    assert len(entry.data["rules"]) == 1
    # ELI5: the remaining rule is the "3x Tap" one.
    assert entry.data["rules"][0]["gesture"] == "3x Tap"


def test_delete_rule_out_of_range_is_error() -> None:
    data = _base_entry_data()
    flow, entry = _flow_with_entry(data)
    result = _run(flow.async_step_select_rule({"rule_index": "99"}))
    assert result["errors"]["base"] == "rule_index_out_of_range"
    assert len(entry.data["rules"]) == 2


# ---------------------------------------------------------------------------
# LAYER 2 — delete a source binding (multi-way)
# ---------------------------------------------------------------------------
def test_delete_binding_removes_chosen_source() -> None:
    data = _base_entry_data()
    data["sources"] = [
        {"device_id": "sw_a", "name": "Hall Switch"},
        {"device_id": "sw_b", "name": "Kitchen Switch"},
    ]
    flow, entry = _flow_with_entry(data)

    # ELI5: delete the second source (index 1).
    result = _run(flow.async_step_delete_binding({"source_index": "1"}))
    assert result["step_id"] == "dashboard"
    assert len(entry.data["sources"]) == 1
    assert entry.data["sources"][0]["device_id"] == "sw_a"


def test_delete_binding_out_of_range_is_error() -> None:
    data = _base_entry_data()
    flow, entry = _flow_with_entry(data)
    result = _run(flow.async_step_delete_binding({"source_index": "5"}))
    assert result["errors"]["base"] == "source_index_out_of_range"
    assert len(entry.data["sources"]) == 1


# ---------------------------------------------------------------------------
# LAYER 2 — erase_all is still resilient (the v0.1.9 guarantee)
# ---------------------------------------------------------------------------
def test_erase_all_clears_compiler_store() -> None:
    data = _base_entry_data()
    flow, entry = _flow_with_entry(data, with_compiler=True)
    compiler = flow.hass.data["ship_anchor"]["compiler"]
    # ELI5: seed the compiler's store with a binding so erase_all has work.
    from custom_components.ship_anchor.protocols import BindingRecord
    from dataclasses import asdict
    compiler._stored_data["bindings"] = [asdict(BindingRecord(
        protocol="shelly", source_ieee="192.0.2.10", target_ieee="192.0.2.20",
        clusters=[], group_id=None, preflight_done=True,
    ))]

    result = _run(flow.async_step_init({"action": "erase_all"}))
    # ELI5: the action completes cleanly (not an error) and the store is
    # cleared.
    assert result.get("data", {}).get("erase_result") == "success"
    assert compiler._stored_data["bindings"] == []
