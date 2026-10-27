"""
============================================================================
SHiP Anchor - tests/test_fuzz.py    (LAYER 1: UNIT TESTS, v0.1.23 - Pillar 1)
============================================================================

LAYER 1 - THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
This is the "crash test" for SHiP Anchor's radio language. A real mesh
sits out in the open where the air is full of garbage: half-arrived
packets, a neighbour replaying an old recording, a corrupted secret stamp,
a device that sends nonsense. If any of that could make our code *crash*
(a traceback, a memory overrun, an unhandled error) it could take the whole
offline mesh down - the exact disaster SHiP Anchor exists to prevent.

So this test throws *thousands* of deliberately broken packets at the codec
and asserts one simple thing every time: the code never crashes. A bad
packet is either (a) cleanly rejected with a typed ValueError - "drop it
silently" - or (b) decoded. Anything else (a stray exception, a crash) is
a test failure.

ELI5: it is the airbag test. We jab the steering wheel in every direction
to make sure the airbag pops instead of the car flipping over.

LAYER 2 - THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
Four families of malformed input, fuzzed with a fixed seed so the run is
reproducible:
   1. Truncated / over-long frames     -> must raise ValueError, not crash.
   2. Invalid nonces / stale counters  -> replay rejected (ValueError).
   3. Corrupted SipHash tags          -> bad-HMAC rejected (ValueError).
   4. Garbage headers (bad magic)     -> bad-magic rejected (ValueError).
Plus randomized byte-mutation of otherwise-valid frames, and fuzzing of the
new v0.1.23 surface (nonce windows, mesh diagnostic, LEAT lookup).

The pure-Python ``ship_protocol`` mirror is byte-compatible with the C core
``ship_protocol.c``; the C core's malformed-input robustness is asserted by
the companion fuzz harness ``sim/test_ship_fuzz.c``.
"""

from __future__ import annotations

import random

import pytest

from custom_components.ship_anchor.ship_protocol import (
    SHIP_FRAME_SIZE,
    SHIP_DOMAIN_MESH_CLOCK,
    diagnose_mesh,
    decode_verify_19b_frame,
    encode_19b_frame,
    leat_match,
    nonce_window_accept,
    nonce_window_state,
    nonce_windows_from_storage,
    nonce_windows_to_storage,
    siphash24,
)

# ELI5: a 16-byte key is 32 hex characters. This is a well-known *test* key,
# NOT a real secret - it must never protect a live device.
TEST_KEY = "deadbeefcafefabe0102030405060708"
WRONG_KEY = "00000000000000000000000000000000"

# A fixed seed makes the thousands of random mutations reproducible.
SEED = 0xA5A5
FROBS = 4096    # number of random mutations to throw at each target


def _encode(counter: int, domain: int = 0x1, gesture: int = 0x2,
            group_id: int = 1, key_hex: str = TEST_KEY) -> str:
    """ELI5: build a perfectly good frame hex so we can then break it."""
    args = {
        "key_hex": key_hex,
        "pan_id": 0x1234,
        "src_short": 0xABCD,
        "dst_short": 0xFFFF,
        "seq_num": 7,
        "group_id": group_id,
        "domain": domain,
        "gesture": gesture,
        "param_1": 0xBB,
        "param_2": 0xCC,
        "param_3": 0xDD,
        "counter": counter,
    }
    if domain == SHIP_DOMAIN_MESH_CLOCK:
        args["utc_epoch"] = 1700000000
    return encode_19b_frame(args)["frame_hex"]


def _decode(frame_hex: str, last_counter: int,
            key_hex: str = TEST_KEY) -> dict:
    """ELI5: hand a frame to the verifier and see what it decides."""
    return decode_verify_19b_frame({
        "frame_hex": frame_hex,
        "key_hex": key_hex,
        "last_counter": last_counter,
    })


def _mutate_frame(frame_hex: str, rng: random.Random) -> str:
    """ELI5: randomly flip some bytes in a good frame to make a broken one."""
    raw = bytearray(bytes.fromhex(frame_hex))
    for _ in range(rng.randint(0, 3)):
        index = rng.randrange(len(raw))
        raw[index] = (raw[index] ^ rng.randrange(1, 257)) & 0xFF
    return raw.hex()


def test_truncated_and_oversized_frames_never_crash() -> None:
    """ELI5: a half-arrived or over-long packet is cleanly refused, never a crash."""
    good = _encode(counter=10)
    good_bytes = bytes.fromhex(good)
    rng = random.Random(SEED)
    # ELI5: the C decoder (ship_frame_decode) refuses only frames SHORTER than
    # 19 bytes (SHIP_ERR_BAD_LENGTH); a frame of 19+ bytes decodes its first
    # 19 bytes and ignores any trailing padding. So truncated (<19) MUST be
    # refused; over-long (>=19) MUST NOT crash. Both are clean outcomes.
    refused = 0
    for length in range(0, SHIP_FRAME_SIZE + 6):
        frame = good_bytes[:length]
        try:
            result = _decode(frame.hex(), last_counter=9)
            assert isinstance(result, dict)
            assert length >= SHIP_FRAME_SIZE, "a short frame must be refused"
        except ValueError:
            if length < SHIP_FRAME_SIZE:
                refused += 1
        except Exception as exc:    # noqa: BLE001
            pytest.fail("Truncated frame raised unhandled {}: {!r}".format(
                type(exc).__name__, exc))
    assert refused >= 1, "expected short frames to be cleanly refused"

