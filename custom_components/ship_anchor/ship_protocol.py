"""
SHiP Anchor Python protocol codec.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This is the Python-side reference codec for constructing and checking SHiP
radio envelopes. The MCP bridge calls these functions instead of maintaining a
second copy of the packet rules.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Implements the byte-compatible 19-byte packet layout, SipHash-2-4 truncation,
Mesh-RTC's compact tag format, and anti-replay counter validation.
"""

from __future__ import annotations

import struct

SHIP_FRAME_SIZE = 19
SHIP_DOMAIN_MESH_CLOCK = 0xF


def siphash24(data: bytes, key: bytes) -> int:
    """Return the full 64-bit SipHash-2-4 digest for a 16-byte key."""
    if len(key) != 16:
        raise ValueError("SipHash key must contain exactly 16 bytes")
    mask = 0xFFFFFFFFFFFFFFFF
    k0, k1 = struct.unpack("<QQ", key)
    v0 = k0 ^ 0x736F6D6570736575
    v1 = k1 ^ 0x646F72616E646F6D
    v2 = k0 ^ 0x6C7967656E657261
    v3 = k1 ^ 0x7465646279746573

    def rotl(value: int, shift: int) -> int:
        return ((value << shift) | (value >> (64 - shift))) & mask

    def round_once() -> None:
        nonlocal v0, v1, v2, v3
        v0 = (v0 + v1) & mask
        v1 = rotl(v1, 13) ^ v0
        v0 = rotl(v0, 32)
        v2 = (v2 + v3) & mask
        v3 = rotl(v3, 16) ^ v2
        v0 = (v0 + v3) & mask
        v3 = rotl(v3, 21) ^ v0
        v2 = (v2 + v1) & mask
        v1 = rotl(v1, 17) ^ v2
        v2 = rotl(v2, 32)

    full_blocks = len(data) // 8
    for index in range(full_blocks):
        block = struct.unpack_from("<Q", data, index * 8)[0]
        v3 ^= block
        round_once()
        round_once()
        v0 ^= block

    tail = data[full_blocks * 8:]
    last = (len(data) & 0xFF) << 56
    for index, byte in enumerate(tail):
        last |= byte << (8 * index)
    v3 ^= last
    round_once()
    round_once()
    v0 ^= last
    v2 ^= 0xFF
    for _ in range(4):
        round_once()
    return (v0 ^ v1 ^ v2 ^ v3) & mask


def _key_from_hex(key_hex: str) -> bytes:
    try:
        key = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise ValueError("key_hex must contain valid hexadecimal") from exc
    if len(key) != 16:
        raise ValueError("key_hex must encode exactly 16 bytes (32 hex characters)")
    return key


def encode_19b_frame(args: dict) -> dict:
    """Encode frame fields and return the frame plus a human-readable breakdown."""
    key = _key_from_hex(args["key_hex"])
    fields = (
        "pan_id", "src_short", "dst_short", "seq_num", "group_id",
        "domain", "gesture", "param_1", "param_2", "param_3", "counter",
    )
    values = {name: int(args[name]) for name in fields}
    limits = {
        "pan_id": 0xFFFF, "src_short": 0xFFFF, "dst_short": 0xFFFF,
        "seq_num": 0xFF, "group_id": 0xFF, "domain": 0xF, "gesture": 0xF,
        "param_1": 0xFF, "param_2": 0xFF, "param_3": 0xFF, "counter": 0xFFFF,
    }
    for name, value in values.items():
        if not 0 <= value <= limits[name]:
            raise ValueError("{} must be between 0 and {}".format(name, limits[name]))

    destination = values["dst_short"]
    fcf = 0x8841 if destination == 0xFFFF else 0x8861
    mac = struct.pack(
        "<HBHHH", fcf, values["seq_num"], values["pan_id"],
        destination, values["src_short"],
    )
    command = (values["domain"] << 4) | values["gesture"]
    prefix = (
        bytes((0xA5, values["group_id"]))
        + struct.pack("<H", values["counter"])
        + bytes((command,))
    )
    utc_epoch = None
    if values["domain"] == SHIP_DOMAIN_MESH_CLOCK:
        if "utc_epoch" not in args:
            raise ValueError("utc_epoch is required when domain is Mesh-RTC (0xF)")
        utc_epoch = int(args["utc_epoch"])
        if not 0 <= utc_epoch <= 0xFFFFFFFF:
            raise ValueError("utc_epoch must be a 32-bit unsigned integer")
        payload = prefix + utc_epoch.to_bytes(4, "little")
        tag = siphash24(payload, key) & 0xFF
        frame = mac + payload + bytes((tag,))
    else:
        payload = prefix + bytes(
            (values["param_1"], values["param_2"], values["param_3"])
        )
        tag = siphash24(payload, key) & 0xFFFF
        frame = mac + payload + struct.pack("<H", tag)
    if len(frame) != SHIP_FRAME_SIZE:
        raise AssertionError(
            "SHiP frame encoder produced {} bytes".format(len(frame))
        )

    return {
        "frame_hex": frame.hex(),
        "length": len(frame),
        "byte_breakdown": [
            {"offset": index, "hex": "{:02x}".format(value)}
            for index, value in enumerate(frame)
        ],
        "airtime_ms": 0.864,
        "airtime_note": "(6 PHY bytes + 21-byte PSDU) × 32 µs at 250 kbps",
        "domain": values["domain"],
        "utc_epoch": utc_epoch,
        "tag_hex": "{:02x}".format(tag) if utc_epoch is not None else "{:04x}".format(tag),
    }


