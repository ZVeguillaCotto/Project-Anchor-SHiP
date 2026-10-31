"""
============================================================================
SHiP Anchor — tests/test_integration.py  (LAYER 2: INTEGRATION TESTS)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
The "middle of the pyramid". Instead of testing one brain-cell at a time (the
unit layer), here we wire several SHiP Anchor parts together and check they
play nicely:

  • The wizard (``ShipAnchorConfigFlow``) walks target → source → behaviour →
    finish, only ever offering switches that *can* reach the chosen target
    (the cross-protocol guard), and finally records the collected "rule
    sheets" on the config entry.
  • The persistent store (``.storage/ship_anchor.bindings``) round-trips a
    compiled binding: compile → save to disk → load back → list it again,
    proving the data is JSON-serialisable and lossless.
  • "Erase everything" clears the store even when one of the saved bindings
    is broken or its device is offline — the user always gets a clean
    success, never a traceback.

These tests run with Home Assistant *stubbed out* (see ``conftest.py``) —
no HA box, no radios, no network. Only the top of the pyramid (the C core and
the disaster drills) touches real hardware paths.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- Fake device + entity registries are installed by monkeypatching
  ``config_flow.dr.async_get`` / ``config_flow.er.async_get``.
- The persistent store is the JSON-round-tripping fake from ``conftest.py``.
- Async tests use ``@pytest.mark.asyncio``-free helpers: we run the coroutines
  with ``asyncio.run`` so no extra plugin is required.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

import custom_components.ship_anchor.config_flow as config_flow
from custom_components.ship_anchor.compiler import AnchorCompiler, BindingRecord
from tests.conftest import _make_fake_hass, _make_fake_store


def _run(coro):
    """ELI5: run one async function to completion without a pytest plugin.

    A fresh event loop per call keeps the coroutine world isolated from any
    other test, which mirrors how Home Assistant drives a flow in its own
    loop.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Fake registries
# ---------------------------------------------------------------------------
class FakeDeviceRegistry:
    def __init__(self, devices):
        self.devices = devices

    def async_get(self, device_id):
        return self.devices.get(device_id)


class FakeEntityRegistry:
    def __init__(self, entities):
        self.entities = entities

    def async_get(self, entity_id):
          # ELI5 (v0.1.30): the advanced manual picker resolves an entity (e.g. an
          # existing automation) back to its owning device, so the registry needs
          # to look up entities the way the real HA entity registry does.
        return self.entities.get(entity_id)


def _device(id, model, manufacturer="", identifiers=()):
    return SimpleNamespace(
        id=id, model=model, manufacturer=manufacturer,
        identifiers=identifiers, name_by_user=None, name=id,
    )


def _entity(device_id, platform):
    return SimpleNamespace(device_id=device_id, platform=platform)


@pytest.fixture
def registries():
    """ELI5: a small home — two Zigbee switches, a Zigbee light, and a
    Z-Wave switch + lock. The Z-Wave devices exist so we can prove the
    wizard never offers them as a source for a Zigbee target."""
    # ELI5: IEEE 802.15.4 addresses are exactly 16 hex digits (8 bytes).
    # The old fixture used 18-digit strings that failed the IEEE regex in
    # ``_device_ieee``; this breaks the ZHA handler's "source must have an
    # IEEE" guard. All values below are now valid 16-digit addresses.
    devices = {
        "sw_a": _device("sw_a", "TS0505B", "Tuya",
                        identifiers=(("zha", "001788fffe00aa11"),)),
        "sw_b": _device("sw_b", "TS0505B", "Tuya",
                        identifiers=(("zha", "001788fffe00dd22"),)),
        "light1": _device("light1", "LCA001", "IKEA",
                          identifiers=(("zha", "001798fffe001133"),)),
        "zw_sw": _device("zw_sw", "ZW158 switch", "AEON",
                         identifiers=(("zwave_js", "3"),)),
        "zw_lock": _device("zw_lock", "UL395BE", "Yale",
                           identifiers=(("zwave_js", "7"),)),
    }
    entities = {
        "e_sw_a": _entity("sw_a", "switch"),
        "e_sw_b": _entity("sw_b", "button"),
        "e_light1": _entity("light1", "light"),
        "e_zw_sw": _entity("zw_sw", "switch"),
        "e_zw_lock": _entity("zw_lock", "lock"),
    }
    return FakeDeviceRegistry(devices), FakeEntityRegistry(entities)