def test_stale_and_replayed_nonces_never_crash() -> None:
    """ELI5: replayed / stale counters are refused by the guard, never a crash."""
    rng = random.Random(SEED + 1)
    clean_rejections = 0
    for _ in range(FROBS):
        counter = rng.randrange(0, 0x10000)
        frame = _encode(counter=counter)
        last_counter = min(0xFFFF, counter + rng.choice([0, 1, 5, 20]))
        try:
            result = _decode(frame, last_counter=last_counter)
            assert result["valid"] is True
        except ValueError:
            clean_rejections += 1
        except Exception as exc:  # noqa: BLE001
            pytest.fail("Replay fuzz raised unhandled {}: {!r}".format(
                type(exc).__name__, exc))
    assert clean_rejections > 0, "expected some clean replay refusals"


def test_corrupted_siphash_tags_never_crash() -> None:
    """ELI5: a flipped secret stamp drops the frame, never a crash."""
    rng = random.Random(SEED + 2)
    clean_rejections = 0
    for _ in range(FROBS // 4):
        frame = bytearray(bytes.fromhex(_encode(counter=rng.randrange(1, 100))))
        if rng.random() < 0.5:
            frame[17] ^= 0x01
            frame[18] ^= 0x01
        else:
            frame[18] ^= 0x01
        try:
            result = _decode(frame.hex(), last_counter=0)
            assert result["valid"] is True
        except ValueError:
            clean_rejections += 1
        except Exception as exc:  # noqa: BLE001
            pytest.fail("Corrupt-HMAC fuzz raised unhandled {}: {!r}".format(
                type(exc).__name__, exc))
    assert clean_rejections > 0, "expected some clean HMAC refusals"


def test_garbage_headers_never_crash() -> None:
    """ELI5: a mangled 0xA5 magic byte is silently dropped, never a crash."""
    rng = random.Random(SEED + 3)
    clean_rejections = 0
    for _ in range(FROBS // 2):
        frame = bytearray(bytes.fromhex(_encode(counter=rng.randrange(1, 100))))
        frame[9] = rng.randrange(0, 0x100)
        if frame[9] == 0xA5:
            frame[9] = 0x00
        try:
            result = _decode(frame.hex(), last_counter=0)
            assert result["valid"] is True
        except ValueError:
            clean_rejections += 1
        except Exception as exc:  # noqa: BLE001
            pytest.fail("Garbage-header fuzz raised unhandled {}: {!r}".format(
                type(exc).__name__, exc))
    assert clean_rejections > 0, "expected some clean magic refusals"


def test_random_byte_mutation_never_crashes() -> None:
    """ELI5: thousands of bit-flips in real frames - clean refuse or decode, never crash."""
    rng = random.Random(SEED + 4)
    outcomes = {"valueerror": 0, "valid": 0, "other": 0}
    for i in range(FROBS):
        counter = rng.randrange(0, 0x10000)
        domain = rng.choice([0x1, 0x2, 0x4, 0x5, SHIP_DOMAIN_MESH_CLOCK])
        frame = _encode(counter=counter, domain=domain)
        broken = _mutate_frame(frame, rng)
        try:
            result = _decode(broken, last_counter=max(0, counter - 1))
            assert isinstance(result, dict)
            outcomes["valid"] += 1
        except ValueError:
            outcomes["valueerror"] += 1
        except Exception as exc:  # noqa: BLE001
            outcomes["other"] += 1
            pytest.fail("Byte-mutation #{} raised unhandled {}: {!r}".format(
                i, type(exc).__name__, exc))
    assert outcomes["other"] == 0, "an unhandled exception slipped through"
    assert outcomes["valueerror"] > 0, "expected some mutations to be refused"
    assert outcomes["valid"] > 0, "expected some mutations to still decode"


def test_random_hex_garbage_never_crashes() -> None:
    """ELI5: random hex strings of every length are refused cleanly, never a crash."""
    rng = random.Random(SEED + 5)
    clean = 0
    for _ in range(FROBS // 4):
        length = rng.randrange(0, 48)
        hex_str = "".join(rng.choice("0123456789abcdef") for _ in range(length))
        try:
            result = _decode(hex_str, last_counter=rng.randrange(0, 100))
            assert isinstance(result, dict)
        except ValueError:
            clean += 1
        except Exception as exc:  # noqa: BLE001
            pytest.fail("Hex-garbage fuzz raised unhandled {}: {!r}".format(
                type(exc).__name__, exc))
    assert clean > 0, "expected some clean refusals of hex garbage"


def test_leat_lookup_never_crashes_on_garbage() -> None:
    """ELI5: a junk cheat sheet and nonsense frame return an index or -1, never crash."""
    rng = random.Random(SEED + 6)
    for _ in range(FROBS // 4):
        table = [
            {"group_id": rng.randrange(0, 0x100),
             "domain": rng.randrange(0, 0x100),
             "gesture": rng.randrange(0, 0x100),
             "enabled": bool(rng.random())}
            for _ in range(rng.randrange(0, 6))
        ]
        frame = {
            "group_id": rng.randrange(0, 0x100),
            "domain": rng.randrange(0, 0x100),
            "gesture": rng.randrange(0, 0x100),
        }
        for hour in (None, rng.randrange(0, 24)):
            index = leat_match(table, frame, hour)
            assert index == -1 or 0 <= index < len(table)


def test_diagnose_mesh_never_crashes_on_garbage() -> None:
    """ELI5: junk hop snapshots still yield a verdict, never crash."""
    rng = random.Random(SEED + 7)
    for _ in range(FROBS // 8):
        hops = [
            {"hop_index": rng.randrange(0, 10),
             "last_counter": rng.randrange(0, 0x10000),
             "last_seen_ms": rng.choice([0, rng.randrange(0, 1_000_000)])}
            for _ in range(rng.randrange(0, 5))
        ]
        result = diagnose_mesh(hops, now_ms=rng.randrange(0, 1_000_000))
        assert isinstance(result, dict)
        assert "verdict" in result
        assert result["hop_count"] == len(hops)


def test_nonce_window_fuzz_never_crashes() -> None:
    """ELI5: a flood of random counters through the anti-replay window stays clean."""
    rng = random.Random(SEED + 8)
    window = nonce_window_state(rng.randrange(0, 0x10000))
    accepted = 0
    for _ in range(FROBS):
        counter = rng.randrange(0, 0x10000)
        window = nonce_window_accept(window, counter,
                                     now_ms=rng.randrange(0, 1_000_000))
        assert isinstance(window, dict)
        assert isinstance(window.get("accepted"), bool)
        if window["accepted"]:
            accepted += 1
    assert accepted > 0, "expected some fresh counters to be accepted"


def test_nonce_storage_round_trip_never_crashes() -> None:
    """ELI5: windows survive a store round-trip; a junk blob degrades to empty."""
    rng = random.Random(SEED + 9)
    store: dict = {}
    windows: dict = {}
    for gid in range(rng.randrange(0, 8)):
        windows[gid] = nonce_window_state(
            gid, rng.randrange(0, 0x10000),
            rng.randrange(0, 0x100000000),
            [rng.randrange(0, 0x10000) for _ in range(rng.randrange(0, 5))])
    nonce_windows_to_storage(store, windows)
    reloaded = nonce_windows_from_storage(store)
    for gid, window in windows.items():
        assert reloaded[gid]["last_counter"] == window["last_counter"]
    assert nonce_windows_from_storage({}) == {}
    assert nonce_windows_from_storage({"anti_replay_windows": "not-a-dict"}) == {}
    assert nonce_windows_from_storage(
        {"anti_replay_windows": {"3": "junk"}}) == {}


def test_siphash_is_stable_under_fuzz() -> None:
    """ELI5: the secret stamp is deterministic and key-dependent under fuzz."""
    rng = random.Random(SEED + 10)
    for _ in range(FROBS // 4):
        data = bytes(rng.randrange(0, 0x100) for _ in range(rng.randrange(0, 32)))
        key = bytes.fromhex(TEST_KEY)
        assert siphash24(data, key) == siphash24(data, key)
        if len(data) > 0:
            other = bytearray(data)
            other[0] ^= 0xFF
            assert siphash24(bytes(other), key) != siphash24(data, key)


def test_wrong_key_is_rejected_not_crash() -> None:
    """ELI5: a frame signed with one key fails verification under another, cleanly."""
    frame = _encode(counter=5, key_hex=TEST_KEY)
    with pytest.raises(ValueError):
        _decode(frame, last_counter=4, key_hex=WRONG_KEY)


def test_full_airbag_run_no_unhandled_exceptions() -> None:
    """ELI5: run every fuzz family back-to-back; not one unhandled exception escapes."""
    rng = random.Random(SEED)
    crashes = 0
    total = 0
    for _ in range(FROBS):
        total += 1
        counter = rng.randrange(0, 0x10000)
        domain = rng.choice([0x1, 0x2, 0x4, 0x5, SHIP_DOMAIN_MESH_CLOCK])
        frame = _encode(counter=counter, domain=domain)
        broken = _mutate_frame(frame, rng)
        last_counter = rng.randrange(0, 0x10000)
        key_hex = rng.choice([TEST_KEY, WRONG_KEY, "nothex"])
        try:
            _decode(broken, last_counter=last_counter, key_hex=key_hex)
        except ValueError:
            pass
        except Exception as exc:  # noqa: BLE001
            crashes += 1
            pytest.fail("Un-handled exception in full airbag run: {}: {!r}".format(
                type(exc).__name__, exc))
    assert crashes == 0
    assert total == FROBS