def decode_verify_19b_frame(args: dict) -> dict:
    """Verify SHiP magic, authentication tag, and freshness before decoding."""
    try:
        frame = bytes.fromhex(args["frame_hex"])
    except ValueError as exc:
        raise ValueError("frame_hex must contain valid hexadecimal") from exc
    if len(frame) != SHIP_FRAME_SIZE:
        raise ValueError("SHiP frame must be exactly 19 bytes")
    key = _key_from_hex(args["key_hex"])
    if frame[9] != 0xA5:
        raise ValueError("Invalid SHiP magic byte at offset 9")

    command = frame[13]
    domain = command >> 4
    if domain == SHIP_DOMAIN_MESH_CLOCK:
        expected_tag = siphash24(frame[9:18], key) & 0xFF
        if frame[18] != expected_tag:
            raise ValueError("Mesh-RTC SipHash tag does not match")
    else:
        expected_tag = siphash24(frame[9:17], key) & 0xFFFF
        if int.from_bytes(frame[17:19], "little") != expected_tag:
            raise ValueError("SipHash tag does not match")

    counter = int.from_bytes(frame[11:13], "little")
    last_counter = int(args["last_counter"])
    counter_ok = counter > last_counter or (
        last_counter >= 0xFFFD and counter <= 8
    )
    if not counter_ok:
        raise ValueError(
            "Replay counter {} is not newer than {}".format(counter, last_counter)
        )
    decoded = {
        "fcf": int.from_bytes(frame[0:2], "little"),
        "seq_num": frame[2],
        "pan_id": int.from_bytes(frame[3:5], "little"),
        "dst_short": int.from_bytes(frame[5:7], "little"),
        "src_short": int.from_bytes(frame[7:9], "little"),
        "group_id": frame[10],
        "counter": counter,
        "domain": domain,
        "gesture": command & 0xF,
        "hmac_valid": True,
        "anti_replay_valid": True,
    }
    if domain == SHIP_DOMAIN_MESH_CLOCK:
        decoded["utc_epoch"] = int.from_bytes(frame[14:18], "little")
    else:
        decoded.update({
            "param_1": frame[14],
            "param_2": frame[15],
            "param_3": frame[16],
            "hmac_trunc16": int.from_bytes(frame[17:19], "little"),
        })
    return {
        "valid": True,
        "plain_english": (
            "The 19-byte frame has valid SHiP magic, SipHash, and a fresh counter."
        ),
        "decoded": decoded,
    }


# ---------------------------------------------------------------------------
# ORPHANED-MESH HEARTBEAT    (v0.1.18 — Pillar 1, Python mirror of C core)
# ELI5: the same "is my neighbour still alive?" check that runs on the
# hardware, in Python so the MCP diagnostic tool and the options dashboard
# can reason about a live routing table without a radio. A hop only gets
# refreshed by a frame that PASSED decode_verify_19b_frame(), so a replayed
# or recorded frame can never fake a heartbeat.
# ---------------------------------------------------------------------------

# One heartbeat window. Mirrors SHIP_HEARTBEAT_INTERVAL_MS in ship_protocol.h.
HEARTBEAT_INTERVAL_MS = 60_000
HEARTBEAT_DEGRADED = 2     # 2 missed windows → degrade/warn
HEARTBEAT_ORPHANED = 3     # 3+ missed windows → orphan/severed

HEALTH_UNKNOWN = "unknown"
HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_ORPHANED = "orphaned"


