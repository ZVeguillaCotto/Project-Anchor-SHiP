"""
============================================================================
SHiP Anchor — tests/test_heartbeat_unit.py  (LAYER 1: UNIT TESTS, v0.1.18)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
Base-of-the-pyramid tests for the **Orphaned Mesh Heartbeat** added in
v0.1.18 (Pillar 1). Each hop in a multi-hop chain remembers when it last
heard a *genuine* (HMAC-checked, anti-replay-cleared) frame. From the gap
since that frame we derive a health verdict:

    • never heard anything   → UNKNOWN   (needs a re-handshake to baseline)
    • heard it within 1 win  → HEALTHY   (nothing to do)
    • 2 windows since        → DEGRADED  (warn the user)
    • 3+ windows since       → ORPHANED  (branch severed, flag it)

Anything but HEALTHY means the dashboard lights its degradation flag and
offers a "Re-handshake" action. These are pure-Python mirrors of the C core
``ship_heartbeat_*`` functions, so the unit layer stays fast and radio-free.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- Tests import the pure helpers from ``custom_components.ship_anchor.ship_protocol``.
- They assert the verdicts at the threshold boundaries and the re-handshake
  flag, plus that a fresh feed heals an orphaned hop back to HEALTHY.
"""

from __future__ import annotations

from custom_components.ship_anchor.ship_protocol import (
    HEALTH_DEGRADED,
    HEALTH_HEALTHY,
    HEALTH_ORPHANED,
    HEALTH_UNKNOWN,
    HEARTBEAT_INTERVAL_MS,
    heartbeat_check,
    heartbeat_feed,
    heartbeat_rehandshake_needed,
)


def _fresh_hop(last_seen_ms: int = 0, last_counter: int = 0) -> dict:
    return {"hop_index": 1, "last_counter": last_counter,
            "last_seen_ms": last_seen_ms, "state": HEALTH_UNKNOWN}


def test_never_fed_is_unknown() -> None:
    """ELI5: a hop that has never heard a genuine frame is UNKNOWN."""
    hop = _fresh_hop()
    check = heartbeat_check(hop, now_ms=1000)
    assert check["state"] == HEALTH_UNKNOWN
    assert check["missed_windows"] == 0
    assert heartbeat_rehandshake_needed(hop, now_ms=1000) is True


def test_fresh_feed_is_healthy() -> None:
    """ELI5: a genuine frame just seen marks the hop HEALTHY."""
    hop = _fresh_hop()
    heartbeat_feed(hop, counter=10, local_mono_ms=10000)
    check = heartbeat_check(hop, now_ms=10000)
    assert check["state"] == HEALTH_HEALTHY
    assert check["missed_windows"] == 0
    assert heartbeat_rehandshake_needed(hop, now_ms=10000) is False


def test_two_windows_is_degraded() -> None:
    """ELI5: 2 missed windows → DEGRADED, needs a re-handshake."""
    hop = _fresh_hop(last_seen_ms=10000, last_counter=5)
    now = 10000 + 2 * HEARTBEAT_INTERVAL_MS
    check = heartbeat_check(hop, now_ms=now)
    assert check["state"] == HEALTH_DEGRADED
    assert check["missed_windows"] == 2
    assert heartbeat_rehandshake_needed(hop, now_ms=now) is True


def test_three_windows_is_orphaned() -> None:
    """ELI5: 3+ missed windows → ORPHANED (branch severed)."""
    hop = _fresh_hop(last_seen_ms=10000, last_counter=5)
    now = 10000 + 3 * HEARTBEAT_INTERVAL_MS
    check = heartbeat_check(hop, now_ms=now)
    assert check["state"] == HEALTH_ORPHANED
    assert check["missed_windows"] == 3
    assert heartbeat_rehandshake_needed(hop, now_ms=now) is True


def test_fresh_feed_heals_orphan() -> None:
    """ELI5: a later genuine frame heals an orphaned hop back to HEALTHY."""
    hop = _fresh_hop(last_seen_ms=10000, last_counter=5)
    now = 10000 + 3 * HEARTBEAT_INTERVAL_MS
    assert heartbeat_check(hop, now_ms=now)["state"] == HEALTH_ORPHANED
    heartbeat_feed(hop, counter=6, local_mono_ms=now)
    check = heartbeat_check(hop, now_ms=now)
    assert check["state"] == HEALTH_HEALTHY
    assert check["missed_windows"] == 0
    assert heartbeat_rehandshake_needed(hop, now_ms=now) is False


def test_feed_updates_nonce_and_timestamp() -> None:
    """ELI5: a heartbeat feed must refresh BOTH the nonce and the clock."""
    hop = _fresh_hop()
    heartbeat_feed(hop, counter=42, local_mono_ms=50000)
    assert hop["last_counter"] == 42
    assert hop["last_seen_ms"] == 50000

def test_clock_skew_backward_clamps_to_healthy() -> None:
    """ELI5 (Finding C): a *backward* clock skew (now_ms earlier than the
    last genuine frame) must NOT be read as a huge gap. The C core clamps the
    unsigned elapsed time to 0 so the hop stays HEALTHY; this Python mirror
    must agree -- feeding now_ms=0 with last_seen_ms=60000 (one whole interval)
    must clamp missed windows to 0, never report ORPHANED.

    This is the parity guard: the C suite (test_ship_core.c) asserts the same
    verdict for the same inputs, so a divergence between the C and Python cores
    becomes a release blocker at the unit layer.
    """
    hop = _fresh_hop(last_seen_ms=60000, last_counter=7)
    # ELI5: now_ms is *earlier* than the last frame -- a skewed/rolled clock.
    check = heartbeat_check(hop, now_ms=0)
    assert check["state"] == HEALTH_HEALTHY, "skew must clamp, not orphan"
    assert check["missed_windows"] == 0, "skew must clamp missed to 0"
    assert heartbeat_rehandshake_needed(hop, now_ms=0) is False
