# SHiP Anchor — Project Memory

## Layer 1 — The Kitchen-Table Explanation (ELI5)

Project Anchor helps a smart home keep important device-to-device actions
working when Home Assistant, the router, or the cloud is unavailable. It has
two complementary approaches:

1. **Tier 1 — Offline Compiler:** read Home Assistant automation definitions
   and preview or program existing Zigbee, Matter, Z-Wave, Shelly, and Tasmota
   devices using each protocol's native binding mechanism.
2. **Tier 2 — SHiP Fast-Path:** use one authenticated 19-byte 802.15.4 message
   between custom-capable devices, with local action rules and a mesh clock.

The non-negotiable project rule is **ELI5 First, Engineering Second**. Every
file added or changed must put a plain-language explanation before technical
details. Explain jargon before depending on it, and keep documentation honest
about what is simulated versus measured on real hardware.

## Layer 2 — Engineering Ground Truth

### Protocol and implementation invariants

- SHiP frames are 19 software bytes; the radio adds a 2-byte FCS, yielding a
  21-byte PSDU. At 250 kbps, the modeled PHY airtime is 0.864 ms.
- Byte 9 is the `0xA5` discriminator. Inject/listen on the active PAN channel;
  never hop channels per message on a single-radio device.
- Normal domains authenticate bytes 9–16 with a 16-bit SipHash-2-4 tag in
  bytes 17–18. Mesh-RTC domain `0xF` instead stores its 32-bit UTC epoch in
  bytes 14–17 and an 8-bit SipHash tag in byte 18, authenticating bytes 9–17.
  Use `ship_frame_encode_rtc()` to build clock frames.
- Clock frames must pass `ship_frame_decode()` before their decoded fields are
  passed to `ship_rtc_update()`. A replay counter is still checked.
- For Zigbee ZHA plans with more than two targets, add every target to the ZCL
  Groups cluster (`0x0004`, Add command `0x00`) before issuing source
  `zha_toolkit.bind_group`. Hue target endpoint is 11; standard ZCL target
  endpoint is 1.
- Compiler and MCP dry-runs must emit payloads without sending them. Review
  payloads before any intentional real-device operation.

### Graveyard of Dead Ends — Do Not Repeat

1. **OpenThread raw-link takeover:** Never call `otLinkRawSetReceiveDone()` or
   `otLinkRawTransmit()` with OpenThread IPv6 enabled; it can return
   `OT_ERROR_INVALID_STATE`. Use `otLinkSetPcapCallback()` for RX. For TX,
   dispatch through `esp_ieee802154_transmit()` or
   `nrf_802154_transmit_raw()` when the matching SoC driver is available;
   Nordic builds require `CONFIG_MPSL=y`.
2. **Per-message channel hopping:** Never hop 802.15.4 channels on
   `ESP32-C6`, `ESP32-H2`, or `nRF52840`. A receiver left on the active channel
   becomes deaf to the sender. Transmit on the active PAN channel and use the
   `0xA5` marker at offset 9.
3. **Archived Matter server:** Never target `python-matter-server` (archived
   June 2026 at v8.1.2). Target `matter-js/matterjs-server` at
   `ws://localhost:5580/ws`. Bind with the two-step transaction: write
   AccessControl cluster `0x001F` at `0/31/0`, then Binding cluster `0x001E`
   at `<source-endpoint>/30/0`.
4. **Unsafe Z-Wave direct associations:** Never allow Z-Wave Long Range nodes
   (`node_id >= 256`) or mismatched S2 security classes into direct Association
   (`0x85`) or Multi-Channel Association (`0x8E`) plans.
5. **Zigbee unicast popcorn:** Never bind more than two Zigbee lights using
   separate IEEE unicast bindings. Add target membership to a ZCL Group
   (`0x0004`) and bind the source switch to that group. Emit vendor preflight
   writes first: Inovelli `0xFC31` attribute `0x0012=1`; Aqara `0xFCC0`
   attribute `0x0200=0`; Tuya cluster `0x0006` attribute `0x8004=0`.