def heartbeat_feed(hop: dict, counter: int, local_mono_ms: int) -> dict:
    """ELI5: mark a hop as having just heard a GENUINE frame.

    In production this is only called after ``decode_verify_19b_frame``
    accepted the frame, so the nonce/last-seen clock can never be faked by
    a replay. Returns the updated hop dict.
    """
    hop["last_counter"] = counter & 0xFFFF
    hop["last_seen_ms"] = int(local_mono_ms)
    hop["state"] = HEALTH_HEALTHY
    return hop


def heartbeat_check(hop: dict, now_ms: int) -> dict:
    """ELI5: recompute a hop health from how long since its last genuine
    frame; return ``{"state": ..., "missed_windows": N}``.

    A hop that has never been fed (``last_seen_ms`` is 0) is UNKNOWN — it
    needs a re-handshake to establish a baseline, so it is never healthy.
    """
    last_seen = int(hop.get("last_seen_ms", 0))
    if last_seen == 0:
        state = HEALTH_UNKNOWN
        missed = 0
    else:
        missed = (int(now_ms) - last_seen) // HEARTBEAT_INTERVAL_MS
        if missed < 0:
            missed = 0
        if missed <= HEARTBEAT_DEGRADED - 1:
            state = HEALTH_HEALTHY
        elif missed == HEARTBEAT_DEGRADED:
            state = HEALTH_DEGRADED
        else:
            state = HEALTH_ORPHANED
    hop["state"] = state
    return {"state": state, "missed_windows": missed}


def heartbeat_rehandshake_needed(hop: dict, now_ms: int) -> bool:
    """ELI5: True whenever a hop needs a "Re-handshake" — anything but
    HEALTHY (unknown / degraded / orphaned). The dashboard degradation flag.
    """
    return heartbeat_check(hop, now_ms)["state"] != HEALTH_HEALTHY



# ---------------------------------------------------------------------------
# MESH TELEMETRY    (v0.1.20 — the shared "is my mesh alive?" source of truth)
# ELI5: both the MCP diagnostic tool and the Home Assistant dashboard render
# from ONE function so a hop can never look "healthy" in one place and
# "orphaned" in the other. It mirrors the C core's per-hop heartbeat verdicts
# and the modeled multi-hop airtime (one 19-byte frame per hop at 250 kbps).
# ---------------------------------------------------------------------------

# Per-hop airtime of a 19-byte SHiP frame at 250 kbps (the C core's number).
PER_HOP_AIRTIME_MS = 0.864
# A small coordination slack between hops in a multi-hop chain.
COORDINATION_SLACK_MS = 0.5

# ELI5: the visual status badges the dashboard paints next to each hop.
HEALTHY_BADGE = "🟢"
DEGRADED_BADGE = "🟡"
ORPHANED_BADGE = "🔴"
UNKNOWN_BADGE = "⚪"

# Burst debounce — mirrors SHIP_BURST_MAX_TAPS / SHIP_BURST_WINDOW_MS in C.
BURST_MAX_TAPS = 5         # taps per window before the extras are swallowed
BURST_WINDOW_MS = 500      # the sliding window: 500 ms

_BADGE_BY_STATE = {
    HEALTH_HEALTHY: HEALTHY_BADGE,
    HEALTH_DEGRADED: DEGRADED_BADGE,
    HEALTH_ORPHANED: ORPHANED_BADGE,
    HEALTH_UNKNOWN: UNKNOWN_BADGE,
}
_PHRASE_BY_STATE = {
    HEALTH_HEALTHY: "Healthy",
    HEALTH_DEGRADED: "Degraded",
    HEALTH_ORPHANED: "Orphaned",
    HEALTH_UNKNOWN: "Unknown",
}


def health_badge(state: str) -> str:
    """ELI5: the emoji badge for a health verdict (green/yellow/red/white)."""
    return _BADGE_BY_STATE.get(state, UNKNOWN_BADGE)


def health_phrase(state: str) -> str:
    """ELI5: a "badge + word" phrase, e.g. a green/yellow/red badge, for a verdict."""
    return _BADGE_BY_STATE.get(state, UNKNOWN_BADGE) + " " + _PHRASE_BY_STATE.get(state, "Unknown")


