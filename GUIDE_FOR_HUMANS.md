# SHiP v0.1 — Complete Plain-English Guide for Humans
### *"You don't need to be a programmer to understand this."*

---

## LAYER 1 — The Kitchen-Table Explanation (ELI5)

Welcome. This guide explains **every part of Project Anchor** in plain English first, then in technical detail second. If you see a word you don't recognise, it will always be explained before it's used.

> *Want the plain-English history of **why** the project is built the way it is —
> every milestone from the very first release up to today, with its calendar
> date? That lives in* [**ARCH_DECISIONS.md**](ARCH_DECISIONS.md)*, the
> Architectural Decision Record.*

---

## Part 1: Why Does Your Smart Home Break During a Reboot?

Picture a smart home as a bustling office building.

- Your **wall switches and sensors** are like workers on different floors. When someone presses a button, they want to send a message to another floor ("please turn on the lights in Room 201").
- Your **Home Assistant server** is the building's telephone switchboard operator — every message passes through it.
- Your **Wi-Fi router** is the hallway connecting all the phones.

**The problem:** Modern smart home devices were designed to *always* route messages through the switchboard. So when the operator goes on lunch break (the hub reboots for an update), or the hallway floods (the router hiccups after a power blink), **no messages get through**. Lights stop responding. Automations go silent.

**SHiP's fix:** Program the workers to remember each other's desk phone numbers directly. The light on Floor 2 and the switch on Floor 1 have each other's direct lines — they whisper to each other on a private radio frequency that bypasses the switchboard entirely.

---

## Part 2: Two Ways SHiP Does This (The Two Tiers)

### Tier 1 — The "Offline Compiler" (for your existing devices)

**ELI5:** You already have smart devices at home — a Zigbee light switch, some Philips Hue bulbs, maybe a Shelly plug. The Offline Compiler reads the automations you already created in Home Assistant ("when I press the kitchen switch, turn on the kitchen lights") and quietly programs the devices to do this directly — without the hub being in the loop.