6. **Invalid radio combinations:** Never enable `espnow:` on `ESP32-H2` (it has
   no Wi-Fi PHY), or run concurrent `wifi:` and `zigbee: router: true` on
   `ESP32-C6` (shared RF-switch instability).

### v0.1.11 — Target-First Wizard & LEAT Rule Compiler

- **Wizard order is target-first** (`custom_components/ship_anchor/config_flow.py`):
  `target` → `source` → `behavior` → `finish`. The target's protocol anchors the
  whole flow; the source step lists **only** switches that share that protocol.
- **Cross-protocol guardrails:** `_infer_protocol(device)` derives a protocol from
  identifier namespaces / model strings; `_target_kind(device, entity_reg)`
  classifies the target (light/fan/valve/lock/cover/climate); and
  `_device_protocols_match(a, b)` is the safety guard — it treats ZHA and
  Zigbee2MQTT as the same Zigbee family but **rejects** e.g. Zigbee↔Z-Wave or
  Matter↔Shelly, so a source can never be paired to a target it cannot reach.
- **Dynamic behaviour:** `TARGET_CAPABILITIES` maps each target kind to the
  action parameters its form may show (lights → brightness/colour/transition;
  fans → speed; valves → open/close; locks → lock/unlock; climate → setpoint).
  Each rule collects a gesture (1x–5x Tap, Hold) plus an optional time-of-day
  window (`hour_start`, `hour_end`, overnight-aware).
- **LEAT compiler mapping:** `AnchorCompiler.build_leat_table(rules, group_id)`
  turns each rule into a row that matches the C struct `ship_leat_entry_t`
  (`src/ship_core/ship_protocol.h`) field-for-field: `{group_id, domain,
  gesture, action_type, action_param, hour_start, hour_end, enabled}`. Do NOT
  invent `param_2`/`param_3` fields on the LEAT row — the C struct has a single
  `action_param` byte. Values that need more than a byte (colour temperature in
  mireds is 153–500, fade time) ride in an optional `frame_params` annotation
  consumed by the 19-byte frame encoder, keeping the struct honest.
- **MCP surface is seven tools** (all preview-only, never actuating):
  `ship_inspect_device`, `ship_compile_binding`, `ship_bake_behavior` (LEAT),
  `ship_list_bindings` (inventory), `ship_encode_19b_frame`,
  `ship_decode_verify_19b_frame`, and `ship_run_verification_suite`.

### Repository map

| Path | Purpose |
|---|---|
| `README.md` | Project overview, wire format, benchmarks, quick start |
| `GUIDE_FOR_HUMANS.md` | Plain-English explanation, payload reference, MCP usage |
| `CLAUDE.md` / `AGENTS.md` | Agent memory and onboarding |
| `src/ship_core/ship_protocol.h` | Frame layout, enums, protocol and HAL APIs |
| `src/ship_core/ship_protocol.c` | SipHash, frame encode/decode, LEAT, Mesh-RTC |
| `src/ship_core/ship_radio_hal.c` | ESP-IDF, Nordic, OpenThread, native radio paths |
| `custom_components/ship_anchor/compiler.py` | Automation parser, multi-protocol binding compiler, and LEAT rule compiler |
| `custom_components/ship_anchor/quirks_inspector.py` | Zigbee endpoint, preflight, and group inspection |
| `custom_components/ship_anchor/__init__.py` | Home Assistant integration setup and services |
| `custom_components/ship_anchor/config_flow.py` | Target-first wizard (target → source → behavior → finish) and options flow |
| `custom_components/ship_anchor/automation.py` | HA automation normalization + entity-registry protocol fallback |
| `custom_components/ship_anchor/manifest.json` | Home Assistant integration metadata |
| `mcp/ship_anchor_mcp.py` | Seven-tool MCP stdio server (inspect, compile, bake LEAT, list bindings, encode/decode, verify), CLI, and demo |
| `sim/test_ship_core.c` | C protocol, clock round-trip, and native HAL tests |
| `sim/check_ship_fastpath_compile.py` | Host-side ESPHome component compile/link smoke test |
| `sim/run_anchor_benchmarks.py` | Six Python verification and disaster drills |
| `esphome/components/ship_fastpath/__init__.py` | ESPHome validation and code generation |
| `esphome/components/ship_fastpath/ship_fastpath.h` | ESPHome C++ component interface |
| `esphome/components/ship_fastpath/ship_fastpath.cpp` | ESPHome runtime behavior |
| `esphome/components/ship_fastpath/example_esp32c6_switch.yaml` | Example ESP32-C6 configuration |
| `hacs.json` | HACS repository metadata |
| `tests/conftest.py` | Pytest bootstrap: stubs `voluptuous` + `homeassistant.*` and provides `fake_hass` / `fake_store` fixtures |
| `tests/test_compiler_unit.py` | **Layer 1** — pure-logic tests for the LEAT builder and rule domain/action mapping |
| `tests/test_config_flow_unit.py` | **Layer 1** — protocol-match, target-kind, capability, and protocol-inference unit tests |
| `tests/test_integration.py` | **Layer 2** — wizard steps, `.storage/ship_anchor.bindings` round-trip, and resilient `erase_all` |

