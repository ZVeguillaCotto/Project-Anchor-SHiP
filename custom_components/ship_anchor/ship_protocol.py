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


__all__ = ["decode_verify_19b_frame", "encode_19b_frame", "siphash24"]