**What it does in plain steps:**
1. **Reads** your Home Assistant automation list.
2. **Identifies** which devices can talk directly (not all can — see the guards below).
3. **Runs pre-flight checks** — makes sure the devices are in the right mode to talk directly (some switches cut power to bulbs by default, which kills the bulb's radio; the compiler fixes that).
4. **Programs** the binding — the switch now has the bulb's direct address in its memory.
5. **Saves a log** of what it did, so it can undo it later if you call `ship_anchor.erase_all`.

**Supported device types:**

| Protocol | Plain English Name | Manufacturer Examples |
|---|---|---|
| Zigbee (ZHA) | The most popular smart home radio | Philips Hue, Inovelli, Aqara, SONOFF, IKEA |
| Zigbee (Z2M) | Same radio, different software bridge | All Zigbee devices via Zigbee2MQTT |
| Matter 1.3–1.6 | The "universal language" standard (2023+) | Apple HomePod, Amazon Echo, Google Nest |
| Z-Wave 700/800 | An older, reliable home automation radio | Zooz, Fibaro, Aeotec |
| Shelly Gen2/3/4 | Wi-Fi plug and switch devices | Shelly Plus, Shelly Pro |
| Tasmota | Open-source firmware for cheap Wi-Fi plugs | Gosund, BlitzWolf, many others |

---

### Tier 2 — The 19-Byte Fast-Path (for open hardware)

**ELI5:** For devices you build yourself (or open hardware kits), SHiP goes one level deeper. Instead of programming "remember this bulb's address," it teaches devices to speak a brand-new secret language — a tiny 19-word sentence whispered over the radio in under one millisecond. Any device on your radio network that speaks this language instantly acts on it, no hub, no router, no internet needed.

The 19-byte sentence is like a super-compressed telegram:

```
[WHO AM I] [WHO IS THIS FOR] [🔐 SECRET STAMP] [WHAT TO DO] [HOW MUCH]
```

Every word (byte) has an exact, fixed meaning. The secret stamp (HMAC) at the end proves the telegram wasn't forged or replayed.

---

## Part 3: The Three Pre-Flight Guards (What the Compiler Checks First)

Before programming any binding, the Offline Compiler runs three checks:

### Pre-Flight 1 — "Are you going to cut the bulb's power?"

Some smart wall switches are designed to physically cut the electricity to a bulb when pressed. This is a problem because smart bulbs need constant power to keep their radio alive. If power is cut, the bulb "disappears" from the network.

The compiler automatically fixes this for known device families:

| Device | Problem | Fix Written by Compiler |
|---|---|---|
| **Inovelli Blue (VZM31-SN, VZM30-SN, VZM35-SN)** | Default mode cuts relay power | Write `smart_bulb_mode = 1` to Manufacturer Cluster `0xFC31` (64561), Attribute `0x0012` |
| **Aqara H1 / Opple Switches** | Default mode controls local relay | Write `operation_mode = 0` (decoupled) to Manufacturer Cluster `0xFCC0` (64704), Attribute `0x0200` |
| **Tuya / MOES TS004F buttons** | Default mode = Coordinator Event (won't bind directly) | Write `switch_mode = 0` (Command mode) to Cluster `0x0006`, Attribute `0x8004` (32772) |
| **SONOFF ZBMINIR2 Extreme** | Relay may disconnect when hub is offline | Enable `detach_relay` mode via Attribute `0x8002` |

### Pre-Flight 2 — "Which connection socket does your bulb use?"

Every Zigbee device has numbered connection sockets called *endpoints* (think of them like different phone extension numbers within the same device). Philips Hue bulbs use **extension 11**. Almost everything else uses **extension 1**. If the compiler sends messages to the wrong extension, the bulb ignores them.

The compiler looks up the correct extension automatically from its built-in device database and from the live `zigpy/zha-device-handlers` library if it's installed.

### Pre-Flight 3 — The Anti-Popcorn Rule

**ELI5:** If you want one switch to control **more than 2 bulbs**, and the switch sends each bulb a separate message one after another, the bulbs turn on one by one with a visible delay — like popcorn popping. 

The fix: put all the bulbs into a **Zigbee Group** (like a group text message). The switch sends **one message** to the group, and all bulbs hear it simultaneously.

The compiler automatically does this whenever more than 2 bulbs are targeted:
- First adds every bulb to that group using the ZCL Groups cluster Add command.
- Then uses `zha_toolkit.bind_group` on the switch instead of `bind_ieee`.
- Or publishes to `zigbee2mqtt/bridge/request/device/bind` with a group as the target

---

## Part 4: The Five Safety Guards (Things That Block Binding)

### Guard 1 — Matter Switch-Only Cluster Block

Some Matter button devices only implement the "Switch" cluster (ID 59 / `0x003B`). This means the button knows how to report "I was pressed" but does NOT know how to send "turn on" commands. These devices **cannot** bind offline.

When the compiler detects this, it shows:

```
ELI5 COMPATIBILITY WARNING — Matter Node 38:
  This Matter button only has a 'Switch' cluster (ID 59), which reports
  button presses but cannot send On/Off commands itself.
  RECOMMENDED FIX: Use a Matter device that also has the OnOff Client
  cluster (ID 6), OR upgrade to Tier 2 SHiP firmware.
```

### Guard 2 — Z-Wave Long Range (ZWLR) Block

Z-Wave Long Range devices (Node ID ≥ 256) use a **hub-only star** architecture — they are physically designed to only talk to the central hub, never to each other. The compiler detects and blocks these:

```
ELI5 — Z-Wave Long Range BLOCK: Node 260 is a ZWLR device.
  ZWLR uses a hub-only 'star' design. Devices cannot talk directly
  to each other, by design. Options: use standard Z-Wave 700/800
  instead, or upgrade to Tier 2 SHiP firmware.
```

### Guard 3 — Z-Wave Security Class Mismatch

Both the source and target Z-Wave devices must have the same security badge:
- `S2 Access Control` (highest — for locks and alarms)
- `S2 Authenticated` (medium — for most devices)
- `S2 Unauthenticated` (low)
- `S0 Legacy` (old)
- `None` (no encryption)

Mismatched security classes cause associations to silently fail. The compiler blocks this and explains which class each device has.

### Guard 4 — ESP32-H2 + ESP-NOW Block

The ESP32-H2 chip has **no Wi-Fi hardware**. ESP-NOW requires Wi-Fi. The ESPHome component validates the YAML at compile time and raises an error if `transport: espnow` is set on an H2:

```
ELI5 Hardware Error: The ESP32-H2 has no Wi-Fi antenna.
  Change 'transport: espnow' → 'transport: ieee802154'
```

### Guard 5 — ESP32-C6 Dual-Radio Warning

The ESP32-C6 has ONE shared 2.4 GHz radio path. Using Wi-Fi and Zigbee Router mode simultaneously causes packet collisions. A compile-time `#warning` is injected into the generated firmware.

---

## Part 5: The 19-Byte SHiP Frame — Every Byte Explained

The Tier 2 fast-path uses a 19-byte (19-word) radio telegram. Here is what each word means in plain English:

| Bytes | Name | Plain English |
|---|---|---|
| 0–1 | Frame Control (FCF) | "Is this a group shout (broadcast) or a private message (unicast)?" |
| 2 | Sequence Number | "This is message #N in a row" (so duplicate packets are detected) |
| 3–4 | PAN ID | "We are all on the same radio team (network)" |
| 5–6 | Destination Address | 0xFFFF = "everyone hear this" / or a specific short address |
| 7–8 | Source Address | "I am device #XXXX" |
| 9 | Magic Byte (0xA5) | "This is a SHiP packet, not a regular Zigbee/Thread packet" |
| 10 | Group ID | "This message is for Group N (like a walkie-talkie channel)" |
| 11–12 | Replay Counter | "This is my 42nd message ever — older ones are already rejected" |
| 13 | Command+Click | Upper nibble = WHAT (lights/valve/lock/HVAC), Lower nibble = HOW (1×/2×/.../5× press, hold, release) |
| 14 | Parameter 1 | Main value: brightness level / scene ID / valve % / temperature |
| 15 | Parameter 2 | Second value: colour temperature / fan speed / shade tilt |
| 16 | Parameter 3 | Third value: fade time / share mask / emergency override |
| 17–18 | HMAC Stamp | 16-bit SipHash-2-4 security signature — proves this packet is genuine |

**Total: 19 bytes** → on-air time at 250 kbps = **0.864 milliseconds** (less than one blink of an eye).

---

## Part 6: The Mesh Real-Time Clock (Mesh-RTC)

**ELI5:** Some automations only make sense at certain times of day. "Dim lights to 5% if it's between 2:00 AM and 6:00 AM." Without a hub, who keeps track of time?

SHiP devices keep their own clocks in sync by occasionally broadcasting a special "time sync" whisper to the whole group (once per minute). Any device that hears a time sync frame updates its own internal clock if it's off by more than 2 seconds.

This means time-of-day rules (stored in the LEAT — see below) keep working even when every router and hub in the home is off.

**How a Mesh-RTC sync frame looks (domain = `0xF`):**
- `param_1`: UTC timestamp byte 0 (LSB)
- `param_2`: UTC timestamp byte 1
- `param_3`: UTC timestamp byte 2
- Byte 17 (hmac_trunc16 lower byte): UTC timestamp byte 3 (MSB)
- Byte 18 (hmac_trunc16 upper byte): 8-bit SipHash-2-4 HMAC (compact signature over bytes 9..17)

---

## Part 7: The Local Event-Action Table (LEAT)

**ELI5:** Each SHiP device carries a "cheat sheet" (up to 32 rules) in its own memory. When it hears a matching radio whisper, it acts immediately — no network request, no hub, no cloud.

Each rule has five fields:

| Field | Meaning |
|---|---|
| `group_id` | Which SHiP group triggers this rule |
| `domain` | What type of command (lights, valve, lock, HVAC...) |
| `gesture` | How it was triggered (1×, 2×, 3×, 4×, 5× click, hold, release) |
| `hour_start` / `hour_end` | Only fire this rule between these hours (UTC, from Mesh-RTC) |
| `action_type` + `action_param` | What to do (toggle relay, set brightness, recall scene...) |

**Example LEAT table for a kitchen switch:**

```
Rule 0: group=1, domain=OnOff, gesture=1×click, hours=0–23   → toggle relay
Rule 1: group=1, domain=Lighting, gesture=5×click, hours=2–2 → brightness=5%
```

Rule 1 only fires between 2:00 AM and 2:59 AM (the "goodnight nightlight" rule).

---

## Part 8: Running the Verification Suite

The protocol code and host-side simulations have automated checks. These do
not verify Home Assistant installation, real device operations, or radio
performance. Run the checks yourself:

### C Core Tests (10 tests, all pass)
```bash
cd project-anchor-ship

# Compile
gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
    -I src/ship_core \
    sim/test_ship_core.c \
    src/ship_core/ship_protocol.c \
    src/ship_core/ship_radio_hal.c \
    -o /tmp/test_ship_core

# Run
/tmp/test_ship_core
```

**Expected output:**
```
RESULTS: 11/11 tests passed  — ALL PASS ✓
```

### Full Disaster Simulation (6 drills, all pass)
```bash
python3 sim/run_anchor_benchmarks.py
```

**Expected output from the simulator (not a hardware or Home Assistant test):**
```
ALL 6/6 DRILLS PASSED ✓
Simulation checks passed — real Home Assistant and hardware validation remain.
```

### 30-Light “Goodnight” Scene Benchmark

**ELI5:** Sending one message per light takes longer than announcing a single
group message that all 30 lights can hear. The table compares those two
approaches with standard Matter-over-Thread unicast.

| Mode | Frames | Raw airtime | Same-slot collision model |
|---|---:|---:|---:|
| Matter-over-Thread unicast sweep | 30 | 99.8 ms | 100.0% |
| SHiP unicast sweep | 30 | 25.9 ms | 100.0% |
| SHiP group broadcast to `0xFFFF` | 1 | 0.864 ms | 0.0% |

The 19-byte SHiP payload uses a 21-byte PSDU: `1 - (21 / 98) = 78.6%`
smaller. Including 6 PHY overhead bytes, total PHY airtime is
`1 - (27 / 104) = 74.0%` less. The contention model calculates the chance
that 30 independent unicast transmitters choose the same one of 32 initial
backoff slots (each 320 µs); it models a single group broadcaster as one
transmitter. The 50-node recovery drill starts with simultaneous blackout
attempts and applies randomized 50–2000 ms retry delays until no collision
remains. These are model results, not RF lab measurements.

---

## Part 9: Exact Protocol Payloads Reference

### Zigbee (ZHA) — Inovelli Blue + 4 Philips Hue Bulbs

```yaml
action:
  # Step 1: Keep the Inovelli relay powered when its button is pressed.
  - action: zha.set_zigbee_cluster_attribute
    data:
      ieee: "00:0d:6f:00:05:7d:2d:34"
      endpoint_id: 1
      cluster_id: 64561       # 0xFC31 — Inovelli manufacturer cluster
      cluster_type: in
      attribute: 18           # 0x0012 — smart_bulb_mode
      value: 1
      manufacturer: 4655      # 0x122F — Inovelli manufacturer code

  # Step 2: Add each Hue bulb to the group (repeat for all 4).
  # Hue uses endpoint 11; ordinary ZCL bulbs generally use endpoint 1.
  - action: zha.issue_zigbee_cluster_command
    data:
      ieee: "00:17:88:01:09:aa:bb:01"
      endpoint_id: 11
      cluster_id: 4           # 0x0004 — Groups cluster
      cluster_type: in
      command: 0              # 0x00 — Add Group
      args: [2, "anchor_grp_2"]

  # Step 3: Bind the source switch after all target Add commands.
  - action: zha_toolkit.bind_group
    data:
      ieee: "00:0d:6f:00:05:7d:2d:34"
      command_data: 0x0002
      src_endpoint: 2
      cluster: 6              # 0x0006 — On/Off
      tries: 100
```

### Zigbee (Zigbee2MQTT) — Direct binding

```json
Topic: zigbee2mqtt/bridge/request/device/bind
{
  "from": "kitchen_dimmer",
  "from_endpoint": 2,
  "to": "kitchen_lights_group",
  "clusters": ["genScenes", "genOnOff", "genLevelCtrl"]
}
```

### Matter 1.3+ — Switch Node 38 → Light Node 37

```json
// Step 1: Write ACL on the target light (permission to be controlled)
{
  "message_id": "acl_write_38_37",
  "command": "write_attribute",
  "args": {
    "node_id": 37,
    "attribute_path": "0/31/0",
    "value": [
      {"1": 5, "2": 2, "3": [112233], "4": null, "254": 2},
      {"1": 3, "2": 2, "3": [38],
       "4": [{"0": 6, "1": 1, "2": null}, {"0": 8, "1": 1, "2": null}],
       "254": 2}
    ]
  }
}

// Step 2: Write Binding Table on the source switch (where to send commands)
{
  "message_id": "bind_write_38_37",
  "command": "write_attribute",
  "args": {
    "node_id": 38,
    "attribute_path": "2/30/0",
    "value": [
      {"1": 37, "2": null, "3": 1, "4": 6, "254": 2},
      {"1": 37, "2": null, "3": 1, "4": 8, "254": 2}
    ]
  }
}
```

**TLV Tag Mapping (plain English):**

| Tag # | Field | Plain English |
|---|---|---|
| `1` | `privilege` | Access level: 3=Operate, 5=Administer |
| `2` | `authMode` | 2 = CASE (device-to-device authentication) |
| `3` | `subjects` | List of Node IDs allowed to send commands |
| `4` | `targets` | Which clusters/endpoints are accessible |
| `254` | `fabricIndex` | Which Matter fabric (network) this applies to |

### Z-Wave 700/800 — Single-Channel Association (CC 133)

```yaml
service: zwave_js.invoke_cc_api
data:
  node_id: 5                 # Source switch node
  command_class: 133         # 0x85 = Association
  endpoint: 0
  method_name: "addNodeIds"
  parameters: [1, 6]        # [group_id=1, target_node_id=6]
```

### Z-Wave Multi-Channel Association (CC 142) — target a specific endpoint

```yaml
service: zwave_js.invoke_cc_api
data:
  node_id: 5
  command_class: 142         # 0x8E = Multi-Channel Association
  endpoint: 0
  method_name: "addDestinations"
  parameters:
    - groupId: 1
      nodeIds: []
      endpoints:
        - nodeId: 6
          endpoint: 1        # Target relay 1 on a dual-outlet device
```

### Shelly Gen2 — Local mJS Script Deployment

```
POST http://192.168.1.100/rpc/Script.Create   {"name": "ship_anchor_relay"}
POST http://192.168.1.100/rpc/Script.PutCode  {"id": 1, "code": "<mJS code>"}
POST http://192.168.1.100/rpc/Script.SetConfig {"id": 1, "config": {"enable": true}}
POST http://192.168.1.100/rpc/Script.Start    {"id": 1}
```

### Tasmota Device Groups — Exact Backlog Commands

```bash
# Controller switch (broadcasts state changes to the group)
Backlog SetOption85 1; SetOption88 1; DevGroupName1 anchor_grp_1; DevGroupShare 0xffffffff,0xffffffff

# Target loads (receive-only — do NOT echo back to avoid loops)
Backlog SetOption85 1; DevGroupName1 anchor_grp_1; DevGroupShare 0xffffffff,0

# Scene override lock (prevents stale state from overriding a scene after hub reboot)
DevGroupSend 129=@|18
```

**Bit-field meanings:**
- `SetOption85 1` → Enable Device Groups
- `SetOption88 1` → Detach relay from switch (sends commands but keeps its own relay closed)
- `DevGroupShare 0xffffffff,0xffffffff` → Share ALL state bits in both directions
- `DevGroupShare 0xffffffff,0` → Receive ALL bits, send NOTHING back (prevents echo)
- `DevGroupSend 129=@|18` → Broadcast NoStatusShare lock: bit 2 (brightness) + bit 16 (colour) locked

---

## Part 10: Glossary — Every Technical Term Explained

| Term | Plain English |
|---|---|
| **ZCL** (Zigbee Cluster Library) | The standardised "vocabulary" all Zigbee devices use — like a common language rulebook |
| **ZDO** (Zigbee Device Object) | The "receptionist" that handles device discovery and binding setup on a Zigbee device |
| **Endpoint** | A numbered "socket" on a Zigbee device — like different extension numbers on an office phone |
| **Quirk** | A software patch for a specific device that doesn't quite follow the standard — like a special adapter |
| **PAN ID** | Personal Area Network ID — like a Wi-Fi SSID but for Zigbee/Thread networks |
| **PSDU** | Physical layer payload — the actual bytes the radio transmits (including data + FCS checksum) |
| **FCS** | Frame Check Sequence — a 2-byte checksum the radio hardware appends and verifies automatically |
| **CASE** | Certificate-Authenticated Session Establishment — how Matter devices verify each other's identity |
| **TLV** | Tag-Length-Value — a compact binary encoding format used in Matter attribute writes |
| **CC** (Command Class) | Z-Wave's equivalent of Zigbee clusters — standardised feature sets |
| **ZWLR** | Z-Wave Long Range — a hub-only variant of Z-Wave that cannot do direct device-to-device communication |
| **MPSL** | Multi-Protocol Service Layer — Nordic's radio scheduler that manages time slots for BLE + Thread + 802.15.4 simultaneously |
| **6LoWPAN** | A compression protocol that squeezes IPv6 packets small enough to fit in 802.15.4 radio frames |
| **CSMA-CA** | Carrier Sense Multiple Access with Collision Avoidance — "listen before you talk" on the radio channel |
| **HMAC** | Hash-based Message Authentication Code — a cryptographic stamp proving a message is genuine |
| **LEAT** | Local Event-Action Table — the cheat sheet stored in each SHiP device's memory |
| **Mesh-RTC** | Mesh Real-Time Clock — a clock kept in sync across all SHiP devices via radio whispers |
| **DGR** | Device Group — Tasmota's multicast state-sharing mechanism |
| **mJS** | micro-JavaScript — a tiny JavaScript engine that runs locally inside Shelly Gen2+ devices |
| **ESP-NOW** | Espressif's proprietary low-latency Wi-Fi direct protocol (requires Wi-Fi hardware — not available on ESP32-H2) |
| **SipHash-2-4** | A fast, cryptographically strong hash function used as SHiP's HMAC; designed by Aumasson & Bernstein (2012) |
| **Anti-Popcorn Rule** | The rule that groups >2 Zigbee targets into one Group Binding to prevent staggered turn-on delays |
| **NoStatusShare** | A Tasmota-inspired lock that tells devices "don't echo your status back after this scene change" |

---

## Part 11: File-by-File Guide

| File | What it does in plain English |
|---|---|
| [`src/ship_core/ship_protocol.h`](src/ship_core/ship_protocol.h) | The "rulebook" — defines the 19-byte frame struct and all enumerations |
| [`src/ship_core/ship_protocol.c`](src/ship_core/ship_protocol.c) | The "engine" — SipHash-2-4, encode/decode, anti-replay, LEAT matcher, Mesh-RTC |
| [`src/ship_core/ship_radio_hal.c`](src/ship_core/ship_radio_hal.c) | The "universal remote" — radio transmit/receive for ESP-IDF, Nordic NCS, OpenThread, and native simulation |
| [`esphome/components/ship_fastpath/__init__.py`](esphome/components/ship_fastpath/__init__.py) | The "hardware safety inspector" — validates YAML, enforces H2/C6/nRF limits |
| [`esphome/components/ship_fastpath/ship_fastpath.h`](esphome/components/ship_fastpath/ship_fastpath.h) | The "component blueprint" — C++ class declaration |
| [`esphome/components/ship_fastpath/ship_fastpath.cpp`](esphome/components/ship_fastpath/ship_fastpath.cpp) | The "component engine" — setup, loop, TX, RX dispatch, LEAT, Mesh-RTC sync |
| [`esphome/components/ship_fastpath/example_esp32c6_switch.yaml`](esphome/components/ship_fastpath/example_esp32c6_switch.yaml) | The "recipe card" — copy-paste YAML for an ESP32-C6 switch with 1×/2×/5× tap |
| [`custom_components/ship_anchor/manifest.json`](custom_components/ship_anchor/manifest.json) | The "business card" — tells Home Assistant and HACS who this integration is |
| [`custom_components/ship_anchor/__init__.py`](custom_components/ship_anchor/__init__.py) | The "front door" — loads the integration, registers `compile` and `erase` services |
| [`custom_components/ship_anchor/quirks_inspector.py`](custom_components/ship_anchor/quirks_inspector.py) | The "device translator" — finds correct endpoints, modes, and pre-flight attribute writes for each device |
| [`custom_components/ship_anchor/compiler.py`](custom_components/ship_anchor/compiler.py) | The "offline programmer" — normalizes automations and creates binding payloads |
| [`mcp/ship_anchor_mcp.py`](mcp/ship_anchor_mcp.py) | The "AI adapter" — eight MCP tools (inspect, compile, bake LEAT, list bindings, encode/decode, verify, mesh diagnostic), stdio JSON-RPC, CLI, and demo mode |
| [`sim/test_ship_core.c`](sim/test_ship_core.c) | The "flight simulator" — 11 C unit tests covering the protocol and native HAL |
| [`sim/check_ship_fastpath_compile.py`](sim/check_ship_fastpath_compile.py) | A host-side C++ compile/link check for the ESPHome component and SHiP core |
| [`sim/run_anchor_benchmarks.py`](sim/run_anchor_benchmarks.py) | The "fire drill coordinator" — six disaster scenarios and benchmark comparisons |
| [`CLAUDE.md`](CLAUDE.md) | ELI5-first engineering memory and documented dead ends |
| [`AGENTS.md`](AGENTS.md) | Agent onboarding link to the project memory |

---

## Part 12: Multi-Way Switches & Keeping Multiple Controllers in Sync

### Layer 1 — The Kitchen-Table Explanation (ELI5)

Imagine the classic bedroom **3-way switch**: one switch by the door, one by
the bed, both controlling the same bedside lamp. In a normal house they
"talk to each other" through the wiring in the wall — flip either one and
the lamp, and the other switch, reflect the change immediately. No remote,
no app, no internet.

Now imagine those same two switches are **smart devices**, and your
Home Assistant hub is rebooted, or the internet is down. Most smart
switches go dumb: each one only talks to the cloud, so pressing Switch A
doors nothing on Switch B — and the bulbs stop staying in sync.

SHiP fixes this the way real 3-way wiring works. Both switches and all the
bulbs share one **local group channel** — a tiny walkie-talkie frequency
that only your devices know about. Because they all listen to that one
channel:

- Pressing **Switch A by the door** is *instantly heard* by **Switch B by
  the bed** and by every bulb in the group.
- The bulbs update their lights, and each switch updates its little LED or
  rocker state, to match — **without the hub, the router, or the cloud in
  the middle**.
- It all happens in well under one millisecond on the air (a 19-byte
  message at 250 kbps), so it feels exactly like a normal 3-way switch.

One more thing keeps this from turning into a shouting match (see Layer 2):
when a switch *receives* the "the lights changed" announcement, it quietly
updates itself **without shouting a confirmation back** — otherwise every
device would reply to every other device, forever.

### Layer 2 — The Under-the-Hood Engineering

**The shared Group ID is the broadcast bus.**

Every SHiP frame carries a `group_id` (byte 10 of the 19-byte frame) — a
0–255 channel number that only the devices in one logical group use. A
multi-way setup is exactly one group:

- Each switch is a **sender**: on button press it encodes a 19-byte frame
  with its group's `group_id`, a domain, and the gesture (single/double/
  triple tap, hold, release).
- Each bulb and each *other* switch is a **receiver**: it accepts the frame
  if `group_id` matches its own and the SipHash-2-4 tag and anti-replay
  counter check out — then applies the action locally (LEAT rule or relay
  toggle) and updates its own reflected state.
- Because the frame is a broadcast on the shared group channel, the hub is
  never a relay: it is completely absent from the path. This is what keeps
  multi-way switching working during a full hub outage.

**The `NoStatusShare` flag (gesture `0x8`) stops the echo war.**

Naively, every receiver that updates its state would want to announce the
new state back to the group — and each of those announcements would trigger
another round of announcements, cascading forever (the classic
"status echo loop").

SHiP borrows the fix from Tasmota's Device Groups (`DevGroupShare
0xffffffff,0` = "receive everything, send nothing back") and encodes it as a
dedicated gesture in the lower nibble of byte 13 (`cmd_click`):

| Gesture nibble | Meaning |
|---|---|
| `0x1`–`0x5` | 1× to 5× click |
| `0x6` | Hold |
| `0x7` | Release |
| **`0x8`** | **`SHIP_GESTURE_NO_STATUS_SHARE` — "apply this change, but do NOT broadcast your new status back"** |

So a multi-way scene change travels exactly once around the group: the
pressing switch broadcasts the change with the `0x8` lock, every peer
switch and bulb applies it locally, and none of them re-announce. One radio
burst, N synchronized devices, zero echo loops — with the hub offline.

*(The C core verifies this exact flag in Test 9 — "Tasmota NoStatusShare
flag (gesture = 0x8)" — see `sim/test_ship_core.c`.)*

---

## Part 13: The Bidirectional Wizard — Picking Devices, Gestures & Time Windows

### Layer 1 — The Kitchen-Table Explanation

The setup wizard (Settings → Devices & Services → Add Integration → SHiP Anchor)
asks **which end of the chain you already know** — and then walks you to the
other end. Since v0.1.14 it is fully **bidirectional**: you can start from the
device you want to **control** (target-first) *or* from the switch/button you
want to **use** (source-first). Think of it like installing a 3-way light: you
may already know where the lamp lives, or you may already know where the
switch lives — either way the wizard reaches the other end and connects them.

Step by step:

1. **Pick a direction** — "I want to control a device" (target-first) or
   "I have a switch / button" (source-first).
2. **Anchor that end** — choose one or more devices. SHiP Anchor immediately
   detects the protocol (Zigbee, Matter, Z-Wave, Shelly, Tasmota) and, for
   targets, their *kind*.
3. **Pick the other end** — *only* devices that speak the **same protocol**
   as the anchor are listed, so a Zigbee switch can never be paired with a
   Z-Wave lock, in either direction. Pick several switches for a multi-way
   (3-way) setup.
4. **Build the rule sheet** — up to four rules, each with a gesture (1x–5x
   tap or Hold), an optional time-of-day window (e.g. active only between 22:00
   and 06:00), and the action parameters the target kind supports (brightness
   + colour temp for lights, speed for fans, open/close for valves,
   lock/unlock for locks).
5. **Optional intermediate module** — if the signal passes through a ceiling
   canopy, relay box, or mesh repeater before reaching the load, name it here
   (see Part 14). Leave empty for a direct one-hop binding.
6. **Preview or apply** — dry-run prints the exact payloads (binding calls +
   LEAT rows) in the log; apply sends them to the devices.

### Layer 2 — The Engineering Ground Truth

- **Protocol filtering** uses `_device_protocols_match()` in
  `config_flow.py` — ZHA and Z2M cross-match (both Zigbee), but Zigbee↔Z-Wave,
  Matter↔Shelly, etc. are rejected.
- **Target-kind detection** (`_target_kind()`) maps device → capability set
  from `TARGET_CAPABILITIES`, which drives which behaviour-step fields are
  rendered.
- **Rule compilation** runs through
  `AnchorCompiler.build_leat_table()` in `compiler.py`. Each row mirrors the
  C struct `ship_leat_entry_t` field-for-field:
  `{group_id, domain, gesture, action_type, action_param, hour_start,
  hour_end, enabled}`. Values that need more than one byte (colour
  temperature in mireds 153–500, fade time) are carried in an optional
  `frame_params` annotation that the 19-byte frame encoder uses — they are
  NOT invented into the C struct.
- **Time-of-day windows** honour the overnight case (e.g. 22:00 → 06:00)
  via the `hour_start > hour_end` branch in `ship_leat_match()` (see
  `src/ship_core/ship_protocol.c`).

---

## Part 14: Multi-Hop Canopy Routing — Chain-Like vs. Broadcast-Like Architectures

### Layer 1 — The Kitchen-Table Explanation

Some rooms are wired in a **chain**, not a single wire. A classic ceiling fan
with a light kit is the usual example: the switch does not talk to the bulb
directly. It talks to the **fan canopy** (the little motor / relay box under
the ceiling), and *that* module powers the bulb. In smart-home terms:

```
  Switch  ────( Hop 1 )────►  Ceiling Canopy  ────( Hop 2 )────►  Bulb / Fan
```

A **multi-hop (chain-like) binding** is SHiP Anchor's way of programming that
topology. You tell the wizard the switch, the canopy, and the load — and it
builds **one payload per hop**, in order, so the gesture + rule travels through
the canopy and out to the load. Every hop is a normal one-hop binding to the
next device; the chain is just the sequence of those hops.

This is the **immediate, shipping** implementation. It is ideal for **physical
power-gating ceiling canopies**: the canopy is a real device on the same bus
that must be told to pass the signal through, and the bulb behind it is a
second leg.

> **Why not just one broadcast?** Because in a chain the *middle* device is a
> real participant — it has to receive Hop 1 and re-emit Hop 2. A single
> multicast frame would skip it, and the bulb would never see the command.

### Layer 2 — The Engineering Ground Truth

`AnchorCompiler.async_compile_multihop(chain, rules, dry_run, group_name)`
(`compiler.py`, v0.1.14) turns a list of wizard device entries — in the order
the gesture should travel — into a **sequential** set of payloads:

- **Hop 1:** `chain[0]` (switch) → `chain[1]` (canopy) is compiled through
  the *same* per-protocol handler a one-hop binding uses.
- **Hop 2:** `chain[1]` (canopy) → `chain[2]` (load) is compiled the same way.
- **LEAT rows** are compiled **once** and attached to every hop, so the
  gesture + time-window + parameters are identical at each stage of the chain.
- Each hop's protocol is inferred from its **source** device's identifier
  namespace (`_hop_protocol()`), so a Z-Wave switch can drive a Zigbee canopy
  leg as long as that leg's own handler supports it.

**Chain-Like (implemented now)** vs. **Broadcast-Like (roadmap):**

| Aspect | Chain-Like (v0.1.14) | Broadcast-Like (future) |
|---|---|---|
| Topology | Sequential `A → B → C` | One frame to a **group** `{A, B, C}` |
| Middle device | Actively participates (relays) | Passive listener (or absent) |
| Frames on air | One per hop | One multicast, de-duplicated by group |
| Best for | Power-gating canopies, physical relay boxes | Large fan / bulb clusters that should all flip together |
| Status in this repo | **Implemented** in `compiler.py` | **Architectural roadmap** — not yet shipped |

The **Broadcast-Like (Multicast Group Mesh)** approach is the next step on the
roadmap: instead of N sequential hops, the group leader emits a single
multicast to a ZCL Group / Tasmota Device-Group / Shelly multicast, and every
member applies it locally (with the `NoStatusShare` lock from Part 12 to keep
echo loops out). That is strictly more efficient for *large, flat* clusters
where no middle device must relay — but it does **not** cover the physical
canopy case, which is why the chain-like path ships first. The two are
complementary: a canopy chain is a *sub-topology* that a future multicast mesh
could wrap.

*(Multi-hop is covered by `tests/test_multihop_unit.py` — Layer 1 of the
Testing Pyramid — and the end-to-end wizard path in `tests/test_integration.py`.)*

---

## Part 15: Ask an AI Agent with MCP

### Layer 1 — The Kitchen-Table Explanation

The MCP server is like a small help desk for AI assistants. Rather than reading
every project file, an assistant can ask one focused question: inspect a device,
preview its binding commands, encode or verify a radio frame, or run the checks.
Binding is preview-only through MCP; it does not send commands to real devices.

### Layer 2 — The Engineering Ground Truth

The server uses only the Python 3.9+ standard library and exposes these tools:
`ship_inspect_device`, `ship_compile_binding`, `ship_bake_behavior`,
`ship_list_bindings`, `ship_encode_19b_frame`, `ship_decode_verify_19b_frame`,
and `ship_run_verification_suite`.

- `ship_bake_behavior` — turn gesture + time-window rules into LEAT rows
  (matches the C `ship_leat_entry_t` struct).
- `ship_list_bindings` — summarise a stored bindings list into an inventory.

```bash
# Start a stdio MCP server for a compatible client
python3 mcp/ship_anchor_mcp.py

# List the compact schemas or try all eight tools locally
python3 mcp/ship_anchor_mcp.py --cli
python3 mcp/ship_anchor_mcp.py --demo
```

The displayed schema token count is an exact lexical count of compact JSON
tokens; model-specific BPE token counts vary by MCP client.

---

## Part 16: The Visual LEAT & Multi-Hop Inspector Dashboard

### Layer 1 — The Kitchen-Table Explanation

Remember the options dialog being "a set of hidden buttons"? In v0.1.15 it is
now a **dashboard** you can *read and edit* in plain English — like the
"About" page of a phone, except for your offline switch bindings.

Open it from **Settings → Devices & Services → SHiP Anchor → Configure**.
Instead of a wall of JSON, the dashboard says:

> **Chain:** Hall Switch → Ceiling Canopy → Kitchen Light
> **Protocol:** ZHA (Zigbee)
> **Configured LEAT rules (2).**
> 1. On a double tap: set brightness to 128.
> 2. On a long press (between 22:00 and 06:00): open the valve.

That last line is the whole point: **every rule is one English sentence** that
a non-engineer can understand at a glance — including the time-of-day window
and the chain it rides through. From the same screen you pick an action:

- **Add a rule** — fill in a gesture (single/double/…/quintuple tap or a long
  press), an optional time window, and the action fields for your target kind
  (brightness for a light, speed for a fan, open/close for a valve, lock state
  for a lock, setpoint for climate).
- **Delete a rule** — a dropdown lists every rule as its English sentence;
  pick one and it is removed.
- **Delete a source binding** — remove one of the multi-way switches from the
  group.
- **Erase all device programming** — the v0.1.9-safe "wipe everything" (still a
  clean error, never a crash, if the runtime is missing).
- **Recompile** — restarts the wizard so you can rebuild the binding from
  scratch.
- **Just close this dialog** — read-only; nothing is changed.

Adding or deleting a rule writes straight back to the config entry (through
`hass.config_entries.async_update_entry`), so the change is saved and the
dashboard re-renders with your updated rules.

### Layer 2 — The Engineering Ground Truth

The `ShipAnchorOptionsFlow` in `custom_components/ship_anchor/config_flow.py`
has four steps: `dashboard` (init), `add_rule`, `select_rule` (delete a rule),
and `delete_binding` (delete a source). The dashboard renders the entry's
`data` (source(s), optional canopy, target(s), protocol, group, and rules) as
a markdown `description` plus a single **action** `SelectSelector`.

The ELI5 sentence builders are **pure functions** (no I/O), so the unit layer
can assert their exact wording:

- `_GESTURE_ELI5` maps `2x Tap` → "a double tap", `Hold` → "a long press", …
- `_rule_window_phrase(rule)` → `"between 22:00 and 06:00"` or `""` (no
  window); a `None` hour becomes "any hour".
- `_rule_actions_phrase(rule)` → stable-ordering join of the present action
  fields (brightness → colour temp → fade → fan speed → valve → lock →
  setpoint); empty → `"toggle the target (on / off)"`.
- `_rule_eli5(rule)` → one sentence, e.g.
  `"On a triple tap: set brightness to 50% (raw 127), set colour temperature
   to ~2857K (350 mireds) and fade over 2 s."` (see Part 17 for the human
   units)