### Testing Pyramid

SHiP Anchor is verified by the industry-standard **Testing Pyramid**, bottom-up.
Each layer is independent and can run on a plain computer (no Home Assistant box,
no radios, no network) until the very top:

| Layer | What it is | Command | Target |
|---|---|---|---|
| **1 — Unit** | Fast, isolated tests of pure logic: `_device_protocols_match()`, `_target_kind()`, `_infer_protocol()`, `TARGET_CAPABILITIES`, and the LEAT row mapping (`build_leat_table()`, `_rule_domain()`, `_rule_action()`). No HA, no I/O. | `python3 -m pytest tests/ -v` | 36 passed |
| **2 — Integration** | Component tests that wire the wizard, the persistent store (`.storage/ship_anchor.bindings`), and `AnchorCompiler` together: cross-protocol source filtering, full target→source→behaviour→finish flow, JSON serialisation round-trip, and the resilient `erase_all`. | `python3 -m pytest tests/ -v` | (included above) |
| **3 — System / disaster** | The C protocol core (`test_ship_core.c`) and the Python simulation + MCP disaster drills (`run_anchor_benchmarks.py`), which *do* touch the real 19-byte encoder, SipHash, LEAT, Mesh-RTC, and radio HAL. | `gcc … && /tmp/test_ship_core` and `python3 sim/run_anchor_benchmarks.py` | 10/10 and 6/6 |

The base is wide and cheap (run on every change); the top is narrow and
expensive (run before release). **The invariant to protect:** the LEAT rows
produced at Layer 1 are field-for-field identical to the `ship_leat_entry_t`
struct asserted by the C suite at Layer 3 — a mismatch between the two is a
release blocker.

> **Honest caveat:** `pytest` is a dev dependency (installed via
> `python3 -m pip install --user pytest` in the authoring environment). The
> integration tests stub `voluptuous` and `homeassistant.*` via
> `tests/conftest.py`, so they run without Home Assistant installed. CI should
> `pip install pytest` before running `python3 -m pytest tests/ -v`.

### Run and validate

```bash
# Layer 1 + 2 — unit and integration (needs `pip install pytest`)
python3 -m pytest tests/ -v

# Layer 3 — C protocol core (10/10) and simulation/MCP disaster drills (6/6)
gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
  -I src/ship_core sim/test_ship_core.c \
  src/ship_core/ship_protocol.c src/ship_core/ship_radio_hal.c \
  -o /tmp/test_ship_core && /tmp/test_ship_core

python3 sim/run_anchor_benchmarks.py
python3 sim/check_ship_fastpath_compile.py
python3 mcp/ship_anchor_mcp.py --cli
python3 mcp/ship_anchor_mcp.py --demo
```

Use `python3 sim/run_anchor_benchmarks.py --skip-mcp` for the C suite and
Drills 1–5 without starting the MCP demo. The MCP schema check reports an exact
lexical-token count over compact JSON against a tripwire ceiling (currently
1,500; raised from 1,000 in v0.1.11 when two tools were added); model-specific
tokenizers can count different units. Drill 6 asserts the full seven-tool
surface and exercises `ship_bake_behavior` and `ship_list_bindings`.
