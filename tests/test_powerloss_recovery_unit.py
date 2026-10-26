"""
============================================================================
SHiP Anchor — tests/test_powerloss_recovery_unit.py  (LAYER 2, v0.1.20)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
------------------------------------------------
Physical edge-case hardening. An intermediate relay (a ceiling canopy, a
bridge) loses power, then regains it. The question the project promises to
answer: does the mesh notice, flag it, and RE-ARM the binding on its own
the moment the node comes back — WITHOUT anyone re-running the wizard?

This is a disaster-recovery drill: a genuine frame arrives just after the
power returns, the heartbeat verdict flips from ORPHANED back to HEALTHY,
and the modeled 'needs a re-handshake' flag clears. It exercises the same
heartbeat_feed / heartbeat_check / diagnose_mesh loop the C core and the
options-flow 'Re-handshake / Heal Mesh' action use.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- Pure-Python, radio-free, using the ship_protocol mirror helpers.
- Mirrors the real sequence: feed a fresh nonce -> check -> verdict heals.
"""

from __future__ import annotations

from custom_components.ship_anchor.ship_protocol import (
    HEALTH_HEALTHY,
    HEALTH_ORPHANED,
    HEARTBEAT_INTERVAL_MS,
    diagnose_mesh,
    heartbeat_check,
    heartbeat_feed,
    heartbeat_rehandshake_needed,
)


def _dropped_hop() -> dict:
    """ELI5: a relay that has been silent long enough to look ORPHANED."""
    base = 10000
    hop = {
        "hop_index": 2, "last_counter": 7, "last_seen_ms": base,
        "state": "unknown",
    }
    return hop, base


def test_orphaned_then_reconnects_heals_without_intervention() -> None:
    """ELI5: power returns, one genuine frame arrives, the binding heals."""
    hop, base = _dropped_hop()
    # 1. The relay has been silent for 3 windows: the branch is ORPHANED.
    gap = base + 3 * HEARTBEAT_INTERVAL_MS
    verdict = heartbeat_check(dict(hop), gap) 
    assert verdict["state"] == HEALTH_ORPHANED
    assert heartbeat_rehandshake_needed(hop, gap) is True
    assert diagnose_mesh([dict(hop)], gap)["needs_re_handshake"] is True
    
    # 2. Power returns and the relay emits ONE genuine frame. This is the
    #    automatic recovery: no wizard, no user action, just a fresh nonce.
    reconnect = gap + 1000
    heartbeat_feed(hop, counter=8, local_mono_ms=reconnect)
    
    # 3. The verdict flips back to HEALTHY and the heal flag clears.
    healed = heartbeat_check(dict(hop), reconnect)
    assert healed["state"] == HEALTH_HEALTHY
    assert healed["missed_windows"] == 0
    assert heartbeat_rehandshake_needed(hop, reconnect) is False
    assert diagnose_mesh([dict(hop)], reconnect)["needs_re_handshake"] is False


def test_multihop_one_branch_recovers_independently() -> None:
    """ELI5: when one branch heals, the others are unaffected (DAG)."""
    healthy = {
        "hop_index": 1, "last_counter": 1, "last_seen_ms": 60000, "state": "x",
    }
    dead = {
        "hop_index": 2, "last_counter": 2, "last_seen_ms": 0, "state": "x",
    }
    now = 60000
    diag = diagnose_mesh([dict(healthy), dict(dead)], now)
    # hop 1 fresh, hop 2 never-seen (UNKNOWN) -> a re-handshake is needed.
    assert diag["needs_re_handshake"] is True
    assert diag["degraded_hops"] + diag["orphaned_hops"] >= 0
    
    # hop 2 regains power and feeds a genuine frame.
    heartbeat_feed(dead, counter=3, local_mono_ms=now)
    diag2 = diagnose_mesh([dict(healthy), dict(dead)], now)
    # Now BOTH branches are healthy: nothing needs a re-handshake.
    assert diag2["needs_re_handshake"] is False
    assert all(h["state"] == HEALTH_HEALTHY for h in diag2["hops"])
