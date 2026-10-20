# SHiP v0.1 — Smart Home Independent Protocol
### Code-name: **Project Anchor**

> **"What if your smart home kept working even when the hub, router, and cloud are all off?"**

[![C Tests](https://img.shields.io/badge/C%20Core%20Tests-10%2F10%20PASS-brightgreen)](#)
[![Python Drills](https://img.shields.io/badge/Disaster%20Drills-6%2F6%20PASS-brightgreen)](#)
[![Frame Size](https://img.shields.io/badge/Frame%20Size-19%20bytes-blue)](#)
[![Airtime](https://img.shields.io/badge/Airtime-0.864%20ms-blue)](#)
[![E2E Latency](https://img.shields.io/badge/End--to--End-<3%20ms-blue)](#)
[![HACS](https://img.shields.io/badge/HACS-3--click%20install-orange)](#)

---

## LAYER 1 — The Kitchen-Table Explanation (ELI5)

**The problem:** Almost every smart home device today is like a telephone that can only call the operator. Your kitchen switch wants to turn on the living room lights — but instead of calling them directly, it has to call the operator (your Home Assistant server, Apple HomePod, or Wi-Fi router) and ask "please connect me to the lights." If the operator is rebooting for an update, or the router blinked off after a power surge, your lights don't turn on.

**SHiP v0.1 fixes this in two ways:**

### 🏠 Tier 1 — The "Offline Compiler" (for existing store-bought devices)
SHiP Anchor reads your existing Home Assistant automations and quietly programs a backup copy directly into the memory chips of your existing **Zigbee, Matter, Z-Wave, Shelly, and Tasmota** devices — using their own manufacturer specifications so they can't block it.

Think of it like programming your TV remote to also work with the neighbours' TV. The remote (switch) and the TV (light) remember each other's codes. The operator (hub) is no longer needed for that relationship.

**Supported protocols:**
- **Zigbee** (ZHA & Zigbee2MQTT) — direct device-to-device binding with auto-discovery of vendor quirks (Inovelli, Aqara, Tuya, Philips Hue, SONOFF)
- **Matter 1.3–1.6** — two-step ACL + Binding table writes via `matterjs-server` WebSocket API
- **Z-Wave 700/800** — Association Group programming via `zwave_js` (with ZWLR guard)
- **Shelly Gen2/3/4** — local mJS scripts over HTTP RPC
- **Tasmota** — Device Groups (DGR) via UDP multicast 239.255.250.250:4447

### ⚡ Tier 2 — The 19-Byte Fast-Path (for open/customisable hardware)
For **ESP32-C6, ESP32-H2, nRF52840, and nRF5340** devices, SHiP injects a tiny 19-byte "radio whisper" at the exact hardware radio layer — below Wi-Fi, below IPv6, below everything. Devices talk **directly to each other** in under 3 milliseconds even if every router and server in your home is completely unplugged.

---

## LAYER 2 — The Engineering Ground Truth

### The 19-Byte SHiP Micro-Packet Wire Layout

```
Total Software Frame: 19 Bytes (packed, little-endian)
Total PSDU with hardware FCS: 21 Bytes
On-air time @ 250 kbps (802.15.4 O-QPSK): 0.864 ms  (< 1 ms!)

Byte  0..1   fcf            Frame Control: 0x8841 (broadcast) / 0x8861 (unicast)
Byte  2      seq_num        802.15.4 MAC sequence number
Bytes 3..4   pan_id         Active Thread/Zigbee PAN ID
Bytes 5..6   dst_short      0xFFFF (group broadcast) or target 16-bit address
Bytes 7..8   src_short      Sender's 16-bit short address
Byte  9      ship_magic     0xA5 — SHiP v0.1 fast-path discriminator
Byte  10     group_id       SHiP Device Group ID (0–255)
Bytes 11..12 replay_counter Monotonic 16-bit anti-replay counter
Byte  13     cmd_click      Bits[7:4]=Domain, Bits[3:0]=Gesture
Byte  14     param_1        Primary: relay mask / brightness / scene ID / valve %
Byte  15     param_2        Secondary: color temp (Mireds) / fan speed / shade tilt
Byte  16     param_3        Tertiary: fade time / share mask / safety override
Bytes 17..18 hmac_trunc16   Truncated 16-bit SipHash-2-4 tag (normal domains)

Mesh-RTC domain 0xF uses bytes 14..17 for the full 32-bit UTC epoch and
byte 18 for the low 8 bits of SipHash over bytes 9..17.
```

### Verification and Scene-Storm Benchmarks

```
=================================================================
  SHiP v0.1 (Project Anchor) — C Core Unit Test Suite
  Testing: ship_protocol.c (SipHash-2-4, encode/decode, LEAT, RTC)
=================================================================

[TEST 1] test_frame_size_assertion
  sizeof(ship_frame_19b_t) = 19 bytes  ✓            [PASS]

[TEST 2] test_encode_decode_roundtrip
  Encode→Decode round-trip: all 19 fields verified  ✓
  cmd_click=0x12  (domain=0x1  gesture=0x2)
  HMAC-trunc16=0xED24  ✓                             [PASS]

[TEST 3] test_siphash_crypto
  SipHash(data1, TEST_KEY)  = 0xB906
  SipHash(data1, TEST_KEY)  = 0xB906 (repeat — must match)
  SipHash(data2, TEST_KEY)  = 0xEBFE (1-bit diff — must differ)
  SipHash(data1, WRONG_KEY) = 0xFF38 (wrong key — must differ)
  SipHash-2-4 cryptographic properties verified  ✓  [PASS]

[TEST 4] test_anti_replay
  Anti-replay: replay blocked, older blocked, next accepted  ✓  [PASS]

[TEST 5] test_counter_rollover
  Counter rollover resync: 0xFFFF→0 handled correctly  ✓  [PASS]

[TEST 6] test_mesh_rtc_encode_decode_roundtrip
  Mesh-RTC encode→decode→update: epoch=1700000000, byte 17 and tag verified ✓

[TEST 7] test_mesh_rtc_sync
  Mesh-RTC sync, drift guard, and ship_rtc_now() verified  ✓  [PASS]

[TEST 8] test_leat_matching
  LEAT matching: all rules + time-of-day window verified  ✓  [PASS]

[TEST 9] test_nostatus_share_flag
  Tasmota NoStatusShare flag (gesture=0x8) verified  ✓  [PASS]

[TEST 10] test_bad_magic_rejection
  Bad magic and HMAC tamper rejection verified  ✓  [PASS]

RESULTS: 10/10 tests passed  — ALL PASS ✓
=================================================================
```

### Full Disaster Simulation Results

```
╔══════════════════════════════════════════════════════════════════════╗
║     SHiP v0.1 (Project Anchor) — Full-Home Disaster Simulator       ║
║     & Tier 1 Offline Compiler Verification Suite                    ║
╚══════════════════════════════════════════════════════════════════════╝

DRILL 3 — Emergency Water Leak Reflex (Hub & Wi-Fi Dead)
──────────────────────────────────────────────────────────
  Frame size: 19 bytes (target: 19)  ✓
  frame[9] = 0xA5 (SHiP magic byte)  ✓
  Encode time (avg over 10,000 samples): 13,662 ns = 0.0137 ms
  802.15.4 airtime @ 250 kbps: 27B × 32µs = 864 µs = 0.864 ms
  End-to-end latency estimate: 0.878 ms  (target: < 3 ms)  ✓
  Worst-case with 1 CSMA-CA backoff: 1.184 ms  < 3 ms  ✓

DRILL 5 — 30-Light Goodnight Scene
──────────────────────────────────
  Mode                                      Frames   Raw airtime   Collision
  Matter-over-Thread, 30× unicast               30       99.8 ms     100.0%
  SHiP v0.1, 30× unicast sweep                  30       25.9 ms     100.0%
  SHiP v0.1, 1× group broadcast (0xFFFF)         1      0.864 ms       0.0%

  PSDU size reduction: 1 - (21 / 98) = 78.6% smaller
  Total PHY airtime reduction: 1 - (27 / 104) = 74.0% less
  50-node recovery: simultaneous collisions → 0 unresolved after jittered retry

FINAL REPORT:
  ✓  Drill 1: C Core + Radio HAL (10/10 C unit tests)
  ✓  Drill 2: Tier 1 Compiler (ZHA membership, Matter 2-step, ZWLR block)
  ✓  Drill 3: Water Leak Reflex (0.878 ms end-to-end)
  ✓  Drill 4: 5×-Click Scene + 2:00 AM Mesh-RTC Nightlight
  ✓  Drill 5: 30-Device Storm & 50-Node Recovery
  ✓  Drill 6: Five MCP tools and <1,000 lexical schema tokens

  ALL 6/6 DRILLS PASSED ✓
  SHiP v0.1 (Project Anchor) — PoC verified and ready.
  Total benchmark runtime: 1.09 seconds
```

---

## Repository Structure

```
project-anchor-ship/
├── README.md                                     ← You are here
├── GUIDE_FOR_HUMANS.md                           ← Plain-English walkthrough
├── CLAUDE.md                                     ← ELI5-first engineering memory
├── AGENTS.md                                     ← Agent entry point to project memory
├── hacs.json                                     ← HACS 3-click install metadata
├── src/
│   └── ship_core/
│       ├── ship_protocol.h                       ← 19-byte frame struct + enums
│       ├── ship_protocol.c                       ← SipHash-2-4, LEAT, Mesh-RTC
│       └── ship_radio_hal.c                      ← ESP-IDF / Nordic / OpenThread HAL
├── esphome/
│   └── components/
│       └── ship_fastpath/
│           ├── __init__.py                       ← Hardware guards + ESPHome codegen
│           ├── ship_fastpath.h                   ← C++ component header
│           ├── ship_fastpath.cpp                 ← C++ component (setup/loop/RX/TX)
│           └── example_esp32c6_switch.yaml       ← Copy-paste user YAML
├── custom_components/
│   └── ship_anchor/
│       ├── manifest.json                         ← HA integration manifest
│       ├── __init__.py                           ← Persistent Store + service registration
│       ├── quirks_inspector.py                   ← Live zigpy + static quirks DB
│       └── compiler.py                           ← Multi-protocol Offline Compiler
├── mcp/
│   └── ship_anchor_mcp.py                        ← Five-tool stdio MCP server + CLI
└── sim/
    ├── test_ship_core.c                          ← 10 C unit tests (gcc/clang)
    ├── check_ship_fastpath_compile.py            ← ESPHome C++ protocol/HAL link check
    └── run_anchor_benchmarks.py                  ← Six-drill simulator
```

---

## Quick Start

### Tier 1: Install the Home Assistant Integration (3 clicks)

1. Open HACS → Custom Repositories → Add `https://github.com/project-anchor/ship`
2. Search "SHiP Anchor" → Install
3. Restart Home Assistant → go to **Settings → Integrations → Add → SHiP Anchor**

Then call the service:
```yaml
service: ship_anchor.compile
data:
  dry_run: false   # Set to true first to preview all payloads without applying
```

### Tier 2: Flash SHiP Fast-Path to an ESP32-C6

1. Copy `esphome/components/ship_fastpath/example_esp32c6_switch.yaml` to your ESPHome folder
2. Change the `group_key` to a random 32-character hex string
3. Run: `esphome run example_esp32c6_switch.yaml`

### Run the Tests Yourself

```bash
# Compile & run C unit tests
gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
    -I src/ship_core \
    sim/test_ship_core.c \
    src/ship_core/ship_protocol.c \
    src/ship_core/ship_radio_hal.c \
    -o /tmp/test_ship_core && /tmp/test_ship_core

# Run all six Python disaster and MCP drills
python3 sim/run_anchor_benchmarks.py

# Compile/link the ESPHome C++ component against the included protocol and HAL
python3 sim/check_ship_fastpath_compile.py
```

### Model Context Protocol (MCP)

The dependency-free Python 3.9+ server exposes device inspection, binding
preview, 19-byte frame encoding/verification, and the full verification suite.
MCP clients launch it as a stdio process:

```bash
python3 mcp/ship_anchor_mcp.py
```

For local smoke tests, list the compact tool schemas, call one tool, or exercise
all five tools (including the nested verification-suite run):

```bash
python3 mcp/ship_anchor_mcp.py --cli
python3 mcp/ship_anchor_mcp.py --cli ship_inspect_device \
  --args '{"ieee":"00:0d:6f:00:05:7d:2d:34","model":"VZM31-SN","manufacturer":"Inovelli"}'
python3 mcp/ship_anchor_mcp.py --demo
```

The schema check reports an exact lexical-token count for its compact JSON
definition; actual model tokenizer counts can vary by client.

---

## Hardware Platform Support

| Platform | TX Method | RX Method | Coexistence |
|---|---|---|---|
| ESP32-C6 (ESP-IDF v5.1+) | `esp_ieee802154_transmit()` | `esp_ieee802154_receive_done` callback | ⚠️ Shares 2.4 GHz with Wi-Fi |
| ESP32-H2 (ESP-IDF v5.1+) | `esp_ieee802154_transmit()` | `esp_ieee802154_receive_done` callback | ✅ Zigbee-only (no Wi-Fi) |
| nRF52840 (NCS v3.2+) | `nrf_802154_transmit_raw()` | `nrf_802154_received_raw()` callback | ✅ MPSL managed |
| nRF5340 (NCS v3.2+) | `nrf_802154_transmit_raw()` | `nrf_802154_received_raw()` callback | ✅ MPSL managed |
| OpenThread with ESP-IDF/Nordic SoC driver | `esp_ieee802154_transmit()` / `nrf_802154_transmit_raw()` | `otLinkSetPcapCallback()` pcap hook | ✅ Non-intrusive wiretap |
| OpenThread without direct SoC driver | Unsupported for Tier 2 TX | `otLinkSetPcapCallback()` pcap hook | RX inspection only |

---

## Security Model

- **SipHash-2-4** truncated to 16 bits — fast, 128-bit keyed, forgery-resistant
- **16-bit monotonic replay counter** — blocks recorded-and-replayed attacks
- **Counter rollover resync** — accepts counters 0–8 after last=0xFFFF (rollover window)
- **Group key** — 128-bit, pre-shared per device group, set in ESPHome YAML
- **Tasmota NoStatusShare** (gesture=0x8) — scene override lock prevents stale state echoes

---

## License

MIT License — Copyright (c) 2026 Project Anchor Contributors.

*SHiP v0.1 is a proof-of-concept. Production deployment requires additional security review,
OTA key management, and compliance verification for your jurisdiction.*