@pytest.fixture
def patched_registries(registries, monkeypatch):
    device_reg, entity_reg = registries
    monkeypatch.setattr(config_flow.dr, "async_get", lambda hass: device_reg)
    monkeypatch.setattr(config_flow.er, "async_get", lambda hass: entity_reg)
    return device_reg, entity_reg


def _source_option_values(form: dict) -> set:
    """ELI5: pull the dropdown values out of the wizard's source-picker form."""
    schema = form["data_schema"]._schema
    src_key = next(k for k in schema if k.key == "sources")
    return {opt["value"] for opt in schema[src_key].config.options}


# ---------------------------------------------------------------------------
# 1 — the wizard only offers same-protocol sources
# ---------------------------------------------------------------------------
def test_wizard_source_filter_excludes_cross_protocol(patched_registries) -> None:
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    # ELI5: pick the Zigbee light as the target …
    form = _run(flow.async_step_target({"targets": ["light1"]}))
    assert form["step_id"] == "source"

    # … so the source dropdown must list the two Zigbee switches and NOT the
    # Z-Wave switch (which could never reach a Zigbee light).
    values = _source_option_values(form)
    assert "sw_a" in values
    assert "sw_b" in values
    assert "zw_sw" not in values


def test_wizard_source_filter_for_zwave_target(patched_registries) -> None:
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    form = _run(flow.async_step_target({"targets": ["zw_lock"]}))
    values = _source_option_values(form)
    assert "zw_sw" in values
    assert "sw_a" not in values
    assert "sw_b" not in values


# ---------------------------------------------------------------------------
# 2 — the full wizard records the collected rules on the entry
# ---------------------------------------------------------------------------
def test_wizard_full_flow_creates_entry_with_rules(patched_registries) -> None:
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    # Step 1 → target.
    _run(flow.async_step_target({"targets": ["light1"]}))
    # Step 2 → source (Zigbee switch).
    _run(flow.async_step_source({"sources": ["sw_a"]}))
    # Step 3 → behaviour: one 3x-tap rule that dims the light.
    _run(flow.async_step_behavior({
        "rule1_gesture": "3x Tap",
        "rule1_brightness_percent": 50,
        "rule1_color_temp_kelvin": 2700,
        "rule1_transition_seconds": 2,
    }))
    # Step 4 → canopy: skip the intermediate module (direct one-hop binding).
    _run(flow.async_step_canopy({"canopy": ""}))
    # Step 5 → finish (dry-run).
    result = _run(flow.async_step_finish({"dry_run": True}))

    # ELI5: the entry is created and carries the rule sheet the user typed.
    assert result["created"] is True
    data = result["data"]
    assert data["rules"] and data["rules"][0]["gesture"] == "3x Tap"
    rule = data["rules"][0]
        # ELI5 (v0.1.16): the human input (50 %, 2700 K) was translated to
    # the protocol units the LEAT row stores (127-byte brightness, 370 mireds).
    assert rule["brightness"] == 127
    assert rule["color_temp_mireds"] == 370
    assert rule["transition_seconds"] == 2
    assert data["targets"][0]["device_id"] == "light1"
    assert data["sources"][0]["device_id"] == "sw_a"
    # ELI5: with no canopy chosen, the entry records a direct (None) chain.
    assert data["canopy"] is None
    # The finish step always records a compile summary for the UI.
    assert "compile" in data


# ---------------------------------------------------------------------------
# 2b — the bidirectional entry step (v0.1.14)
# ---------------------------------------------------------------------------
def test_wizard_direction_step_requires_choice(patched_registries) -> None:
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    # ELI5: the entry step shows a direction picker, and an unknown choice
    # is rejected rather than silently defaulting.
    form = _run(flow.async_step_user(None))
    assert form["step_id"] == "user"
    assert form["errors"] == {}

    bad = _run(flow.async_step_user({"direction": "sideways"}))
    assert bad["errors"].get("base") == "direction_required"


