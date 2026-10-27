"""
============================================================================
SHiP Anchor - tests/test_missing_extension.py     (LAYER 2, v0.1.21)
============================================================================

LAYER 1 - THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
The big question this suite answers: *what happens on a locked-down Home
Assistant box that has no C compiler at all?* The native C accelerator is an
optional speed-up; the project must run 100% on the pure-Python engine when
the compiled binary is missing, corrupt, or simply never shipped. That is the
HACS / HAOS guarantee.

This suite simulates three worlds and proves the right engine wins in each:
       1. The native module is ABSENT     -> the pure-Python engine is chosen.
       2. The native module is PRESENT    -> the native accelerator is chosen and
           must speak the exact same operation interface.
       3. The native module is CORRUPT    -> we fall back to pure Python instead of
           crashing start-up.
It also proves every radio-language operation runs identically on the
pure-Python engine: framing, SipHash, LEAT lookup, burst debounce, heartbeat,
and mesh telemetry - so the compiler, options flow, handlers, and telemetry
all work with no compiled binary present.

LAYER 2 - THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- The tests import the runtime seam (runtime.py) and the compiler, plus the
  fake hass/store from conftest.py. Home Assistant / voluptuous are stubbed
  by conftest, so nothing real needs to be installed.
- A fake "ship_anchor_native" module is injected into sys.modules to stand in
  for a compiled accelerator; it is removed afterwards so the next test
  starts from a clean (extension-missing) state.
"""

from __future__ import annotations

import json
import os
import sys
import types
from types import SimpleNamespace

from custom_components.ship_anchor.runtime import (
     NATIVE_MODULE_NAME, OPERATIONS, PURE_PYTHON_ENGINE, ShipEngine,
        _try_load_native, engine_summary, select_engine,
)
from custom_components.ship_anchor import ship_protocol
from custom_components.ship_anchor.compiler import AnchorCompiler
from custom_components.ship_anchor.config_flow import ShipAnchorOptionsFlow
from tests.conftest import _make_fake_hass, _make_fake_store


def _clear_native() -> None:
    """ELI5: guarantee the next test sees a world with no native binary."""
    sys.modules.pop(NATIVE_MODULE_NAME, None)


def test_pure_python_engine_is_the_default_when_extension_missing() -> None:
    """ELI5: no native binary -> the pure-Python engine is chosen."""
    _clear_native()
    engine = select_engine()
    assert isinstance(engine, ShipEngine)
    assert engine.is_native is False
    assert _try_load_native() is None
    summary = engine_summary()
    assert summary["pure_python_fallback"] is True
    assert set(summary["operations"]) == set(OPERATIONS)


def test_pure_python_engine_exposes_every_operation() -> None:
    """ELI5: the fallback engine speaks the full interface, identically."""
    _clear_native()
    engine = select_engine()
    for op in OPERATIONS:
        assert op in engine
        assert callable(getattr(engine, op))
    assert engine is PURE_PYTHON_ENGINE


def test_pure_python_framing_roundtrip() -> None:
    """ELI5: build a 19-byte frame and verify it - no compiler needed."""
    _clear_native()
    engine = select_engine()
    args = {
        "key_hex": "00" * 16,
        "pan_id": 0x1234, "src_short": 0x0001, "dst_short": 0xFFFF,
        "seq_num": 7, "group_id": 1, "domain": 1, "gesture": 1,
        "param_1": 128, "param_2": 0, "param_3": 0, "counter": 3,
    }
    encoded = engine.encode_19b_frame(args)
    assert encoded["length"] == 19
    decoded = engine.decode_verify_19b_frame({
        "frame_hex": encoded["frame_hex"], "key_hex": "00" * 16, "last_counter": 2,
    })
    assert decoded["valid"] is True
    assert decoded["decoded"]["group_id"] == 1
    assert decoded["decoded"]["gesture"] == 1


def test_pure_python_siphash_matches_reference() -> None:
    """ELI5: the engine SipHash equals the reference codec."""
    _clear_native()
    engine = select_engine()
    data = b"the anchor stands"
    key = bytes(range(16))
    assert engine.siphash24(data, key) == ship_protocol.siphash24(data, key)


def test_pure_python_leat_lookup_matches_compiler_table() -> None:
    """ELI5: a compiler-built cheat sheet is matched by the engine."""
    _clear_native()
    hass = _make_fake_hass()
    store = _make_fake_store({})
    compiler = AnchorCompiler(hass, store, {})
    table = compiler.build_leat_table([
        {"gesture": "1x Tap", "brightness": 128},
        {"gesture": "3x Tap", "brightness": 40, "color_temp_mireds": 350},
    ], group_id=5)
    assert len(table) == 2
    engine = select_engine()
    hit = engine.leat_match(table, {
        "group_id": 5, "domain": table[0]["domain"], "gesture": table[0]["gesture"],
    })
    assert hit == 0
    miss = engine.leat_match(table, {
        "group_id": 6, "domain": table[0]["domain"], "gesture": table[0]["gesture"],
    })
    assert miss == -1


