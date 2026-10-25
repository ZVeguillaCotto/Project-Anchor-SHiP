"""
============================================================================
SHiP Anchor — tests/test_mcp_unit.py   (LAYER 1: UNIT TESTS, v0.1.18)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
------------------------------------------------
The MCP bridge is the "front desk" an AI assistant uses to ask SHiP Anchor
questions without reading every project file. In v0.1.18 it grew from *seven*
tools to *eight*: the new one is ``ship_diagnose_mesh`` — the "is my mesh still
alive?" tool. It reads a snapshot of each hop's heartbeat (when it last heard a
genuine frame, its anti-replay nonce) and reports a health verdict per hop
plus whether any branch needs a re-handshake, and a modeled multi-hop latency.

It is a *preview*: it only reads; it never touches a device.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- We import the MCP module by path (it lives in the top-level ``mcp/`` folder,
  which is not an importable package), after conftest has laid down the
  Home Assistant / voluptuous stubs.
- We assert the full eight-tool surface, that the diagnostic verdicts map the
  heartbeat thresholds (healthy / degraded / orphaned), that an all-healthy
  snapshot reports "no re-handshake", and that the compact schema stays under
  its 1,500 lexical-token tripwire.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_mcp_module():
    """Import mcp/ship_anchor_mcp.py by path (it is not a package)."""
    path = os.path.join(REPO_ROOT, "mcp", "ship_anchor_mcp.py")
    spec = importlib.util.spec_from_file_location("ship_anchor_mcp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The eight-tool surface
# ---------------------------------------------------------------------------
def test_mcp_exposes_eight_tools():
    module = _load_mcp_module()
    names = {tool["name"] for tool in module.TOOL_DEFINITIONS}
    expected = {
        "ship_inspect_device",
        "ship_compile_binding",
        "ship_bake_behavior",
        "ship_list_bindings",
        "ship_encode_19b_frame",
        "ship_decode_verify_19b_frame",
        "ship_run_verification_suite",
        "ship_diagnose_mesh",
    }
    assert names == expected
    assert len(names) == 8


def test_mcp_schema_stays_under_token_ceiling():
    module = _load_mcp_module()
    count = module._schema_token_count()
    assert count < 1500, "MCP schema grew past its 1,500 lexical-token tripwire"
    # Honest: it is an *exact lexical* count, not model BPE tokens.
    assert isinstance(count, int) and count > 0


# ---------------------------------------------------------------------------
# The v0.1.18 diagnostic tool
# ---------------------------------------------------------------------------
def test_diagnose_mesh_one_healthy_one_degraded_one_orphaned():
    module = _load_mcp_module()
    # now_ms = 240000; windows = 60000 ms each.
    #   hop 1 last_seen 240000 → 0 missed → healthy
    #   hop 2 last_seen 120000 → 2 missed → degraded
    #   hop 3 last_seen 60000  → 3 missed → orphaned
    result = asyncio.run(
        module._call_tool(
            "ship_diagnose_mesh",
            {
                "now_ms": 240000,
                "hops": [
                    {"hop_index": 1, "last_counter": 41, "last_seen_ms": 240000},
                    {"hop_index": 2, "last_counter": 39, "last_seen_ms": 120000},
                    {"hop_index": 3, "last_counter": 12, "last_seen_ms": 60000},
                ],
            },
        )
    )
    assert result["hop_count"] == 3
    assert result["orphaned_hops"] == 1
    assert result["degraded_hops"] == 1
    assert result["needs_re_handshake"] is True

    by_index = {h["hop_index"]: h for h in result["hops"]}
    assert by_index[1]["state"] == "healthy"
    assert by_index[1]["re_handshake_needed"] is False
    assert by_index[1]["missed_windows"] == 0
    assert by_index[2]["state"] == "degraded"
    assert by_index[2]["re_handshake_needed"] is True
    assert by_index[3]["state"] == "orphaned"
    assert by_index[3]["re_handshake_needed"] is True
    # The modeled airtime per hop is the 19-byte frame at 250 kbps.
    assert result["per_hop_airtime_ms"] == pytest.approx(0.864, rel=1e-2)


def test_diagnose_mesh_all_healthy_needs_no_rehandshake():
    module = _load_mcp_module()
    result = asyncio.run(
        module._call_tool(
            "ship_diagnose_mesh",
            {
                "now_ms": 60000,
                "hops": [
                    {"hop_index": 1, "last_counter": 5, "last_seen_ms": 60000},
                    {"hop_index": 2, "last_counter": 6, "last_seen_ms": 60000},
                ],
            },
        )
    )
    assert result["hop_count"] == 2
    assert result["orphaned_hops"] == 0
    assert result["degraded_hops"] == 0
    assert result["needs_re_handshake"] is False
    for hop in result["hops"]:
        assert hop["state"] == "healthy"


def test_diagnose_mesh_never_fed_hop_is_unknown():
    module = _load_mcp_module()
    result = asyncio.run(
        module._call_tool(
            "ship_diagnose_mesh",
            {
                "now_ms": 120000,
                "hops": [
                    {"hop_index": 1, "last_counter": 0, "last_seen_ms": 0},
                ],
            },
        )
    )
    # A hop that has never been fed is UNKNOWN and needs a re-handshake.
    assert result["hops"][0]["state"] == "unknown"
    assert result["needs_re_handshake"] is True


def test_diagnose_mesh_rejects_non_list_hops():
    module = _load_mcp_module()
    with pytest.raises(ValueError):
        asyncio.run(
            module._call_tool("ship_diagnose_mesh", {"hops": {"not": "a list"}})
        )