def test_wizard_source_first_flow(patched_registries) -> None:
    # ELI5: the user may start from the SWITCH. Step 1 picks a direction,
    # step 2 lists sources (unfiltered, no anchor yet), step 3 filters the
    # compatible targets, step 4 the behaviour, step 5 the (empty) canopy,
    # step 6 the finish.
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    # Direction → source-first.
    form = _run(flow.async_step_user({"direction": "source"}))
    assert form["step_id"] == "source"
    # ELI5: with no protocol anchor yet, every source is listed.
    values = _option_values(form, "sources")
    assert "sw_a" in values

    # Pick the source → wizard walks to the (filtered) target picker.
    form = _run(flow.async_step_source({"sources": ["sw_a"]}))
    assert form["step_id"] == "target"
    # ELI5: the Z-Wave lock is filtered out because the source is Zigbee.
    values = _option_values(form, "targets")
    assert "light1" in values
    assert "zw_lock" not in values

    # Target → behaviour → canopy (empty) → finish.
    _run(flow.async_step_target({"targets": ["light1"]}))
    _run(flow.async_step_behavior({"rule1_gesture": "1x Tap", "rule1_brightness_percent": 80}))
    _run(flow.async_step_canopy({"canopy": ""}))
    result = _run(flow.async_step_finish({"dry_run": True}))

    assert result["created"] is True
    data = result["data"]
    assert data["sources"][0]["device_id"] == "sw_a"
    assert data["targets"][0]["device_id"] == "light1"
    assert data["canopy"] is None


def _option_values(form: dict, key: str) -> set:
    """ELI5: pull the dropdown values out of a wizard picker form by key."""
    schema = form["data_schema"]._schema
    sel = next(v for v in schema.values() if hasattr(v, "config"))
    return {opt["value"] for opt in sel.config.options}


# ---------------------------------------------------------------------------
# 2c — the canopy (intermediate module) step produces a two-hop chain
# ---------------------------------------------------------------------------
def test_wizard_canopy_step_records_two_hop_chain(patched_registries) -> None:
    # ELI5: when the user names an intermediate canopy, the finish step
    # builds a source → canopy → target chain and compiles it with the
    # multi-hop compiler (which yields one binding per hop).
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    _run(flow.async_step_target({"targets": ["light1"]}))
    _run(flow.async_step_source({"sources": ["sw_a"]}))
    _run(flow.async_step_behavior({"rule1_gesture": "2x Tap", "rule1_brightness_percent": 60}))

    # ELI5: the canopy picker lists devices other than the source/target.
    form = _run(flow.async_step_canopy(None))
    assert form["step_id"] == "canopy"

    # Choose sw_b (a second Zigbee switch) as the intermediate relay.
    _run(flow.async_step_canopy({"canopy": "sw_b"}))

    # Finish (dry-run) — the compile summary should show two hops.
    result = _run(flow.async_step_finish({"dry_run": True}))
    assert result["created"] is True
    data = result["data"]
    assert data["canopy"] is not None
    assert data["canopy"]["device_id"] == "sw_b"
    # ELI5: two hops → two bindings in the compile summary.
    assert data["compile"]["bindings_added"] == 2
    assert data["compile"]["success"] is True


# ---------------------------------------------------------------------------
# 3 — storage round-trip: compile → save → load → list
# ---------------------------------------------------------------------------
def test_storage_roundtrip_serialises_and_lists() -> None:
    # ELI5: compile a Shelly binding (dry-run — nothing is sent), persist the
    # result to a fake ".storage/ship_anchor.bindings", then read it back with
    # a brand-new compiler and confirm it still lists.
    fake_hass = _make_fake_hass()
    store = _make_fake_store()
    compiler = AnchorCompiler(fake_hass, store, stored_data={})

    config = {
        "protocol": "shelly",
        "shelly_ip": "192.0.2.10",
        "target_ip": "192.0.2.20",
        "target_command": "on",
    }
    result = _run(compiler.async_compile_config(config, dry_run=True))
    assert result.bindings_added, "ELI5: a Shelly binding should compile"

    # Persist exactly the way the wizard's finish step does for a live run.
    compiler._stored_data.setdefault("bindings", []).extend(
        asdict(b) for b in result.bindings_added
    )
    _run(compiler._store.async_save(compiler._stored_data))

    # The fake store proves the payload is JSON-serialisable (it round-trips).
    persisted = json.loads(json.dumps(store.saved))
    assert persisted["bindings"][0]["source_ieee"] == "192.0.2.10"

    # A fresh compiler + fresh store re-hydrates the binding — mirroring
    # `_async_setup_runtime`, which loads the store BEFORE building the
    # compiler.
    reloaded_store = _make_fake_store(persisted=persisted)
    stored = _run(reloaded_store.async_load())
    reloaded = AnchorCompiler(fake_hass, reloaded_store, stored_data=stored)
    records = reloaded.list_bindings()
    assert len(records) == 1
    assert records[0].protocol == "shelly"
    assert records[0].source_ieee == "192.0.2.10"
    assert records[0].target_ieee == "192.0.2.20"


