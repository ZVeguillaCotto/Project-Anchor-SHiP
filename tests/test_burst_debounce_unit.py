"""
============================================================================
SHiP Anchor — tests/test_burst_debounce_unit.py  (LAYER 1, v0.1.20)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
------------------------------------------------
Physical edge-case hardening. A person can mash a button far faster than a
single gesture is *meant* to mean. A 5-tap intent is one thing; a 30-tap
stumble is a bug. If every tap became its own radio command, a clumsy press
could flood the mesh with a "command storm". The **burst debounce** is a
sliding 500 ms window: the first tap opens it and passes, up to 5 taps are
allowed through, and the 6th+ inside the window are SWALLOWED so the radio
stays quiet.

These are the pure-Python mirrors of the C core's ship_burst_accept /
ship_burst_reset (Test 12 in sim/test_ship_core.c), so the unit layer stays
fast and radio-free. The C core and this mirror must agree — a divergence
is a release blocker.
"""

from __future__ import annotations

from custom_components.ship_anchor.ship_protocol import (
    BURST_MAX_TAPS,
    BURST_WINDOW_MS,
    burst_accept,
    burst_reset,
)


def _fresh_lane() -> dict:
    """ELI5: an empty per-(group, gesture) debounce lane."""
    return {"window_start_ms": 0, "taps_in_window": 0, "accepted": 0, "dropped": 0}


def test_first_five_taps_pass() -> None:
    """ELI5: up to BURST_MAX_TAPS taps inside the window are accepted."""
    lane = _fresh_lane()
    results = [burst_accept(lane, 1000 + i * 50) for i in range(BURST_MAX_TAPS)]
    assert results == [True] * BURST_MAX_TAPS
    assert lane["accepted"] == BURST_MAX_TAPS
    assert lane["dropped"] == 0


def test_sixth_tap_swallowed() -> None:
    """ELI5: the 6th tap in the window is the storm — it is dropped."""
    lane = _fresh_lane()
    for _ in range(BURST_MAX_TAPS):
        assert burst_accept(lane, 1000) is True
    # A 6th tap, still inside the 500 ms window, must be swallowed.
    assert burst_accept(lane, 1000 + 40) is False
    assert lane["dropped"] == 1
    assert lane["accepted"] == BURST_MAX_TAPS


def test_gap_reopens_window() -> None:
    """ELI5: a gap longer than the window opens a fresh window and resets."""
    lane = _fresh_lane()
    for _ in range(BURST_MAX_TAPS):
        burst_accept(lane, 1000)
    # A tap after a 500 ms+ gap opens a new window: accepted, count reset.
    assert burst_accept(lane, 1000 + BURST_WINDOW_MS + 1) is True
    assert lane["taps_in_window"] == 1
    assert lane["accepted"] == BURST_MAX_TAPS + 1


def test_reset_clears_lane() -> None:
    """ELI5: burst_reset forgets any in-flight burst so the next is clean."""
    lane = _fresh_lane()
    for _ in range(BURST_MAX_TAPS):
        burst_accept(lane, 1000)
    burst_reset(lane, 2000)
    assert lane["taps_in_window"] == 0
    assert lane["window_start_ms"] == 2000
    # A fresh lane accepts the next tap.
    assert burst_accept(lane, 3000) is True


def test_null_lane_is_noop() -> None:
    """ELI5: a None lane defensively swallows (returns False), no crash."""
    assert burst_accept(None, 1000) is False


def test_window_constants_match_c_core() -> None:
    """ELI5: the Python window mirrors the C core SHIP_BURST_* constants."""
    assert BURST_MAX_TAPS == 5
    assert BURST_WINDOW_MS == 500
