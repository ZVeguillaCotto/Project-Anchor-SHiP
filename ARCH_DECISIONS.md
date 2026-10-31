# SHiP Anchor — Architectural Decision Record (ADR)
### *The plain-English history of why the project is built the way it is.*

> **ELI5 First, Engineering Second.** Every entry below says, in one
> sentence, *what* changed and *why it mattered to a human*, and then gives the
> engineering detail for anyone who wants to go deeper.
>
> **Why this file exists:** git tags can be rewritten or reset for a clean
> public launch, but a human-readable timeline of *when* each decision was made
> and *why* must survive. Every milestone below therefore carries its **real
> calendar completion date** (the git tag date, Eastern Time / US-Eastern,
> `-04:00`), so the chronological progression stays intact even if the commit
> history itself is squashed or rebased.

---

## Part 1 — Project Genesis & The Core Law

### The kitchen-table version

Your smart home's switches and lights normally shout to each other through a
hub (Home Assistant) and a router. When the hub reboots for an update, or the
router hiccups after a power blink, **those shouts get lost** — lights go
dark, valves forget, automations fall silent.

**SHiP Anchor** ("Smart Home Independent Protocol") makes devices remember each
other's *direct* radio addresses, so the most important device-to-device
actions keep working even when the hub, the router, and the cloud are all off.
It also gives a fast 19-byte "radio whisper" path for custom hardware that can
talk directly, below Wi-Fi and below IPv6.

### The Core Law — "ELI5 First, Engineering Second"

This is the non-negotiable project rule that governs *every* file, commit, and
release:

1. **ELI5 First** — explain the idea in plain language a non-engineer can
   understand *before* any jargon. If a word is introduced, it is explained
   first.
2. **Engineering Second** — only after the plain idea is clear do we give the
   rigorous technical detail.
3. **Honesty** — always say plainly what is *simulated* versus *measured on
   real hardware*. Never dress a simulation up as a measurement.

This law is not decorative: it is encoded in the testing pyramid (each layer
states its own plain-language purpose), in the wizard's step titles, and in
the ELI5 rule translators in `config_flow.py`.

### The two tiers

- **Tier 1 — Offline Compiler.** Read Home Assistant automation definitions and
  program *existing* Zigbee, Matter, Z-Wave, Shelly, and Tasmota devices using
  each protocol's native binding mechanism. No new hardware needed.
- **Tier 2 — SHiP Fast-Path.** One authenticated 19-byte 802.15.4 message
  between custom-capable devices (ESP32-C6, ESP32-H2, nRF52840, nRF5340), with
  local action rules (LEAT) and a mesh clock (Mesh-RTC).

---

## Part 2 — Chronological Milestones & Date-Stamped Decisions

The dates below are the **real git tag dates** for `refs/tags/v0.1.N` in this
repository (Eastern Time, `-04:00`). They are the authoritative record and are
preserved here precisely so a git-history reset cannot erase *when* each
decision was made.

### v0.1.0 – v0.1.9 — The Protocol Core, the C Core, and Resilience
*(2026-10-20 → 2026-10-22)*

The first ten releases built the foundation: a faithful C protocol core, the
Home Assistant integration, the seven-tool MCP bridge, and the hardening that
makes it safe to ship.

| Tag | Date | What happened | Why it mattered |
|---|---|---|---|
| **v0.1.0** | 2026-10-20 | Initial public commit: the 19-byte frame format, SipHash-2-4 16-bit authentication, anti-replay counter, LEAT, Mesh-RTC, the C core, and the ESPHome `ship_fastpath` component. | The first honest, working skeleton of the whole idea. |
| **v0.1.1** | 2026-10-21 | Log user-facing milestones at WARNING; surface service-call exceptions. | A user can *see* in the log when something happens, instead of a silent no-op. |
| **v0.1.2** | 2026-10-21 | Fix protocol detection for *entity-trigger* automations. | Entity-based automations (the modern HA style) were being misread; now they map to the right protocol. |
| **v0.1.3** | 2026-10-21 | Support direct-configuration payloads in `ship_anchor.compile`. | Users can compile a binding from a raw config blob, not only from a stored automation. |
| **v0.1.4** | 2026-10-21 | Add the interactive 4-step configuration wizard. | A non-engineer can build a binding from a dropdown flow instead of typing YAML. |
| **v0.1.5** | 2026-10-21 | Make the shared runtime survive multi-entry teardown. | Adding and removing several SHiP Anchor entries no longer kills the shared compiler object. |
| **v0.1.6** | 2026-10-21 | Fix `QuirksInspector` live introspection; correct Inovelli/Hue endpoints. | Vendor quirks (Inovelli, Philips Hue, Aqara, Tuya) read the right endpoints; Hue target endpoint is 11, standard ZCL target is 1. |
| **v0.1.7** | 2026-10-21 | Extend the wizard: multi-select targets, transition (fade) time, and an options flow. | One wizard run can bind *many* targets and add a smooth fade; the options flow lets users reconfigure later. |
| **v0.1.8** | 2026-10-21 | **Security / PII audit:** command-injection hardening, example/mock key masking. | A shipping custom integration must not leak secrets or accept shell-injection. This is the "make it safe for the public" milestone. |
| **v0.1.9** | 2026-10-22 | Surgical fix: `erase_all` no longer aborts on an unsupported or offline binding. | "Wipe everything" must always succeed cleanly, even when one saved device is broken or powered off — a clean error, never a traceback. |

**Engineering ground truth (v0.1.0–v0.1.9):**
- **Protocol isolation:** `_device_protocols_match(a, b)` is the guard that
  decides whether two devices can ever reach each other. It treats ZHA and
  Zigbee2MQTT as the same Zigbee family but **rejects** cross-bus pairs
  (Zigbee↔Z-Wave, Matter↔Shelly, …).
- **Authentication:** normal domains authenticate bytes 9–16 with a 16-bit
  SipHash-2-4 tag in bytes 17–18; the Mesh-RTC domain `0xF` stores its 32-bit
  UTC epoch in bytes 14–17 and an 8-bit tag in byte 18.
- **Anti-replay:** a replay counter is checked on every accepted frame, so a
  captured packet cannot be resent to re-trigger an action.
- **The C core** (`src/ship_core/ship_protocol.c`, `ship_radio_hal.c`) is the
  single source of wire truth; the LEAT rows the Python compiler emits must
  match `ship_leat_entry_t` **field-for-field**.
- **The 7-tool MCP server** (all preview-only, never actuating):
  `ship_inspect_device`, `ship_compile_binding`, `ship_bake_behavior`,
  `ship_list_bindings`, `ship_encode_19b_frame`, `ship_decode_verify_19b_frame`,
  `ship_run_verification_suite`.

---

### v0.1.10 – v0.1.11 — Multi-Way Sync & the Target-First Wizard
*(2026-10-22)*

These releases make *many* switches stay in perfect sync, and they flip the
wizard so it anchors on the *target* first.

| Tag | Date | What happened | Why it mattered |
|---|---|---|---|
| **v0.1.10** | 2026-10-22 | Document multi-way switch synchronisation: a shared `group_id` broadcast bus plus the `NoStatusShare` gesture (`0x8`). | N switches + shared bulbs stay in exact sync on one SHiP Group ID; the `NoStatusShare` gesture suppresses status-echo loops so the bulbs don't "talk back" forever. |
| **v0.1.11** | 2026-10-22 | Target-first wizard: protocol-filtered multi-source selection + per-kind dynamic behaviour, and the LEAT rule compiler. | You start from the thing you want to *control*; the source step lists **only** switches that share that target's protocol, so an impossible pairing can't be built. |

