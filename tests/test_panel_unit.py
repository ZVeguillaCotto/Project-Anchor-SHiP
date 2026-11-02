# SHiP Anchor -- v0.1.33 panel / mesh API foundation unit tests.
#
# These exercise the PURE data providers in panel.py under the conftest stubs
# (no Home Assistant, no radios). They prove the control center returns a sane,
# ELI5-labelled, non-actuating view of the world -- and that the compiler is
# always called in dry-run mode.
from __future__ import annotations

import asyncio


def _run(coro):
    """ELI5: run one async provider on its own fresh event loop so the
    test never depends on pytest's loop state."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()

from custom_components.ship_anchor.panel import (
    async_build_compile_preview,
    async_handle_panel_command,
    build_bindings_overview,
    build_dashboard_snapshot,
    build_mesh_telemetry,
)


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------
class FakeBinding:
    def __init__(self, protocol, source, target, clusters=None, group_id=7,
                 preflight_done=False, timestamp=""):
        self.protocol = protocol
        self.source_ieee = source
        self.target_ieee = target
        self.clusters = clusters or []
        self.group_id = group_id
        self.preflight_done = preflight_done
        self.timestamp = timestamp


class FakeCompileResult:
    def __init__(self, bindings_added=None, warnings=None, errors=None,
                 dry_run_payloads=None, skipped=0):
        self.success = True
        self.bindings_added = bindings_added or []
        self.warnings = warnings or []
        self.errors = errors or []
        self.dry_run_payloads = dry_run_payloads or []
        self.skipped = skipped


class FakeCompiler:
    def __init__(self, bindings, drift_report):
        self._bindings = bindings
        self._drift = drift_report
        self.last_dry_run = None

    def list_bindings(self):
        return list(self._bindings)

    def detect_binding_drift(self):
        return list(self._drift)

    async def async_compile_config(self, config, dry_run=True):
        self.last_dry_run = dry_run
        return FakeCompileResult(
            bindings_added=[
                FakeBinding(config.get("protocol", "shelly"),
                              config.get("source_ieee", "0.0.0.0"),
                              config.get("target_ieee", "0.0.0.1"), [6], 7)
            ]
         )


def make_runtime(compiler=None, nonce_windows=None, drift_report=None):
    return {
        "compiler": compiler,
        "nonce_windows": nonce_windows or {},
        "drift_report": drift_report or [],
      }


# --------------------------------------------------------------------------
# 1. Empty runtime never crashes
# --------------------------------------------------------------------------
def test_empty_runtime_is_fine():
    out = build_bindings_overview(None)
    assert out["eli5_summary"]
    assert out["summary"] == {"total_bindings": 0, "drifted": 0, "healthy": 0}

    mesh = build_mesh_telemetry(None)
    assert mesh["eli5_summary"]
    assert mesh["hop_count"] == 0
    assert mesh["orphaned_hops"] == 0


# --------------------------------------------------------------------------
# 2. Bindings overview + drift (drift lives in its own list)
# --------------------------------------------------------------------------
def test_bindings_overview_lists_bindings_and_drift():
    bindings = [FakeBinding("shelly", "192.0.2.10", "192.0.2.20", [6], 7,
                             preflight_done=True,
                             timestamp="2026-07-01T00:00:00")]
    drift = [{"binding": {"protocol": "shelly", "source_ieee": "192.0.2.10",
                           "target_ieee": "192.0.2.20", "group_id": 7,
                           "clusters": [6]},
               "drifted": True, "missing": ["entity"], "reason": "renamed"}]
    fc = FakeCompiler(bindings, drift)
    runtime = make_runtime(compiler=fc, drift_report=drift)
    out = build_bindings_overview(runtime)
    assert out["summary"]["total_bindings"] == 1
    assert out["summary"]["drifted"] == 1
    assert out["summary"]["healthy"] == 0
    # The binding row carries the device fields, not a per-row drift flag.
    assert out["bindings"][0]["protocol"] == "shelly"
    assert out["bindings"][0]["group"] == 7
    assert out["bindings"][0]["preflight_done"] is True
    # The drift flag lives in the separate drift list.
    assert out["drift"][0]["drifted"] is True
    assert "attention" in out["eli5_summary"].lower()


# --------------------------------------------------------------------------
# 3. Mesh telemetry: one binding -> one hop, with topology + latency
# --------------------------------------------------------------------------
def test_mesh_telemetry_one_binding_one_hop():
    bindings = [FakeBinding("zha", "0011223344556677", "0011223344556678",
                             [6], 3, preflight_done=False)]
    runtime = make_runtime(compiler=FakeCompiler(bindings, []))
    out = build_mesh_telemetry(runtime)
    assert out["hop_count"] == 1
    assert len(out["hops"]) == 1
    assert len(out["topology"]) == 1
    assert out["per_hop_airtime_ms"] == 0.864
    assert out["eli5_summary"]


def test_mesh_telemetry_orphaned_via_heartbeat():
    # A hop whose last frame is long overdue is the orphaned verdict the
     # dashboard must surface -- this is the timing path (not the drift flag).
    from custom_components.ship_anchor import ship_protocol as sp

    # A nonzero last_seen that is long overdue (> HEARTBEAT_ORPHANED windows).
    hop = {"hop_index": 0, "last_counter": 0, "last_seen_ms": 1}
    out = sp.diagnose_mesh([hop], now_ms=1 + sp.HEARTBEAT_INTERVAL_MS * 3)
    assert out["orphaned_hops"] == 1
    assert out["needs_re_handshake"] is True


# --------------------------------------------------------------------------
# 4. Compile preview is always dry-run and never actuating
# --------------------------------------------------------------------------
def test_compile_preview_forces_dry_run():
    bindings = [FakeBinding("shelly", "192.0.2.10", "192.0.2.20", [6], 7)]
    fc = FakeCompiler(bindings, [])
    res = _run(
        async_build_compile_preview(
            fc,
             {"protocol": "shelly", "source_ieee": "192.0.2.10",
              "target_ieee": "192.0.2.20"}
         )
      )
    assert res["dry_run"] is True
    assert res["ok"] is True
    assert res["eli5_summary"]
    assert fc.last_dry_run is True


def test_compile_preview_none_compiler_is_calm():
    res = _run(
        async_build_compile_preview(None, {"protocol": "shelly"})
      )
    assert res["dry_run"] is True
    assert res["ok"] is False
    assert "no compiler" in res["eli5_summary"].lower()


# --------------------------------------------------------------------------
# 5. Command dispatcher
# --------------------------------------------------------------------------
def test_command_ping_and_unknown():
    runtime = make_runtime()
    ping = _run(
        async_handle_panel_command("ping", runtime)
      )
    assert ping["ok"] is True
    assert "eli5_summary" in ping

    err = _run(
        async_handle_panel_command("does_not_exist", runtime, {})
      )
    assert err["ok"] is False
    assert "eli5_summary" in err


def test_command_snapshot_shape():
    bindings = [FakeBinding("zha", "0011223344556677", "0011223344556678",
                             [6], 3)]
    runtime = make_runtime(compiler=FakeCompiler(bindings, []))
    snap = _run(
        async_handle_panel_command("snapshot", runtime, {})
      )
    assert "bindings" in snap and "mesh" in snap and "engine" in snap
    assert snap["panel"]["name"] == "ship_anchor_dashboard"
    assert "nonce_windows" in snap


# --------------------------------------------------------------------------
# 6. Full snapshot stitches the pieces
# --------------------------------------------------------------------------
def test_dashboard_snapshot_shape():
    runtime = make_runtime(compiler=FakeCompiler([], []), nonce_windows={"3": 5})
    snap = build_dashboard_snapshot(runtime)
    assert "bindings" in snap
    assert "mesh" in snap
    assert "nonce_windows" in snap
    assert snap["panel"]["name"] == "ship_anchor_dashboard"
    assert "control center" in snap["eli5_summary"].lower()