def test_pure_python_burst_and_heartbeat() -> None:
    """ELI5: burst debounce + heartbeat run on the pure-Python engine."""
    _clear_native()
    engine = select_engine()
    lane = engine.burst_reset({
        "window_start_ms": 0, "taps_in_window": 0, "accepted": 0, "dropped": 0, }, 1000)
    results = [engine.burst_accept(lane, 1000 + i * 50) for i in range(6)]
    assert results[:5] == [True] * 5
    assert results[5] is False
    assert lane["dropped"] == 1
    hop = engine.heartbeat_feed({}, 9, 5000)
    assert engine.heartbeat_check(hop, 5000)["state"] == "healthy"


def test_pure_python_telemetry_diagnose() -> None:
    """ELI5: the dashboard telemetry verdict runs on the pure-Python engine."""
    _clear_native()
    engine = select_engine()
    diag = engine.diagnose_mesh([
        {"hop_index": 1, "last_counter": 1, "last_seen_ms": 60000},
        {"hop_index": 2, "last_counter": 2, "last_seen_ms": 0},
    ], now_ms=60000)
    assert diag["needs_re_handshake"] is True
    assert diag["hops"][1]["state"] == "unknown"
    assert "badge" in diag["hops"][0]


def _install_fake_native(operations: dict) -> None:
    """ELI5: stand in for a compiled accelerator with the given ops."""
    mod = types.ModuleType(NATIVE_MODULE_NAME)
    for name, fn in operations.items():
        setattr(mod, name, fn)
    sys.modules[NATIVE_MODULE_NAME] = mod


def test_native_accelerator_is_selected_when_present() -> None:
    """ELI5: a present native binary is chosen and speaks the full API."""
    _clear_native()
    ops = {op: getattr(ship_protocol, op) for op in OPERATIONS}
    _install_fake_native(ops)
    try:
        engine = select_engine()
        assert engine.is_native is True
        assert engine.name == "native"
        for op in OPERATIONS:
            assert op in engine
        assert engine.leat_match([], {}) == -1
    finally:
        _clear_native()


def test_corrupt_native_falls_back_to_pure_python() -> None:
    """ELI5: a native binary missing an op falls back, never crashes."""
    _clear_native()
    ops = {op: getattr(ship_protocol, op) for op in OPERATIONS}
    del ops["leat_match"]         # a corrupt / incompatible build
    _install_fake_native(ops)
    try:
        assert _try_load_native() is None
        engine = select_engine()
        assert engine.is_native is False
    finally:
        _clear_native()


def test_runtime_setup_surfaces_engine_on_pure_python() -> None:
    """ELI5: the options flow renders telemetry on the pure-Python engine."""
    _clear_native()
    summary = engine_summary()
    assert summary["pure_python_fallback"] is True
    data = {
        "source_device_id": "sw_a", "source_name": "Hall Switch",
        "protocol": "ZIGBEE_ZHA",
        "sources": [{"device_id": "sw_a", "name": "Hall Switch"}],
        "canopy": None,
        "targets": [{"device_id": "light1", "name": "Kitchen Light", "kind": "light"}],
        "use_group": False, "group_name": "", "rules": [],
        "heartbeat_now_ms": 60000,
        "heartbeats": [{"hop_index": 1, "last_counter": 1, "last_seen_ms": 60000}],
    }
    entry = SimpleNamespace(domain="ship_anchor", data=data, options={})
    hass = _make_fake_hass()
    flow = ShipAnchorOptionsFlow(entry)
    flow.hass = hass
    rendered = flow._render_dashboard(data)
    assert "Live mesh telemetry" in rendered
    assert "Branch health" in rendered
    assert "healthy" in rendered


def test_manifest_declares_pure_python_runtime() -> None:
    """ELI5: the manifest proves no C toolchain is required for install."""
    manifest_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "custom_components", "ship_anchor", "manifest.json",
    )
    manifest = json.load(open(manifest_path, encoding="utf-8"))
    assert manifest["runtime"]["requires_c_toolchain"] is False
    assert manifest["runtime"]["default_engine"] == "pure_python"
    assert manifest["runtime"]["native_accelerator_optional"] is True
    for req in manifest.get("requirements", []):
        assert "gcc" not in req and "clang" not in req and "build-essential" not in req