**Engineering ground truth:**
- **Shared `group_id` broadcast bus:** for more than two targets, every target
  is added to the ZCL Groups cluster (`0x0004`, Add command `0x00`) and the
  source switch binds to that *group* — not to N separate unicast targets.
- **`NoStatusShare` gesture (`0x8`):** when a device acts, it must not rebroadcast
  a "status changed" frame, or N synchronized devices enter an echo loop. The
  `0x8` gesture tells peers "I acted, but don't echo."
- **`_infer_protocol` / `_target_kind` / `TARGET_CAPABILITIES`:** the wizard
  derives a protocol from identifier namespaces and model strings, classifies
  the target (light/fan/valve/lock/cover/climate), and shows only the action
  fields that kind supports.
- **`build_leat_table(rules, group_id)`:** compiles each rule into a row that
  matches `ship_leat_entry_t` field-for-field
   `{group_id, domain, gesture, action_type, action_param, hour_start, hour_end,
  enabled}`.

---

### v0.1.12 – v0.1.14 — The Testing Pyramid, Bidirectional Wizard & Multi-Hop Canopy
*(2026-10-23)*

These releases make the project *verifiable* and *extendable*: a formal testing
pyramid, a wizard that can start from either end, and routing through a physical
intermediate module.

| Tag | Date | What happened | Why it mattered |
|---|---|---|---|
| **v0.1.12** | 2026-10-23 | MCP sync: add `ship_bake_behavior` (LEAT) and `ship_list_bindings`; raise the schema token ceiling. | The MCP surface grows to **seven tools** and a token tripwire keeps the schema compact. |
| **v0.1.13** | 2026-10-23 | Industry-standard **Testing Pyramid**: pytest unit + integration suites, run on a plain computer. | A wide, cheap base (pure logic) and a narrow, expensive top (C core + disaster drills) — every change is verified bottom-up before it lands. |
| **v0.1.14** | 2026-10-23 | Bidirectional wizard (source-first *or* target-first) + **chain-like multi-hop canopy routing**. | The wizard anchors from either end; a signal can route `switch → canopy/relay → load` to mirror real ceiling-box wiring. |

**Engineering ground truth:**
- **Testing Pyramid (3 tiers, all runnable on a plain computer):**
  1. *Unit* — pure logic: `_device_protocols_match`, `_target_kind`,
     `_infer_protocol`, `TARGET_CAPABILITIES`, LEAT row mapping, the multi-hop
     chain compiler, and the dashboard ELI5 translators.
  2. *Integration* — wizard steps, the `.storage/ship_anchor.bindings`
     round-trip, and the resilient `erase_all` (HA stubbed by
     `tests/conftest.py`).
  3. *System / disaster* — the C core (`sim/test_ship_core.c`, **10/10**) and
     the simulation + MCP drills (`sim/run_anchor_benchmarks.py`, **6/6**).
  - **The invariant:** the LEAT rows produced at Layer 1 are field-for-field
    identical to the `ship_leat_entry_t` struct asserted by the C suite at
    Layer 3. A mismatch is a **release blocker**.
- **Bidirectional wizard:** `async_step_user` asks direction first; both ends
  funnel through one direction-agnostic `async_step_anchor(side)`, and the
  *other* end is always protocol-filtered by `_device_protocols_match()`.
- **`async_compile_multihop(chain, rules, dry_run, group_name)`:** compiles a
  `chain` of device entries sequentially — hop *i* = `chain[i] → chain[i+1]` —
  through the same per-protocol handlers as a one-hop binding. LEAT rows are
  compiled **once** and attached to every hop; each hop is tagged with
   `hop_index`. A broken hop aborts the chain with an honest, hop-numbered
   error.

---

### v0.1.15 — Visual Options Flow Dashboard & LEAT Inspector
*(2026-10-23)*

The options dialog stops being "a set of hidden buttons" and becomes a dashboard
you can *read and edit* in plain English.

- **What happened:** `ShipAnchorOptionsFlow` was rewritten as a **Visual LEAT &
  Multi-Hop Inspector** — a dashboard at `Settings → Devices & Services → SHiP
  Anchor → Configure`. It renders the whole binding (chain + protocol + group +
  every LEAT rule) as a markdown description and offers an **action** dropdown:
  *Add a rule*, *Delete a rule*, *Delete a source binding*, *Erase all*,
  *Recompile*, or *Just close this dialog*.
- **Why it mattered:** a non-engineer can now inspect and manage a binding
  *inside Home Assistant* without reading JSON, Developer Tools, or the log.