def diagnose_mesh(hops: list, now_ms: int = 0) -> dict:
    """ELI5: the single "is my mesh still alive?" verdict, shared by the
    MCP tool and the dashboard.

    For every hop it re-runs the heartbeat verdict (healthy / degraded /
    orphaned / unknown), counts how many branches are degraded or orphaned,
    decides whether any branch needs a re-handshake, and models the
    multi-hop latency as one 19-byte frame's airtime per hop plus a small
    per-hop coordination slack. Pure: it only reads the hop snapshots.
    """
    hop_reports = []
    orphaned = 0
    degraded = 0
    needs_rehandshake = False
    for raw in (hops or []):
        hop = dict(raw)
        hop.setdefault("hop_index", 0)
        hop.setdefault("last_counter", 0)
        hop.setdefault("last_seen_ms", 0)
        check = heartbeat_check(hop, now_ms)
        state = check["state"]
        rehand = heartbeat_rehandshake_needed(hop, now_ms)
        if rehand:
            needs_rehandshake = True
        if state == HEALTH_ORPHANED:
            orphaned += 1
        elif state == HEALTH_DEGRADED:
            degraded += 1
        hop_reports.append({
            "hop_index": int(hop.get("hop_index", 0)),
            "last_counter": int(hop.get("last_counter", 0)),
            "missed_windows": check["missed_windows"],
            "state": state,
            "re_handshake_needed": rehand,
            "badge": health_badge(state),
            "phrase": health_phrase(state),
        })

    hop_count = max(len(hop_reports), 0)
    modeled_latency_ms = round(
        hop_count * PER_HOP_AIRTIME_MS
        + (hop_count - 1 if hop_count else 0) * COORDINATION_SLACK_MS,
        3,
    )

    if not hop_reports:
        verdict = "no hops supplied — nothing to diagnose"
    elif needs_rehandshake:
        verdict = "{} orphaned, {} degraded branch(es) need a re-handshake".format(
            orphaned, degraded)
    else:
        verdict = "all {} hop(s) healthy".format(hop_count)

    return {
        "eli5_summary": "Mesh diagnostic: {}".format(verdict),
        "verdict": verdict,
        "hop_count": hop_count,
        "orphaned_hops": orphaned,
        "degraded_hops": degraded,
        "needs_re_handshake": needs_rehandshake,
        "modeled_latency_ms": modeled_latency_ms,
        "per_hop_airtime_ms": PER_HOP_AIRTIME_MS,
        "now_ms": now_ms,
        "hops": hop_reports,
    }


# ---------------------------------------------------------------------------
# BURST DEBOUNCE    (v0.1.20 — physical edge-case hardening, C-core mirror)
# ELI5: a person can mash a button far faster than one gesture means. A
# sliding 500 ms window lets the first 5 taps through and SWALLOWS the 6th+
# so a clumsy press can't flood the mesh with a "command storm". A replay
# can never manufacture a fake burst because only genuine (already-decoded)
# taps are fed here — mirroring ship_burst_accept() in the C core.
# ---------------------------------------------------------------------------
def burst_reset(state: dict, now_ms: int) -> dict:
    """ELI5: start a fresh burst window, clearing any in-flight count."""
    state["window_start_ms"] = int(now_ms)
    state["taps_in_window"] = 0
    return state


def burst_accept(state: dict, now_ms: int) -> bool:
    """ELI5: True if this tap should produce a radio command, False if the
    debounce swallowed it as part of a burst.

    The first tap (or any tap after a 500 ms gap) opens a fresh window and
    is always accepted. Inside a live window, taps are counted; once the
    count passes BURST_MAX_TAPS the extras are the storm and are swallowed.
    A None state is a defensive no-op that swallows (returns False).
    """
    if not state:
        return False
    now = int(now_ms)
    if state.get("taps_in_window", 0) == 0 or (
        now - int(state.get("window_start_ms", 0))) > BURST_WINDOW_MS:
        state["window_start_ms"] = now
        state["taps_in_window"] = 1
        state["accepted"] = int(state.get("accepted", 0)) + 1
        return True
    state["taps_in_window"] = int(state.get("taps_in_window", 0)) + 1
    if state["taps_in_window"] > BURST_MAX_TAPS:
        state["dropped"] = int(state.get("dropped", 0)) + 1
        return False
    state["accepted"] = int(state.get("accepted", 0)) + 1
    return True


__all__ = [
    "decode_verify_19b_frame",
    "encode_19b_frame",
    "siphash24",
    "heartbeat_check",
    "heartbeat_feed",
    "heartbeat_rehandshake_needed",
    "HEALTH_HEALTHY",
    "HEALTH_DEGRADED",
    "HEALTH_ORPHANED",
    "HEALTH_UNKNOWN",
    "diagnose_mesh",
    "health_badge",
    "health_phrase",
    "burst_accept",
    "burst_reset",
    "HEALTHY_BADGE",
    "DEGRADED_BADGE",
    "ORPHANED_BADGE",
    "UNKNOWN_BADGE",
    "BURST_MAX_TAPS",
    "BURST_WINDOW_MS",
    "PER_HOP_AIRTIME_MS",
    "COORDINATION_SLACK_MS",
]