def test_list_bindings_skips_malformed_rows() -> None:
    # ELI5: a half-written store row must not crash the list view — it is
    # skipped with a warning and the good rows still come back.
    good = asdict(BindingRecord(
        protocol="shelly", source_ieee="192.0.2.10", target_ieee="192.0.2.20",
        clusters=[], group_id=None, preflight_done=True,
    ))
    compiler = AnchorCompiler(
        _make_fake_hass(), _make_fake_store(),
        stored_data={"bindings": [good, {"weird": 1}, {"protocol": "tasmota"}]},
    )
    records = compiler.list_bindings()
    # ELI5: only the fully-shaped row survives; the two malformed ones are
    # skipped rather than raising.
    assert len(records) == 1
    assert records[0].protocol == "shelly"


# ---------------------------------------------------------------------------
# 4 — erase_all is resilient (the v0.1.9 guarantee)
# ---------------------------------------------------------------------------
def test_erase_all_clears_store_even_with_bad_bindings() -> None:
    # ELI5: "erase everything" must ALWAYS clear the store, even if a binding
    # is broken or its device is offline. We mix one good Shelly binding with
    # a malformed row and confirm no exception escapes and the store is
    # cleared + saved.
    good = asdict(BindingRecord(
        protocol="shelly", source_ieee="192.0.2.10", target_ieee="192.0.2.20",
        clusters=[], group_id=None, preflight_done=True,
    ))
    store = _make_fake_store()
    compiler = AnchorCompiler(
        _make_fake_hass(), store,
        stored_data={"bindings": [good, {"broken": True}]},
    )

    # ELI5: this must not raise, regardless of per-binding transport failure.
    _run(compiler.async_erase_all())

    assert compiler._stored_data["bindings"] == []
    assert store.saved is not None
    assert store.saved["bindings"] == []




# ---------------------------------------------------------------------------
# 4 — v0.1.30 native-selector advanced manual route
# ---------------------------------------------------------------------------
def _manual_option_values(form: dict, key: str = "targets") -> set:
    """ELI5: pull the dropdown values out of a picker form by its key."""
    schema = form["data_schema"]._schema
    sel = next(k for k in schema if k.key == key)
    return {opt["value"] for opt in schema[sel].config.options}


def test_advanced_direction_routes_to_manual_picker(patched_registries) -> None:
    # ELI5 (v0.1.30): a third "advanced" option on the entry step opens the
    # native-selector manual picker instead of the protocol-filtered wizard.
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    form = _run(flow.async_step_user({"direction": "advanced"}))
    assert form["step_id"] == "manual"


def test_advanced_manual_device_builds_source_and_filters_targets(
    patched_registries
) -> None:
    # ELI5: picking a device by its *device_id* in the manual step builds the
    # same source entry the wizard does, then funnels into the protocol-
    # filtered TARGET picker — so a cross-bus pairing can never slip through.
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    # ELI5: the manual step opens with a native device/entity picker.
    form = _run(flow.async_step_manual(None))
    assert form["step_id"] == "manual"

    # ELI5: name the Zigbee switch by device_id → the source is recorded and
    # the wizard walks to the (protocol-filtered) target picker.
    form = _run(flow.async_step_manual({"manual_source_device_id": "sw_a"}))
    assert form["step_id"] == "target"
    assert flow._input["sources"][0]["device_id"] == "sw_a"

    # ELI5: the Z-Wave lock is filtered out because the source is Zigbee.
    values = _manual_option_values(form)
    assert "light1" in values
    assert "zw_lock" not in values


def test_advanced_manual_entity_resolves_device(patched_registries) -> None:
    # ELI5: an entity picker (e.g. an existing automation) is resolved back to
    # its owning device, and the device — not the entity — becomes the source.
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    form = _run(flow.async_step_manual({"manual_source_entity_id": "e_sw_b"}))
    assert form["step_id"] == "target"
    assert flow._input["sources"][0]["device_id"] == "sw_b"


def test_advanced_manual_empty_input_shows_errors(patched_registries) -> None:
    # ELI5: submitting the manual step with no device or entity is a clean,
    # self-correcting error — the form is re-shown, not a crash.
    flow = config_flow.ShipAnchorConfigFlow()
    flow.hass = _make_fake_hass()

    form = _run(flow.async_step_manual({}))
    assert form["step_id"] == "manual"
    assert form["errors"].get("base") == "source_required"