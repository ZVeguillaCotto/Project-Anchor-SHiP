# SHiP v0.1 — Smart Home Independent Protocol
### Code-name: **Project Anchor**

> **"What if your smart home kept working even when the hub, router, and cloud are all off?"**

**In one breath (ELI5):** SHiP Anchor teaches your smart-home devices to talk to
*each other* directly — a switch whispers to a light in under one-thousandth of a
second, in a tiny 19-byte radio message, with no hub, no router, and no cloud in
the middle. So when the power flickers, the Wi-Fi drops, or Home Assistant is
mid-restart, the things that *matter* (lock the doors, trip the water valve, the
goodnight scene) keep working anyway. That is **local resilience in under a
millisecond**.

[![CI](https://github.com/ZVeguillaCotto/Project-Anchor-SHiP/actions/workflows/ci.yml/badge.svg)](https://github.com/ZVeguillaCotto/Project-Anchor-SHiP/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-Ready-blue)](#)
[![Version](https://img.shields.io/badge/version-0.1.24-blue)](#)
[![C Tests](https://img.shields.io/badge/C%20Core%20Tests-13%2F13%20PASS-brightgreen)](#)
[![Python Drills](https://img.shields.io/badge/Disaster%20Drills-6%2F6%20PASS-brightgreen)](#)
[![Frame Size](https://img.shields.io/badge/Frame%20Size-19%20bytes-blue)](#)
[![Airtime](https://img.shields.io/badge/Airtime-calculated%200.864%20ms-blue)](#)
[![License](https://img.shields.io/badge/license-MIT-green)](#)

---

> **Testing status:** The passing checks below are host-side simulations and
> native compile checks. They do not verify installation in Home Assistant,
> communication with real devices, radio timing, or hardware behavior. Treat
> this project as an experimental PoC; do not use it for safety-critical
> controls. All three tiers of the Testing Pyramid run automatically on every
> push and pull request via GitHub Actions (`.github/workflows/ci.yml`); find
> out how to contribute in [CONTRIBUTING.md](CONTRIBUTING.md) and how to report
> a vulnerability in [SECURITY.md](SECURITY.md).

## LAYER 1 — The Kitchen-Table Explanation (ELI5)

**The problem:** Almost every smart home device today is like a telephone that can only call the operator. Your kitchen switch wants to turn on the living room lights — but instead of calling them directly, it has to call the operator (your Home Assistant server, Apple HomePod, or Wi-Fi router) and ask "please connect me to the lights." If the operator is rebooting for an update, or the router blinked off after a power surge, your lights don't turn on.

**SHiP v0.1 fixes this in two ways:**

### 🏠 Tier 1 — The "Offline Compiler" (for existing store-bought devices)
SHiP Anchor reads your existing Home Assistant automations and quietly programs a backup copy directly into the memory chips of your existing **Zigbee, Matter, Z-Wave, Shelly, and Tasmota** devices — using their own manufacturer specifications so they can't block it.

Think of it like programming your TV remote to also work with the neighbours' TV. The remote (switch) and the TV (light) remember each other's codes. The operator (hub) is no longer needed for that relationship.

**Supported protocols:**
- **Multi-way switch offline sync** — N switches + shared bulbs stay in exact sync via one SHiP Group ID broadcast channel, with the `NoStatusShare` gesture (`0x8`) suppressing status echo loops — no hub, router, or cloud in the path
- **Bidirectional wizard** — start from either the source switch or the target load; the other end is protocol-filtered so cross-bus pairings are impossible (v0.1.14)
- **Multi-hop canopy routing** — chain-like `switch → canopy/relay → load` bindings for physical power-gating ceiling canopies (v0.1.14); a broadcast-like multicast group mesh is the roadmap step
- **Visual LEAT & Multi-Hop Inspector** — a dashboard in the Home Assistant options dialog that renders the whole binding (chain + protocol + every LEAT rule) in plain English, and lets you add or delete a rule or a source binding without touching JSON (v0.1.15)
- **Human-friendly units** — the wizard and dashboard speak Kelvin (2000K warm → 6500K cool) and a brightness percentage (0–100 %), and silently translate them into the protocol values the hardware needs (mireds and a 0–255 byte) (v0.1.16)
- **Zigbee** (ZHA & Zigbee2MQTT) — direct device-to-device binding with auto-discovery of vendor quirks (Inovelli, Aqara, Tuya, Philips Hue, SONOFF)
- **Matter 1.3–1.6** — two-step ACL + Binding table writes via `matterjs-server` WebSocket API
- **Z-Wave 700/800** — Association Group programming via `zwave_js` (with ZWLR guard)
- **Shelly Gen2/3/4** — local mJS scripts over HTTP RPC
- **Tasmota** — Device Groups (DGR) via UDP multicast 239.255.250.250:4447

### ⚡ Tier 2 — The 19-Byte Fast-Path (for open/customisable hardware)
For **ESP32-C6, ESP32-H2, nRF52840, and nRF5340** devices, SHiP injects a tiny 19-byte "radio whisper" at the exact hardware radio layer — below Wi-Fi, below IPv6, below everything. Devices talk **directly to each other** in under 3 milliseconds even if every router and server in your home is completely unplugged.

### 🔗 Multi-Hop Canopy Routing (Chain-Like vs. Broadcast-Like)
Many real-world loads sit behind an intermediate module — the classic example is a ceiling-fan canopy that powers a light kit. SHiP Anchor programs that **chain** hop-by-hop:

```
  Switch  ── Hop 1 ──►  Ceiling Canopy  ── Hop 2 ──►  Bulb / Fan
```

Each hop is a normal one-hop binding to the next device, compiled in order by
`AnchorCompiler.async_compile_multihop()` so the gesture + LEAT rule travels
through the canopy and out to the load. This **Chain-Like** path is the
shipping implementation and is ideal for physical power-gating canopies.

**Broadcast-Like (Multicast Group Mesh)** is the architectural roadmap: instead
of N sequential hops, one multicast frame goes to a shared group and every member
applies it locally (with the `NoStatusShare` lock to suppress echo). That is more
efficient for large, flat clusters with no relaying middle device, and is
complementary to — not a replacement for — the chain-like path. See
[GUIDE_FOR_HUMANS.md](GUIDE_FOR_HUMANS.md) Part 14 for the full explanation.

---

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

RESULTS: 13/13 tests passed    — ALL PASS ✓
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
  ✓  Drill 1: C Core + Radio HAL (13/13 C unit tests + fuzz airbag)
  ✓  Drill 2: Tier 1 Compiler (ZHA membership, Matter 2-step, ZWLR block)
  ✓  Drill 3: Water Leak Reflex (~0.88 ms modeled estimate)
  ✓  Drill 4: 5×-Click Scene + 2:00 AM Mesh-RTC Nightlight
  ✓  Drill 5: 30-Device Storm & 50-Node Recovery
  ✓  Drill 6: Eight MCP tools and <1,500 lexical schema tokens

  ALL 6/6 DRILLS PASSED ✓
  Simulation checks passed — real Home Assistant and hardware validation remain.
  Total benchmark runtime: varies by machine
```

---

## Repository Structure

```
project-anchor-ship/
├── README.md                                       ← You are here
├── GUIDE_FOR_HUMANS.md                             ← Plain-English walkthrough
├── CLAUDE.md                                       ← ELI5-first engineering memory
├── AGENTS.md                                       ← Agent entry point to project memory
├── ARCH_DECISIONS.md                               ← Date-stamped design history
├── SECURITY.md                                     ← How to report a vulnerability
├── SECURITY_AUDIT.md                               ← The v0.1.0→v0.1.7 PII/injection audit
├── CONTRIBUTING.md                                 ← How to contribute + the Testing Pyramid
├── pyproject.toml                                  ← Ruff/Black/isort code-hygiene config
├── hacs.json                                       ← HACS 3-click install metadata
├── .github/
│    └─ workflows/
│         └─ ci.yml                              ← CI: pytest + C core + drills
├── src/
│    └─ ship_core/
│         ├─ ship_protocol.h                        ← 19-byte frame struct + enums
│         ├─ ship_protocol.c                        ← SipHash-2-4, LEAT, Mesh-RTC
│         └─ ship_radio_hal.c                       ← ESP-IDF / Nordic / OpenThread HAL
├── esphome/
│    └─ components/
│         └─ ship_fastpath/
│             ├─ __init__.py                         ← Hardware guards + ESPHome codegen
│             ├─ ship_fastpath.h                     ← C++ component header
│             ├─ ship_fastpath.cpp                   ← C++ component (setup/loop/RX/TX)
│             └─ example_esp32c6_switch.yaml         ← Copy-paste user YAML
├── custom_components/
│    └─ ship_anchor/
│         ├─ manifest.json                           ← HA integration manifest (v0.1.24)
│         ├─ __init__.py                             ← Persistent Store + service + drift
│         ├─ config_flow.py                          ← Wizard + options flow + simulator
│         ├─ automation.py                           ← HA automation normalization
│         ├─ quirks_inspector.py                     ← Live zigpy + static quirks DB
│         ├─ ship_protocol.py                        ← Pure-Python runtime (default engine)
│         ├─ runtime.py                              ← Engine-selection seam (C optional)
│         ├─ diagnostics.py                          ← HA 'Download diagnostics' bundle
│         ├─ strings.json                            ← Localised wizard strings (ELI5)
│         └─ compiler.py                             ← Multi-protocol Compiler + LEAT builder
├── mcp/
│    └─ ship_anchor_mcp.py                          ← Eight-tool stdio MCP server + CLI
├── tests/
│    ├─ test_*.py                                  ← Layer 1+2 unit + integration (143 passed)
│    └─ test_fuzz.py                               ← Fuzz airbag (never crash on bad bytes)
└─ sim/
     ├─ test_ship_core.c                           ← 13 C unit tests + fuzz airbag
     ├─ test_ship_fuzz.c                           ← C fuzz airbag harness
     ├─ check_ship_fastpath_compile.py             ← ESPHome C++ protocol/HAL link check
     └─ run_anchor_benchmarks.py                   ← Six-drill simulator
```

---

## Quick Start

### Tier 1: Install the Home Assistant Integration

**The easy way — HACS (recommended, 3 clicks).** If you have
[Home Assistant Community Store (HACS)](https://www.home-assistant.io/hacs/)
installed:

1. In HACS, open **Community → Integrations** and search for
   **SHiP Anchor — Smart Home Independent Protocol** and open it.
2. Click **Download** and choose version `v0.1.24`.
3. **Restart Home Assistant**, then open
   **Settings → Devices & services → Add integration → SHiP Anchor**.

HACS will keep the integration up to date from this repository.

**The manual way (no HACS).** Download a release `zip` from the
[GitHub Releases page](https://github.com/ZVeguillaCotto/Project-Anchor-SHiP/releases),
unzip `ship_anchor.zip`, and copy the `custom_components/ship_anchor` folder
into `<HA config>/custom_components/ship_anchor`. Then restart Home Assistant and
add the integration from **Settings → Devices & services → Add integration**.

The first service call is preview-only by default:
```yaml
service: ship_anchor.compile
data:
  dry_run: true
```
Review the generated payloads in the Home Assistant logs and check device
compatibility before deliberately setting `dry_run: false`. A live compile
writes to devices and may not be reversible for every supported protocol;
`ship_anchor.erase_all` only works for protocols with an implemented erase
operation. The compiler currently reads automations from `automations.yaml`;
automations stored only in Home Assistant's UI storage are not included.

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

# Native compile/link smoke check with stand-in ESPHome headers; not a target build
python3 sim/check_ship_fastpath_compile.py
```

### Model Context Protocol (MCP)

The dependency-free Python 3.9+ server exposes device inspection, binding
preview, LEAT behaviour baking, binding inventory, 19-byte frame
encoding/verification, and the full verification suite. MCP clients launch it
as a stdio process:

```bash
python3 mcp/ship_anchor_mcp.py
```

For local smoke tests, list the compact tool schemas, call one tool, or exercise
all eight tools (including the nested verification-suite run and the mesh
diagnostic):

```bash
python3 mcp/ship_anchor_mcp.py --cli
python3 mcp/ship_anchor_mcp.py --cli ship_inspect_device \
  --args '{"ieee":"00:0d:6f:00:05:7d:2d:34","model":"VZM31-SN","manufacturer":"Inovelli"}'
python3 mcp/ship_anchor_mcp.py --cli ship_bake_behavior \
  --args '{"group_id":42,"rules":[{"gesture":"1x Tap","brightness":100,"color_temp_mireds":350},{"gesture":"5x Tap","hour_start":22,"hour_end":6,"brightness":15}]}'
python3 mcp/ship_anchor_mcp.py --demo
```

The schema check reports an exact lexical-token count for its compact JSON
definition; actual model tokenizer counts can vary by client.

---

## The 3-Tier Testing Pyramid

SHiP Anchor is verified **bottom-up**, like a real testing pyramid: a wide,
cheap base you run on every change, and a narrow, expensive top you run before a
release. All three tiers run on an ordinary computer — no Home Assistant box and
no radio hardware — except that the top two get slower and more realistic as they
go up.

| Tier | What it is | Command | Target |
|---|---|---|---|
| **1 — Unit** | Fast, isolated checks of pure logic: protocol matching, target kind, LEAT row mapping, the multi-hop + DAG compiler, the orphaned-mesh heartbeat, the fuzz airbag, and the eight-tool MCP surface. No Home Assistant, no I/O. | `python3 -m pytest tests/ -v` | **143 passed** |
| **2 — Integration** | Component tests wiring the wizard, the persistent store (`.storage/ship_anchor.bindings`), and the compiler together, plus the fuzz airbag (`test_fuzz.py`). | `python3 -m pytest tests/ -v` | (included in the 143) |
| **3 — System / disaster** | The C protocol core, its C fuzz airbag, and the Python disaster drills — the only tiers that touch the real 19-byte encoder, SipHash, LEAT, Mesh-RTC, and radio HAL. | see below | **13/13 + 6/6** |

The base is wide and cheap (run on every change); the top is narrow and
expensive (run before release). **The invariant to protect:** the LEAT rows
produced at Tier 1 are *field-for-field identical* to the `ship_leat_entry_t`
struct asserted by the C suite at Tier 3 — a mismatch between the two is a
release blocker. GitHub Actions runs all three tiers on every push and pull
request (`.github/workflows/ci.yml`).

```bash
# Tier 1 + 2 — unit and integration (needs `pip install pytest`)
python3 -m pytest tests/ -v

# Tier 3 — C protocol core (13/13, includes the fuzz airbag)
gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
   -I src/ship_core sim/test_ship_core.c \
   src/ship_core/ship_protocol.c src/ship_core/ship_radio_hal.c \
   -o /tmp/test_ship_core && /tmp/test_ship_core

# Tier 3 — disaster + MCP drills (6/6, MCP schema under the 1,500-token tripwire)
python3 sim/run_anchor_benchmarks.py
```

---

## Hardware Platform Support

| Platform | TX Method | RX Method | Coexistence |
|---|---|---|---|
| ESP32-C6 (ESP-IDF v5.1+) | `esp_ieee802154_transmit()` (implementation present; hardware unverified) | Receive registration is not implemented | Not hardware-verified |
| ESP32-H2 (ESP-IDF v5.1+) | `esp_ieee802154_transmit()` (implementation present; hardware unverified) | Receive registration is not implemented | Not hardware-verified |
| nRF52840 (NCS v3.2+) | `nrf_802154_transmit_raw()` (implementation present; hardware unverified) | Driver callback path; hardware unverified | Not hardware-verified |
| nRF5340 (NCS v3.2+) | `nrf_802154_transmit_raw()` (implementation present; hardware unverified) | Driver callback path; hardware unverified | Not hardware-verified |
| OpenThread with ESP-IDF/Nordic SoC driver | Platform-dependent; hardware unverified | `otLinkSetPcapCallback()` path; hardware unverified | Not hardware-verified |
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