The `add_rule` schema adapts to the target's kind via `TARGET_CAPABILITIES`
(`_target_capabilities(data)`), the same capability table the wizard uses.
Rule and source indices come back as **strings** from the flow UI and are
validated before being used (`rule_index_out_of_range` /
`source_index_out_of_range`). `erase_all` resolves the compiler from
`hass.data[DOMAIN]["compiler"]` exactly as `__init__.py` does, so a missing
runtime yields `errors["base"] = "no_runtime"` rather than an exception.

The dashboard is **Home Assistant UI-only**: it does not add MCP tools (the MCP
surface stays at seven tools in v0.1.15) and it does not change the wire
format.

---

## Part 17: Human-Friendly Units — Kelvin and Percent, Not Mireds and Bytes

### Layer 1 — The Kitchen-Table Explanation

Ask a person what colour temperature they want and they will answer in
**Kelvin**, the number printed on a bulb box: *2700 K is warm and cosy, 5000 K
is cool daylight.* Ask them what brightness they want and they will answer in a
**percentage**: *half*, *80 %*.

Home Assistant's old form made them type the *machine's* numbers instead: a
brightness byte from 0–255 and a colour temperature in **mireds** (a
153–500 scale that goes the wrong way and makes no one's head stop spinning).

In v0.1.16 SHiP Anchor does the translation for you:

- Type **Kelvin** (2000 K warm → 6500 K cool) and SHiP Anchor converts it to
  mireds.
- Type a **percentage** (0–100 %) and SHiP Anchor converts it to a 0–255 byte.
- The dashboard then *shows you both* — the human value first, the raw value
  in brackets — so a non-engineer can verify the hardware value without
  thinking about mireds or bytes:
   `"set brightness to 50% (raw 127)"`, `"set colour temperature to
   ~2857K (350 mireds)"`.

### Layer 2 — The Engineering Ground Truth

The translation lives in `custom_components/ship_anchor/compiler.py` as four
**pure, unit-tested** functions (plus one bridge):

- `kelvin_to_mireds(k) = round(1000000 / k)`, clamped to 153–500. So 2700 K →
  370 mireds, 5000 K → 200 mireds, 2000 K → 500 mireds.
- `mireds_to_kelvin(m) = round(1000000 / m)` — the inverse, used for display.
- `percent_to_brightness(p) = int(255 * p / 100)` — the classic Home
  Assistant convention, so 50 % → 127, 100 % → 255, 0 % → 0.
- `brightness_to_percent(b)` — the inverse, used for display.
- `normalize_rule_units(rule)` — the bridge: it turns a rule that still
  carries *human* keys (`color_temp_kelvin`, `brightness_percent`) into the
  *protocol* keys (`color_temp_mireds`, `brightness`) before the rule is
  stored.

**The invariant the project protects:** the canonical rule dict on the config
entry and the LEAT rows the C core reads are *always* in protocol units
(`brightness` 0–255, `color_temp_mireds` 153–500). Translation happens only at
the `config_flow.py` boundary — the wizard's `async_step_behavior` and the
options flow's `async_step_add_rule` read the human fields and write the
protocol fields. The C struct `ship_leat_entry_t`, its Layer-1 tests, and the
19-byte wire format are untouched. `normalize_rule_units` is also
*back-compatible*: a rule already in protocol units (e.g. `brightness: 200`,
`color_temp_mireds: 350`) is left exactly as-is, so existing bindings and the
compiler's Layer-1 contract never change.

### Examples

| User types | SHiP Anchor stores | The dashboard shows |
|---|---|---|
| 2700 K | 370 mireds | "~2857K (350 mireds)"* |
| 5000 K | 200 mireds | "~5000K (200 mireds)" |
| 50 % | 127 (byte) | "50% (raw 127)" |
| 100 % | 255 (byte) | "100% (raw 255)" |

\* `350 mireds` reads back as `~2857K` (the inverse of 370 mireds is 2703 K;
the round-trip is lossy by one or two Kelvin because of rounding, which is
exactly the tolerance a human expects of a bulb box).

---

## Part 18: The Architectural Decision Record (ARCH_DECISIONS.md)

### Layer 1 — The Kitchen-Table Explanation

Every big project has a *story*: what it started as, what it tried, what it
threw away, and *why*. For SHiP Anchor that story is written down in
`ARCH_DECISIONS.md`, the **Architectural Decision Record (ADR)**.

Think of it as the project's **diary with dates on every page**. Each entry
says, in one sentence, *what* changed and *why it mattered to a human*, and then
gives the engineering detail for anyone who wants to go deeper. The dates are
the **real calendar dates** the releases were tagged (October 2026), so even if
the git history is ever cleaned up for a public launch, you can still look back
and see the *chronological order* of every decision.

### Layer 2 — The Engineering Ground Truth

`ARCH_DECISIONS.md` is organised in four parts:

1. **Genesis & the Core Law** — what SHiP Anchor is, the "ELI5 First,
   Engineering Second" rule, and the two tiers.
2. **Chronological Milestones** — a date-stamped table for **every** release
   tag `v0.1.0` → `v0.1.16`, grouped into three eras:
    - *The Protocol Core* (v0.1.0–v0.1.9, 2026-10-20 → 2026-10-22): the C
      core, authentication, anti-replay, LEAT, the 8-tool MCP bridge, and the
      v0.1.8 security / PII audit.
    - *Multi-Way & Target-First* (v0.1.10–v0.1.11, 2026-10-22): the shared
      `group_id` bus, the `NoStatusShare` gesture `0x8`, and the target-first
      wizard.
    - *Testing, Bidirectional & Canopy* (v0.1.12–v0.1.16, 2026-10-23 →
      2026-10-24): the Testing Pyramid, the bidirectional wizard, multi-hop
      canopy routing, the visual dashboard, and the human-units translation
      layer.
3. **Key Trade-Offs & Guardrails** — *why* chain-like routing was chosen over
   broadcast-like mesh for physical canopies; *why* C structs stay rigid while
   rich values ride in `frame_params`; the MCP token-ceiling tripwire
   (`< 1,500` tokens); and the six documented dead ends.
4. **The Release Calendar** — a one-line-per-release timeline.

This file is the single source of *architectural* memory. `CLAUDE.md` and
`AGENTS.md` point to it; this guide points to it from the start.

*Guide written for SHiP v0.1 (Project Anchor) — October 2026.*
*"ELI5 First, Engineering Second." — The Anchor Mandate.*