- **Engineering ground truth:**
  - Flow steps: `dashboard` (init) → `add_rule` → `select_rule` →
    `delete_binding`.
  - **Pure, unit-tested ELI5 translators:** `_GESTURE_ELI5`,
    `_rule_window_phrase`, `_rule_actions_phrase`, and `_rule_eli5` turn a raw
    rule dict into one English sentence (e.g. *"On a triple tap: set
    brightness to 120, set colour temperature to 350 mireds and fade over 2 s."*).
  - Persistence via `hass.config_entries.async_update_entry`; `erase_all` keeps
    the v0.1.9 resilience (a missing runtime yields `no_runtime`, not a crash).
  - New test file `tests/test_options_dashboard.py` (Layer 1 + 2).
  - **Honest caveat:** this is a Home Assistant UI feature only; the MCP surface
    stays at **seven tools** and the wire format is unchanged.

---

### v0.1.16 — User-Friendly Unit Abstraction & Translation Layer
*(2026-10-24)*

The wizard and dashboard now speak **human units** (Kelvin and percent) and
silently translate them to the protocol units the hardware needs.

- **What happened:** the wizard (`async_step_behavior`) and the options
   `async_step_add_rule` accept a colour temperature in **Kelvin** (2000 K warm
  → 6500 K cool, like a bulb box) and a **brightness percentage** (0–100 %),
  and translate them to **mireds** (153–500) and a **0–255 byte** at the
   `config_flow.py` boundary.
- **Why it mattered:** a non-engineer never has to think about mireds or raw
  bytes. The dashboard shows *both* — the human value first, the raw value in
  brackets — e.g. *"set brightness to 50% (raw 127)"* and *"set colour
  temperature to ~2857K (350 mireds)"*.
- **Engineering ground truth:**
  - A new pure, unit-tested translation engine in `compiler.py`:
    `kelvin_to_mireds()` (`round(1000000 / K)`, clamped 153–500),
    `mireds_to_kelvin()` (inverse), `percent_to_brightness()`
    (`int(255 * p / 100)`, so 50 % → 127), `brightness_to_percent()` (inverse),
    and `normalize_rule_units()` (the bridge).
  - **The canonical rule dict stays in protocol units** (`brightness` 0–255,
    `color_temp_mireds` 153–500), so the C struct `ship_leat_entry_t` and its
    Layer-1 tests are **untouched** — translation happens only at the
    `config_flow.py` boundary. `normalize_rule_units` is back-compatible, so a
    rule already in protocol units passes through unchanged.
  - `tests/test_compiler_unit.py` gains 12 converter tests; total **74 passed**.

### v0.1.17 — Date-Stamped Architectural Decision Record
*(2026-10-24)*

No new runtime surface — this release **fossilized the project's memory**.

- **What happened:** the chronological, *date-stamped* history that previously
  lived only in the git log was written out as this self-contained ADR
  (`ARCH_DECISIONS.md`), and `CLAUDE.md` / `AGENTS.md` / `README.md` /
  `GUIDE_FOR_HUMANS.md` were pointed at it.
- **Why it mattered:** a clean public launch can reset the git history, which
  would erase *why* each design choice was made. Recording the milestones,
  trade-offs, and the six dead ends in a file keeps the reasoning intact even
  if the commit graph is rewritten.
- **Engineering ground truth:** the wire format, C core, and the 74-test
  pyramid are **unchanged**; only documentation was added and aligned.

### v0.1.18 — Advanced Mesh Resilience, DAG Branching & 8th MCP Diagnostic
*(2026-10-25)*

Three pillars, all of them "is my mesh still healthy, and can it fan out to
many branches at once?".

- **Pillar 1 — Orphaned Mesh Heartbeat:** every hop in a chain remembers when it
  last heard a *genuine* (HMAC-checked, anti-replay-cleared) frame. From the gap
  we derive a health verdict — **UNKNOWN** (never heard anything, needs a
  re-handshake to baseline), **HEALTHY** (within one window), **DEGRADED** (two
  missed windows, warn the user), **ORPHANED** (three or more, branch severed,
  flag it). Implemented in the C core (`ship_heartbeat_feed` /
  `ship_heartbeat_check` / `ship_heartbeat_rehandshake_needed`,
  `ship_hb_health_t` enum, `ship_hb_state_t` struct — 11/11 C tests) and mirrored
  in pure Python (`ship_protocol.py`), wired into the options-flow dashboard as a
  per-hop degradation flag with a **"Re-handshake"** action that re-arms severed
  hops.
- **Pillar 2 — Multi-Zone DAG Branching:** `async_compile_multihop` was refactored
  from a *sequential array* into a **directed acyclic graph** that supports
  one-to-many fan-out — one source reaching many target zones through shared
  relays. The new `async_compile_dag(dag, rules, dry_run, group_name)` accepts
  adjacency-mappings, node-with-children, 2-tuples, or
    `{parent/child}` / `{source/target}` shapes, topologically orders the branches
  (cycles are dropped with an honest warning), and — critically — a broken branch
  is reported but does **not** abort the others ("one severed zone must not blind
  the others"). Each hop is tagged with `hop_index` and a `zone`.
- **Pillar 3 — 8th MCP Diagnostic Tool `ship_diagnose_mesh`:** a *read-only*
  "is my mesh alive?" tool. It ingests a per-hop heartbeat snapshot and returns a
  health verdict per hop, how many branches are orphaned/degraded, whether any
  branch needs a re-handshake, and a modeled multi-hop latency
   (`hop_count × 0.864 ms`). The MCP surface grows from **seven to eight tools**
  and stays under the **1,500** lexical-token tripwire (measured **1,405**).
- **Engineering ground truth:** the 19-byte frame, SipHash, LEAT, and Mesh-RTC
  are **unchanged**. The LEAT row stays rigid. New tests: 6 heartbeat + 5 DAG +
  6 MCP. Drill 6 now asserts the full **eight-tool** surface.

### v0.1.19 — Remediation of Audit Findings A–G
*(independent security/architecture audit → six targeted fixes)*

A de-novo audit of the whole repository surfaced six defects. v0.1.19 fixes the
high- and moderate-severity ones, each backed by a new test.

- **Finding A — DAG branch isolation (High):** `async_compile_dag` did
  `if not result.success: return result`, so a *non-throwing* guardrail failure
  in one branch (e.g. a Z-Wave Long Range block, a ZHA "no IEEE" guardrail)
  aborted **every later branch** — the opposite of "one severed zone must not
  blind the others." Fixed by snapshotting `was_success` *before* each hop and
  only reporting-and-continuing when *that* branch flipped success. New test:
   `test_dag_branch_failure_does_not_abort_others` (branch 2 fails; branches 1
  and 3 still land in `bindings_added`).
- **Finding B — diamond/multi-parent topo-sort (High):** `_dag_topo_order` kept
  only the *first* parent (`parent_of.setdefault`), so a diamond
  (`a→c, b→c`) was mis-ordered and the "cycle remnant" fallback raised a
  **spurious cycle warning**. Fixed with Kahn's algorithm tracking *all* parents
  plus white/gray/black DFS that flags only a *true* back-edge. New test:
   `test_dag_diamond_has_no_spurious_cycle_warning`.
- **Finding C — clock-skew parity (Moderate):** in `ship_heartbeat_check` a
  *backward* clock skew (`now_ms < last_seen_ms`) underflowed the unsigned
  `elapsed` into a huge value → false **ORPHANED**. The Python mirror clamped it.
  Fixed the C core to clamp `elapsed` to 0, restoring C/Python parity. New C
  assertion + `test_clock_skew_backward_clamps_to_healthy`.
- **Findings D & E — IP private-range restriction (Moderate):** every
   target/controller/Shelly IP now passes a strict RFC private-range check
  (`_is_private_or_doc_ip`: 192.168/16, 10/8, 172.16/12, 127/8, plus the IANA
  documentation ranges the tests use as safe fakes); a routable public IP is
  refused (SSRF-style guard). `ShellyHandler.erase` re-validates the stored IP
  at erase time (Finding E). New tests: `test_public_ip_is_rejected`,
   `test_private_and_doc_ips_are_allowed`, `test_malformed_ip_is_rejected`.
- **Finding F — shared LEAT row zone-tagging (Moderate):** LEAT rows are now
  stamped `zone="shared"` / `hop_index=0` up front, so the per-hop untagged
  filter no longer sweeps them into the first branch's zone.
- **Findings G & H — constant naming + erase docs (Low):** `KELVIN_MIN`/
   `KELVIN_MAX` (both wrongly labelled "Kelvin") are replaced by proper
   `MIRED_MIN`/`MIRED_MAX`/`KELVIN_MIN`/`KELVIN_MAX`; Matter and Z-Wave now
  carry explicit `erase` no-ops documenting that their stacks own binding
   lifecycle.
- **Engineering ground truth:** the 19-byte frame, SipHash, LEAT, and Mesh-RTC
  are **unchanged**; the C suite is still **11/11**. Unit/integration suite
  grows from **92 to 98 passed**.

**ELI5:** A ceiling-fan canopy is a *power switch on the way to the light* — a
real wire goes through it. So the natural model is a **chain**:
`switch → canopy → load`. Each hop is just an ordinary one-hop binding to the
*next* device, compiled in order by `async_compile_multihop()`.

- **Decision:** Chain-Like routing is the **shipping path**. A Broadcast-Like
  "Multicast Group Mesh" (one multicast to a shared group, de-duplicated by the
  `NoStatusShare` lock) is **roadmap only**, documented in
  `GUIDE_FOR_HUMANS.md` Part 14 but *not implemented*.
- **Trade-off:** Chain-Like is simple, predictable, and matches physical
  power-gating canopies. Broadcast-Like is more efficient for large, *flat*
  clusters with no relaying middle device — but those are the rare case, and
  shipping the common case first is the right call. The two are complementary,
  not competing.

### v0.1.20 — Visual Telemetry Dashboard & Physical Edge-Case Hardening
*(2026-10-25)*

Three pillars, detailed in §3.4 above.

- **Pillar 1 — Visual Telemetry:** the options-flow dashboard now paints a
  status badge (`🟢/🟡/🔴`) per hop, the degraded/orphaned counts, and a
   modeled multi-hop latency, and offers an interactive **"Re-handshake /
  Heal Mesh"** action. Dashboard and MCP `ship_diagnose_mesh` share one
   `diagnose_mesh()` verdict.
- **Pillar 2 — Burst Debounce:** a `500 ms` / `5`-tap sliding window in the C
  core (new **Test 12**, 11/11 → 12/12) and its Python mirror swallow the 6th+
   rapid tap so a clumsy press can't storm the radio.
- **Pillar 3 — Power-Loss Recovery:** a disaster drill proving that when an
   intermediate relay reconnects, one genuine frame heals the hop from ORPHANED
   to HEALTHY with no user intervention.

### 3.2 Why C structs are kept rigid, and rich values ride in `frame_params`

**ELI5:** The C struct `ship_leat_entry_t` is the thing that gets `memcpy`'d
straight into device flash. If we let the Python side invent new fields, every
device could read garbage.

- **Decision:** the LEAT row is **rigid** — exactly
   `{group_id, domain, gesture, action_type, action_param, hour_start, hour_end,
  enabled}`. A single `action_param` byte holds the primary action.
- **Trade-off:** values that don't fit in one byte (colour temperature in
  mireds is 153–500, fade time) are **not** forced into the struct. They ride
  in an optional `frame_params` annotation (`param_1`/`param_2`/`param_3`) that
  the 19-byte frame encoder consumes. This keeps the struct honest and the
  Layer-1 ⇄ Layer-3 invariant intact, at the cost of a small side-channel for
  the "wide" values.

### 3.3 The token-ceiling tripwire for the MCP surface

**ELI5:** The eight MCP tool schemas are shown to an AI client. If the schemas
get bloated, every tool call costs more tokens (and more money). So we put a
**ceiling** on the total and a test that fails if it is exceeded.

- **Decision:** the MCP schema must stay under a lexical-token tripwire
   (currently **< 1,500**, raised from 1,000 in v0.1.11 when two tools were
  added). Drill 6 in `sim/run_anchor_benchmarks.py` asserts the full eight-tool
  surface and the token count.
- **Trade-off:** a hard ceiling forces compact schemas (short names, compact
  JSON) and guards against accidental bloat. **Honest caveat:** the count is an
  *exact lexical* count of compact JSON tokens; model-specific BPE tokenizers
  (GPT, Claude, …) count in different units, so the number is a guardrail, not a
  promise of identical counts everywhere.

### 3.4 Visual Telemetry & Physical Edge-Case Hardening (v0.1.20)

**ELI5:** v0.1.18 gave the mesh a *heartbeat* — each hop remembers when it last
heard a genuine frame and flags itself DEGRADED or ORPHANED. v0.1.20 makes that
health *visible and actionable* in the dashboard, and hardens two physical edge
cases a real home will hit.

- **Pillar 1 — Visual Telemetry Dashboard** (`config_flow.py` + `ship_protocol.py`):
   the options-flow dashboard now renders a status **badge** next to every hop
   — `🟢 Healthy`, `🟡 Degraded`, `🔴 Orphaned` — plus the degraded/orphaned
   *counts* and a **modeled multi-hop latency** (one 19-byte frame per hop at
    250 kbps ≈ 0.864 ms). A dark branch surfaces an interactive
   **"Re-handshake / Heal Mesh"** action (same `re_handshake` value as v0.1.18,
   richer label) that re-arms every hop with no device touch. The dashboard and
   the MCP `ship_diagnose_mesh` tool now share **one** verdict via the new
    `diagnose_mesh()` in `ship_protocol.py`, so a hop can never look healthy in
  one place and orphaned in the other.
- **Pillar 2 — Burst Debounce** (`ship_protocol.c/.h` + `ship_protocol.py`,
    mirrored as C **Test 12**): a physical edge case — a person can *mash* a
   button far faster than one gesture means. A sliding `500 ms` window
    (`SHIP_BURST_WINDOW_MS`) admits up to `5` taps (`SHIP_BURST_MAX_TAPS`) and
   **swallows the 6th+** so a clumsy press can't storm the radio. A replay can
  never manufacture a fake burst, because only genuine (already-decoded) taps
   reach `ship_burst_accept()`. The Python `burst_accept`/`burst_reset` mirror
   the C core field-for-field; C/Python parity is a release blocker.
- **Pillar 3 — Power-Loss Recovery** (`tests/test_powerloss_recovery_unit.py`):
   a disaster drill proving the promise — when an intermediate relay drops
   offline and **reconnects**, one genuine frame flips the hop from ORPHANED
   back to HEALTHY and the re-handshake flag clears, **without user
  intervention**. The DAG test proves one branch heals independently of others.
- **Engineering ground truth:** the 19-byte frame, SipHash, LEAT, and Mesh-RTC
  are **unchanged**; the C suite grows **11/11 → 12/12** (new Test 12). The
   unit/integration suite grows **98 → 106 passed**. The MCP schema stays under
   its **1,500**-token tripwire (the diagnose tool now *delegates* to
    `diagnose_mesh`, adding no schema surface).

### 3.5 Six documented dead ends — do not repeat

These are the "graves" that cost real debugging time. They live in
`CLAUDE.md` and `AGENTS.md` and are restated here so the ADR is self-contained:

1. **OpenThread raw-link takeover** — never call `otLinkRawSetReceiveDone()` /
   `otLinkRawTransmit()` with IPv6 enabled (can return
   `OT_ERROR_INVALID_STATE`). Use `otLinkSetPcapCallback()` for RX;
   `esp_ieee802154_transmit()` / `nrf_802154_transmit_raw()` for TX.
2. **Per-message channel hopping** — never hop channels on ESP32-C6 / H2 /
   nRF52840; a receiver on the active channel goes deaf. Transmit on the active
   PAN channel and mark the frame with `0xA5` at byte 9.
3. **Archived Matter server** — never target `python-matter-server` (archived
   June 2026 at v8.1.2). Use `matter-js/matterjs-server` at
   `ws://localhost:5580/ws` with the two-step ACL + Binding transaction.
4. **Unsafe Z-Wave direct associations** — never let Z-Wave Long Range nodes
   (`node_id >= 256`) or mismatched S2 classes into direct / multi-channel
   association plans.
5. **Zigbee unicast popcorn** — never bind more than two Zigbee lights with
   separate IEEE unicast bindings; use a ZCL Group and emit vendor preflight
   writes first (Inovelli `0xFC31`/`0x0012=1`, Aqara `0xFCC0`/`0x0200=0`,
   Tuya cluster `0x0006`/`0x8004=0`).
6. **Invalid radio combinations** — never enable `espnow:` on ESP32-H2 (no
   Wi-Fi PHY), or run concurrent `wifi:` + `zigbee: router: true` on
   ESP32-C6 (shared RF-switch instability).

### v0.1.21 — Pure-Python Runtime Fallback & HACS Distribution Hardening

A locked-down Home Assistant box (HAOS, some NAS images) ships **no C
compiler** — no `gcc`, no build tools. v0.1.21 makes that a non-issue:
the pure-Python `ship_protocol.py` engine is the **default runtime**, and
the compiled C accelerator is an *optional speed-up*, not a dependency. If
the native module is missing, corrupt, or was never built, start-up catches
the failure, logs a friendly debug line, and keeps running on pure Python.
Every radio-language operation — framing, SipHash, LEAT lookup, burst
debounce, heartbeat, mesh telemetry — runs identically on both, because they
share one code path and the C suite still proves the bytes match.

### 3.6 The optional-native accelerator seam (v0.1.21)

- **`runtime.py` is the single engine-selection seam.** `select_engine()`
  tries to import a `ship_anchor_native` module; on `ImportError` (the
  normal case — the binary is not shipped in the repo) or on any
  `Exception` from a corrupt build, it returns the pure-Python engine.
  It probes that every operation in `OPERATIONS` is callable before
  trusting a native build, so a half-built accelerator falls back instead of
  crashing. The chosen engine is exposed via `engine_summary()` and stored
  on `hass.data[DOMAIN]["engine"]` so the dashboard and tests can confirm
  which engine is live.
- **`ship_protocol.py` gained `leat_match()`,** a pure-Python mirror of the
  C `ship_leat_match(table, count, frame, rtc)` (group + domain + gesture +
  optional time window). The engine now covers LEAT lookups with no native
  code, so the whole component — compiler, options flow, handlers, telemetry —
  runs on pure Python end to end.
- **`manifest.json` declares the intent in the open:** a `runtime` block —
   `default_engine: pure_python`, `native_accelerator_optional: true`,
   `requires_c_toolchain: false` — and its `requirements` list contains only
  pure-Python wheels (`zigpy`, `websockets`, `aiohttp`), so HACS never forces
   a host-level C build.
- **`tests/test_missing_extension.py` (Layer 2, 11 tests)** simulates the
  three worlds — native *absent*, native *present*, native *corrupt* — and
  proves the right engine wins in each, that the fallback speaks the full
  interface, and that framing / SipHash / LEAT / burst / heartbeat /
  telemetry all run on pure Python with no binary present.

### v0.1.22 — Home Assistant Device Diagnostics

An engineer troubleshooting a physical test bench needs *one file* that says
exactly what SHiP Anchor is doing inside their box: which engine is running,
which bindings are baked in, which LEAT cheat-sheet rows are compiled, and
which mesh branches are alive. v0.1.22 wires up Home Assistant's standard
**"Download diagnostics"** button so that file is one click, and — the one
rule — it is a *photo, not a key*: every cryptographic secret is redacted
in place while the routing structure stays intact.

### 3.7 The diagnostics bundle & its sanitization (v0.1.22)

- **`diagnostics.py` is a new, self-contained module** exposing the two
  Home Assistant entry points `async_get_config_entry_diagnostics` and
  `async_get_device_diagnostics`. HA (2022.4+) auto-detects a function of
   these names at the top level of an integration and shows the download
  button for free — so `__init__.py` simply **re-exports** them; no separate
   platform or registry entry is needed.
- **The bundle is assembled in three reads, then one scrub:** (1) the engine
   status card from `runtime.engine_summary()`; (2) the saved binding records
   from `.storage/ship_anchor.bindings` (via the runtime's compiler) plus a
   **LEAT** summary compiled with the *same* `build_leat_table` the wizard
   uses, so the photo matches the table the hardware holds; (3) the
    `diagnose_mesh` heartbeat verdict shared with the dashboard and the MCP
   diagnostic. A `device` card is added on the device-level entry point.
- **`redact_structure` is a pure, unit-tested sanitizer.** It walks the
   assembled bundle and replaces the value of any key whose *name* names a
   secret (`key`, `secret`, `hmac`, `nonce`, `token`, `password`) with
    `***`, returning a `(sanitized_value, redacted_paths)` pair. Routing
   structure — node IEEE identifiers, group ids, LEAT rows — is **preserved**,
   because that is the debugging payload. The original dict is never mutated.
- **`tests/test_diagnostics_unit.py` (Layer 1 + 2, 10 tests)** proves both
   halves: completeness (every section present: engine, bindings, LEAT,
   heartbeat, sanitization) and privacy (planted `key_hex` / `nonce` secrets
   are `***` in the output while IEEE identifiers survive; the whole bundle is
   JSON-serializable and contains no raw secret). An empty-runtime case proves
   the bundle degrades gracefully.

### v0.1.23 — Enterprise Hardening & Simulation Sprint

A mesh that survives a power outage should also survive a *bad neighbour*: a
device that transmits half a packet, a corrupted secret stamp, or a replayed
old recording. v0.1.23 turns "trust me, it is robust" into "here is the
airbag test that proves it", and closes the last gap between *what the wizard
says a gesture does* and *what the wire would actually do*. Four pillars, all
pure-Python and all unit-tested:

1. **Pillar 1 — the fuzz airbag.** `tests/test_fuzz.py` throws thousands of
   broken frames (truncated, over-long, stale nonces, corrupted SipHash tags,
   garbage headers, random byte-mutation) at the pure-Python codec and asserts
   it never crashes — a bad frame is *either* cleanly refused with a typed
    `ValueError` *or* decoded, never a traceback. A C companion,
    `sim/test_ship_fuzz.c`, throws the same families at the real
     `ship_frame_decode` and asserts clean `SHIP_ERR_*` returns plus
    `SHIP_ERR_NULL_PTR` on null pointers. The C core suite is now **13/13**.
2. **Pillar 2 — entity-registry drift detection.** At start-up,
   `detect_binding_drift()` cross-references every saved binding's
    source/target against Home Assistant's live entity registry. A device that
   was renamed or deleted is flagged **"Entity Missing / Renamed"** in the
   options-flow dashboard with a plain-English nudge to re-point it — so a
    binding that once turned on the kitchen light does not silently go dead
   when the user renames it.
3. **Pillar 3 — the dry-run simulator.** A new **"Simulate a gesture"** action
   in the visual options-flow dashboard. A non-engineer picks a gesture and, in
    pure preview (no radio, no I/O), sees the step-by-step trace across every
   hop, the modeled on-air latency, and a green/red verification card saying
   whether the gesture would actually do something.
4. **Pillar 4 — anti-replay nonce persistence.** The sliding nonce-tracking
   windows that defeat replay attacks are now serialized into
    `.storage/ship_anchor.bindings`, so anti-replay state survives a Home
   Assistant restart or reboot — a recorded frame stays rejected across
    reboots, not just within one session.

### 3.8 The fuzz airbag, drift, simulator & persisted nonces (v0.1.23)

- **`tests/test_fuzz.py` (Layer 1, 13 tests)** fuzzes the pure-Python engine
   with a fixed seed for reproducibility: truncated/over-long frames,
   stale/replayed counters, corrupted SipHash tags, garbage magic bytes, full
   byte-mutation, plus fuzzing of the new `nonce_window_*`, `diagnose_mesh`,
   and `leat_match` surface. Each case catches `ValueError` as a *clean refusal*
   and fails the test on any *other* unhandled exception — the airbag contract.
   The Python total is now **143 passed**.
- **`sim/test_ship_fuzz.c` is the C airbag**, and `test_fuzz_robustness` is
   wired into `sim/test_ship_core.c`'s `main()` so a single native build keeps
   the C core honest. It builds a valid baseline frame, then corrupts it in five
   families (truncation, tag tampering, replay, garbage headers, pure-random
   bytes) and asserts every outcome is a clean `SHIP_ERR_*` — plus three
    `SHIP_ERR_NULL_PTR` checks. The C suite is now **13/13**.
- **Pillar 2 — `compiler.detect_binding_drift()`** is a pure cross-reference
   of `list_bindings()` against `_get_entity_registry()`. Each row is
    `{"binding", "drifted", "missing", "reason"}`; when the registry has not
   loaded yet (`registry is None`) every row is `drifted=False` so start-up
    never blocks on a registry that is still coming up. The reason string uses
    the module-level `DRIFT_LABEL = "Entity Missing / Renamed"`. `__init__.py`
   runs it at start-up (logged at WARNING) and the options-flow dashboard's new
    `_render_drift` shows the panel.
- **Pillar 3 — the simulator** lives in `config_flow.py` as the pure functions
   `simulate_binding`, `render_simulation`, and `build_sim_leat_table`, plus a
    `async_step_simulate` step reached from the dashboard's action dropdown.
   It reuses the *same* `leat_match`, `diagnose_mesh`, and heartbeat math the
   real runtime uses, so the trace is a faithful preview. The verdict priority
   is no-match > needs-heal > replay-rejected > success.
- **Pillar 4 — nonce persistence** is the `nonce_window_*` helpers in
   `ship_protocol.py` (`nonce_window_state`, `nonce_window_accept`,
    `nonce_windows_from_storage`, `nonce_windows_to_storage`,
    `ANTI_REPLAY_WINDOW`, `NONCE_WINDOWS_KEY`). `compiler.py` round-trips the
   windows through `stored_data` on every save (`async_compile`,
    `async_erase_binding`, `async_erase_all` — the last clears them), so the
   anti-replay window survives a restart. The C frame, SipHash, LEAT, and
    Mesh-RTC are **unchanged**; the LEAT row stays rigid.
- **Strings & version.** `strings.json` / `translations/en.json` gain a
   `simulate` options step; `manifest.json` and the MCP `serverInfo` both move
   to `0.1.23`. The MCP surface is still **eight tools**, under the **1,500**
   lexical-token tripwire (measured **1,405**).

### v0.1.24 — Public Launch & Repository Hygiene Sprint

A clean public launch needs three things the private PoC never did: a guard that
catches a broken change *before* it reaches anyone, a front door for new
contributors, and a safe way to report a hole. v0.1.24 delivers all three, with
no change to the wire format — the 19-byte frame, SipHash, LEAT, and Mesh-RTC
stay exactly as they were.

### 3.9 CI, contributor hygiene & safe vulnerability reporting (v0.1.24)

- **`.github/workflows/ci.yml` is the "night watch".** On every `push` and
  `pull_request` to `main`, GitHub spins up a clean Ubuntu runner, sets up
   Python 3.10 (the project floor — `from __future__ import annotations` keeps the
   3.10+ union syntax valid), and runs the whole Testing Pyramid: pytest
   (Layer 1+2), the C core + C fuzz airbag (Layer 3, `13/13`), and the disaster
   drills. The MCP token tripwire is enforced by `run_anchor_benchmarks.py`
  itself (it exits non-zero on a breach), so the workflow does not re-implement
   the check.
- **`pyproject.toml` is the house-keeper's rulebook.** It configures
   [Ruff](https://docs.astral.sh/ruff/) (lint + import sorting), Black, and
   isort for a consistent style; `line-length = 88`, `target-version = py310`,
  and the C/ESPHome trees are excluded from the Python lint. Contributors run
   `ruff check . && ruff format .`.
- **`SECURITY.md` is the safe front door.** It follows the GitHub security-policy
   shape (supported versions, private reporting via Security Advisories, in/out
   of scope) and is written ELI5-first, cross-referencing the existing
    `SECURITY_AUDIT.md` "security diary".
- **`README.md` / `CONTRIBUTING.md` were polished, not rewritten.** The README
   gained an engaging one-breath ELI5 intro, CI/HACS/version badges, a
   HACS-first 3-click install path, and a clean 3-tier Testing-Pyramid section;
  its stale `11/11`/`10/10` C-suite counts were corrected to `13/13`.
   `CONTRIBUTING.md` now documents the full pyramid (with the
    `143 passed` / `13/13` / `6/6` targets), the lint step, and a pointer to
    `SECURITY.md`.
- **Version.** `manifest.json` and the MCP `serverInfo` both move to `0.1.24`;
  `pyproject.toml` records `version = "0.1.24"`. The MCP surface is still
   **eight tools**, under the **1,500** lexical-token tripwire. No new runtime
   surface, so the C frame and the LEAT row stay rigid.

---

### v0.1.25 - Options-Flow Recursion Guard & Regression Lock

A report came in that clicking the gear / "Configure" icon on the SHiP Anchor card could loop the options-flow constructor back into itself and answer with a 500. v0.1.25 investigates that concern and locks the invariant in place - with **no change to the wire format**: the 19-byte frame, SipHash, LEAT, and Mesh-RTC stay exactly as they were.

### 3.10 The gear-icon 500, and why the constructor is already acyclic (v0.1.25)

- **Honest finding: there was no live loop to fix.** In the shipping code, `async_get_options_flow` (a `@classmethod` on `ShipAnchorConfigFlow`) is *defined once and never called from within the integration's own Python* - its one-and-only caller is Home Assistant, which invokes it once per "Configure" click and hands back the resulting flow. `ShipAnchorOptionsFlow.__init__` only copies the entry onto itself (`self._config_entry`, `self._entry`) and calls `super().__init__(config_entry)`. There is no `__new__` / `__init_subclass__` / metaclass re-entry, and the tight-stack reproduction (`sys.setrecursionlimit(80)`) completes the full open path (`async_get_options_flow` -> `async_step_init(None)`) without overflowing. We therefore did **not** fabricate a fix for non-broken code.
- **The guard we *did* add is a comment that names the invariant.** `__init__` now carries an ELI5 note stating the "no gear-icon 500" rule: the constructor must never call `async_get_options_flow` (or any `async_step_*`) on the way back in. It is behaviour-preserving - Python only re-reads a comment - so the existing dashboard is untouched.
- **A regression test locks the guarantee.** New `tests/test_options_flow_recursion.py` (Layer 1 + 2, 5 tests) exercises the exact gear-icon open path, runs it under a deliberately small stack budget so any *future* re-entrancy fails the instant it starts, and asserts the class MRO is acyclic (no class appears twice; the chain ends at `object`). It also keeps `async_get_options_flow` reachable for Home Assistant without the integration calling itself.
- **`ELI5 First, Engineering Second` held.** The report's symptom - a 500 from the gear icon - is explained in plain language *before* the `super()` / MRO detail, and the test docstrings lead with the kitchen-table picture so a future maintainer sees *why* before *how*.
- **Version.** `manifest.json` and the MCP `serverInfo` both move to `0.1.25`. The MCP surface is still **eight tools**, under the **1,500** lexical-token tripwire - no new schema surface, so the drills stay `6/6` and the C core stays `13/13`.

---

### v0.1.26 - Options-Flow Handler Separation

v0.1.25 proved the gear-icon path was already acyclic and locked it with a
comment. v0.1.26 makes that decoupling *structural* instead of *aspirational*:
the dashboard is renamed to a standalone ``ShipAnchorOptionsFlowHandler`` that
inherits Home Assistant's ``OptionsFlow`` directly and shares no constructor
path with the wizard - so the 500 cannot happen, and a test proves it. Again,
**no change to the wire format**: the 19-byte frame, SipHash, LEAT, and
Mesh-RTC are untouched.

### 3.11 A standalone handler class, decoupled from the wizard (v0.1.26)

- **One class per concern.** The dashboard handler is renamed from
   `ShipAnchorOptionsFlow` to `ShipAnchorOptionsFlowHandler` and now inherits
   `config_entries.OptionsFlow` directly; the wizard's `async_get_options_flow`
   factory on `ShipAnchorConfigFlow` simply `return ShipAnchorOptionsFlowHandler(config_entry)`
   and nothing else. The handler's MRO is `ShipAnchorOptionsFlowHandler -> OptionsFlow
   -> object`, so the wizard is *not* in the handler's ancestry.
- **A one-way door, by construction.** The handler keeps no reference back to
   the wizard and its `__init__` only copies the entry and calls `super().__init__`;
   the mangled `__init__` comment from v0.1.25 (a stray continuation line) was
   repaired into a coherent ELI5 "no gear-icon 500" invariant. No `__new__` /
   `__init_subclass__` / metaclass re-entry exists.
- **Tests prove the decoupling.** `tests/test_options_flow_recursion.py` is now
   six tests: the open path returns the handler once, direct construction is
   recursion-free, the tight-stack open path (`sys.setrecursionlimit(80)`) never
   overflows, the dashboard form renders, the MRO is acyclic, and a new
   `test_options_handler_is_decoupled_from_config_flow` asserts the wizard is absent
   from the handler's MRO and that the handler really does inherit `OptionsFlow`.
   `tests/test_options_dashboard.py` and `tests/test_missing_extension.py` were
   renamed to the new handler name.
- **`ELI5 First, Engineering Second` held.** The factory docstring and the new
   test docstrings lead with the plain-language "its own class, two separate
   doors" picture *before* the MRO detail.
- **Version.** `manifest.json`, the MCP `serverInfo`, and `pyproject.toml` all
   move to `0.1.26`. The MCP surface is still **eight tools**, under the **1,500**
   lexical-token tripwire - no new schema surface, so the drills stay `6/6` and
   the C core stays `13/13`.

---

### v0.1.27 - Options-Flow Constructor Signature Fix

A fresh report came in that the gear / "Configure" icon could still answer with a
500, this time blamed on the options-flow *constructor signature*. v0.1.27
investigates that concern against Home Assistant Core's real source and fixes the
one genuine mismatch it finds - again with **no change to the wire format**: the
19-byte frame, SipHash, LEAT, and Mesh-RTC stay exactly as they were.

### 3.12 The gear-icon 500, and the parameterless base (v0.1.27)

- **The finding is genuine this time.** Home Assistant Core's modern
   `config_entries.OptionsFlow` base class defines **no** `__init__`; it inherits
   `FlowHandler.__init__(self, hass)`, which takes **no config-entry argument**.
   The entry instead rides on a `config_entry` *property* that raises until the
   flow is wired to a `hass`. The old habit of writing
   `super().__init__(config_entry)` copied the now-deprecated
   `OptionsFlowWithConfigEntry` pattern, which Core logs as an `ERROR` on
   construction and, in real Core, hands a positional argument into a
   parameterless base - a genuine signature mismatch, not a phantom.
- **The fix is two halves.** `ShipAnchorOptionsFlowHandler.__init__(self,
   config_entry=None)` now calls the modern, parameterless
   `super().__init__()` and *captures* the entry it was handed
   (`self._config_entry`, `self._entry`). A new helper `_resolve_entry()` finds
   the entry whichever way the flow reached us: prefer the entry we were handed,
   and fall back to the base `config_entry` property when we were not. Both
   `_entry_data()` and `_update_entry()` now resolve through it, so the dashboard
   behaves exactly as in v0.1.26 when handed an entry, and degrades to an empty
   card (never a crash) when it is not.
- **The fix is locked with a real test.** New `tests/test_options_flow_signature.py`
   (Layer 1, 5 tests) proves the handler is buildable with **no argument** the way
   modern Core builds it, that a captured entry wins over the property, that the
   resolver falls back to the `config_entry` property, that a not-yet-initialised
   flow returns `None` rather than raising, and that the MRO stays acyclic.
- **`ELI5 First, Engineering Second` held.** The milestone and the new test
   docstrings lead with the plain-language "the constructor now matches Core's
   signature" picture *before* the `super()` / property detail.
- **Version.** `manifest.json`, the MCP `serverInfo`, and `pyproject.toml` all
   move to `0.1.27`. The MCP surface is still **eight tools**, under the **1,500**
   lexical-token tripwire - no new schema surface, so the drills stay `6/6` and
   the C core stays `13/13`.

### v0.1.28 - Options-Flow Dashboard Step Handler Fix

A fresh report came in that the gear / "Configure" icon still answered with a 500,
this time with the exact Core message
**"Handler ShipAnchorOptionsFlowHandler doesn't support step dashboard"**.
v0.1.28 investigates that concern against Home Assistant Core's real source and
fixes the one genuine mismatch it finds - again with **no change to the wire
format**: the 19-byte frame, SipHash, LEAT, and Mesh-RTC stay exactly as they
were.

### 3.13 The gear-icon 500, and why a card needs a matching step (v0.1.28)

- **The finding is genuine this time, and it is a different 500 than v0.1.25 /
   v0.1.26 / v0.1.27.** Those three were about *construction* (an acyclic
   MRO, a parameterless `super()`). This one is about *re-dispatch*. When you
   open a card, Home Assistant Core remembers the card's `step_id` and, when the
   user submits it, Core re-dispatches that form to a method named after the
   card. The Visual LEAT & Multi-Hop dashboard was *rendered from inside*
    `async_step_init` with `step_id="dashboard"`, but there was **no**
    `async_step_dashboard` method. So the *first* paint worked (Core starts at
    `async_step_init`), but the moment the user submitted, Core ran
    `_raise_if_step_does_not_exist(flow, "dashboard")` → `UnknownStep` → a 500
    on the gear icon.
- **The fix makes "dashboard" a real step.** The dashboard body is renamed to its
   own `async_step_dashboard(self, user_input=None)` method (the matching handler
   Core looks for), and `async_step_init` becomes a **thin door** that hands the
   request straight over: `return await self.async_step_dashboard(user_input)`.
   The four internal "return to the dashboard" navigations (add-rule,
    delete-rule, delete-binding, re-handshake) are repointed to
    `async_step_dashboard` too, so nothing loops back through the door. The card
   the user sees is **unchanged** — same dropdown, same plain-English wording.
- **The fix is locked with a real test.** New
    `tests/test_options_flow_dashboard_step.py` (Layer 1 + 2, 4 tests) proves the
   matching handler exists, that the rendered card's `step_id` always has a
   matching `async_step_<id>` on the *same class* (the exact invariant Core's
    `_raise_if_step_does_not_exist` checks), and that the dashboard belongs to the
   options-flow handler, **not** the wizard (the two handlers stay decoupled).
- **`ELI5 First, Engineering Second` held.** The milestone, the new test
   docstrings, and the delegator's docstring lead with the plain-language "a card
   needs a matching handler, just like a room needs a matching door" picture
   *before* the `step_id` / `async_step_` detail.
- **Version.** `manifest.json`, the MCP `serverInfo`, and `pyproject.toml` all
   move to `0.1.28`. The MCP surface is still **eight tools**, under the **1,500**
   lexical-token tripwire - no new schema surface, so the drills stay `6/6` and
   the C core stays `13/13`.

### v0.1.30 - Native UI Selectors & Parameter Tooltips Overhaul

A fresh report asked for the wizard's raw developer text inputs, hand-typed
IEEE addresses, and confusing fields to be replaced by Home Assistant's *native*
selectors (the friendly device / entity / target browsers), with a plain-English
tooltip on **every** field. v0.1.30 adds that opt-in "advanced" route **without
weakening the cross-bus safety guard**, again with **no change to the wire
format**: the 19-byte frame, SipHash, LEAT, and Mesh-RTC stay exactly as they
were.

### 3.14 Native selectors are an *opt-in* side door, not a replacement for the guard (v0.1.30)

- **The naive swap would *weaken* safety.** Home Assistant's
   `DeviceSelector` / `TargetSelector` list **every** device or target the user
   can see — they cannot be filtered by protocol, and `TargetSelector` returns a
   nested `{entity_id, device_id, area_id}` dict the compiler does not expect.
   Dropping the protocol-filtered `SelectSelector` pickers for bare native
   selectors would let a Z-Wave switch be paired to a Zigbee light, the exact
   "cross-bus popcorn" this project has forbidden since v0.1.11. So the native
   selectors are kept **in the code** (import parity with real HA Core, locked
   by the conftest stubs) but used as an **opt-in** route, not the default.
- **The fix adds an "advanced" direction, not a new default.** The entry step's
   dropdown gains a third option, *"Advanced: pick a source or target manually
   (native device / entity / target browser)"*. Picking it opens a new
     `async_step_manual` step (with its own `_manual_schema`) that uses
     `DeviceSelector`, `EntitySelector`, and `TextSelector` for a no-IEEE, no-YAML
   source choice — then **funnels into the very same** protocol-filtered
     `async_step_anchor("target")`, recording the chosen device's protocol as the
   wizard's anchor first. The cross-bus guard is therefore *never bypassed*: the
   native picker supplies the source, but the target picker still excludes any
   device that cannot reach it.
- **Tooltips live in `strings.json` / `en.json`, not in `description=` kwargs.**
   Real HA Core's `SelectSelectorConfig` / `NumberSelectorConfig` use
     `PREVENT_EXTRA`, so passing `description=` to a selector *crashes* the flow
   at build time. The reassuring plain-English guidance is placed in each step's
    `data` section (HA renders `step.data.<field>` as the field label), and a new
    `manual` step plus a `direction` tooltip are added to **both** files, kept
    byte-for-byte equal.
- **The fix is locked with real tests.** Four new integration tests in
     `tests/test_integration.py` prove the "advanced" option routes to
     `async_step_manual`, that a native device / entity choice builds the same
    source entry the wizard does, that the funnel then **filters** the target
   (the Z-Wave lock is excluded for a Zigbee source), and that an empty manual
   submission is a clean self-correcting error — 162 tests pass in total.
- **`ELI5 First, Engineering Second` held.** The new step's title, description,
   and every `data` tooltip lead with the plain-language "pick by name, no IEEE,
   no YAML — and we still check the protocol for you" picture *before* the
    `DeviceSelector` / `async_step_manual` detail.
- **Version.** `manifest.json`, the MCP `serverInfo`, and `pyproject.toml` all
   move to `0.1.30`. The MCP surface is still **eight tools**, under the **1,500**
   lexical-token tripwire - no new schema surface, so the drills stay `6/6` and
   the C core stays `13/13`.

---

## Part 4 — The Release Calendar (one-line timeline)

| Tag | Date (Eastern) | One-line summary |
|---|---|---|
| v0.1.0 | 2026-10-20 | First public commit: 19-byte frame, SipHash, LEAT, Mesh-RTC, C core. |
| v0.1.1 | 2026-10-21 | User-facing WARNING logs; surface service exceptions. |
| v0.1.2 | 2026-10-21 | Fix protocol detection for entity-trigger automations. |
| v0.1.3 | 2026-10-21 | Direct-configuration payloads in `ship_anchor.compile`. |
| v0.1.4 | 2026-10-21 | Interactive 4-step configuration wizard. |
| v0.1.5 | 2026-10-21 | Shared runtime survives multi-entry teardown. |
| v0.1.6 | 2026-10-21 | `QuirksInspector` live introspection; Inovelli/Hue endpoints fixed. |
| v0.1.7 | 2026-10-21 | Wizard: multi-select targets, transition time, options flow. |
| v0.1.8 | 2026-10-21 | Security / PII audit: injection hardening, secret masking. |
| v0.1.9 | 2026-10-22 | `erase_all` no longer aborts on unsupported/offline bindings. |
| v0.1.10 | 2026-10-22 | Multi-way sync: shared `group_id` + `NoStatusShare` gesture `0x8`. |
| v0.1.11 | 2026-10-22 | Target-first wizard + LEAT rule compiler. |
| v0.1.12 | 2026-10-23 | MCP sync to 7 tools + schema token tripwire. |
| v0.1.13 | 2026-10-23 | Industry-standard Testing Pyramid (unit + integration). |
| v0.1.14 | 2026-10-23 | Bidirectional wizard + chain-like multi-hop canopy routing. |
| v0.1.15 | 2026-10-23 | Visual options-flow dashboard & LEAT inspector. |
| v0.1.16 | 2026-10-24 | Human-friendly units (Kelvin + percent) with a translation layer. |
| v0.1.17 | 2026-10-24 | Date-stamped Architectural Decision Record (fossilized project memory). |
| v0.1.18 | 2026-10-25 | Orphaned-mesh heartbeat + DAG branch routing + 8th MCP diagnostic. |
| v0.1.19 | 2026-10-25 | Remediation of audit findings A–G (branch isolation, diamond topo, clock-skew parity, IP SSRF guard). |
| v0.1.20 | 2026-10-25 | Visual telemetry dashboard (status badges + modeled latency) + burst debounce (5 taps / 500 ms) + power-loss recovery drill. |
| v0.1.21 | 2026-10-26 | Pure-Python runtime fallback: optional-native accelerator seam (`runtime.py`), `leat_match` in pure Python, manifest `requires_c_toolchain: false` — HACS/HAOS-safe, no `gcc`. |
| v0.1.22 | 2026-10-26 | Home Assistant device diagnostics (`diagnostics.py`): "Download diagnostics" bundle of engine + bindings + LEAT + heartbeat, with secret redaction (keys/nonces → `***`, routing preserved). |
| v0.1.23 | 2026-10-26 | Enterprise hardening: fuzz airbag (`test_fuzz.py` + C `test_ship_fuzz.c`, 13/13), entity-registry drift detection, dry-run gesture simulator, and persisted anti-replay nonce windows. |
| v0.1.24 | 2026-10-27 | Public launch & hygiene: GitHub Actions CI (pytest + C core + fuzz + drills), `pyproject.toml` (Ruff/Black/isort), `SECURITY.md`, and polished README/CONTRIBUTING. |
| v0.1.25 | 2026-10-27 | Options-flow recursion guard: no live loop found (the constructor is already acyclic); added an ELI5 invariant comment and `tests/test_options_flow_recursion.py` (5 tests) to lock it in. No wire-format change. |
| v0.1.26 | 2026-10-27 | Options-flow handler separation: renamed to the standalone `ShipAnchorOptionsFlowHandler` (inherits `OptionsFlow`, no wizard in its MRO); repaired the `__init__` invariant comment; 6 regression + decoupling tests. No wire-format change. |
| v0.1.27 | 2026-10-27 | Options-flow constructor signature fix: parameterless `super().__init__()` matching Core's `OptionsFlow` base + `_resolve_entry()` fallback; new `tests/test_options_flow_signature.py` (5 tests). No wire-format change. |
| v0.1.28 | 2026-10-27 | Options-flow dashboard step handler: the dashboard card is now a real `async_step_dashboard` step with a matching handler (the "gear icon 500: doesn't support step dashboard" fix); `async_step_init` is a thin door; new `tests/test_options_flow_dashboard_step.py` (4 tests). No wire-format change. |
| v0.1.30 | 2026-10-27 | Native UI selectors & tooltips overhaul: an opt-in "advanced" `async_step_manual` route using native `DeviceSelector`/`EntitySelector`/`TextSelector` (no IEEE, no YAML) that funnels into the *same* protocol-filtered target step so the cross-bus guard is never bypassed; ELI5 tooltips in `strings.json`/`en.json` `data` sections (not `description=` kwargs, which crash Core); 4 new integration tests (162 total). No wire-format change. |

---

*ADR written for SHiP v0.1 (Project Anchor) — October 2026.*
*"ELI5 First, Engineering Second." — The Anchor Mandate.*
