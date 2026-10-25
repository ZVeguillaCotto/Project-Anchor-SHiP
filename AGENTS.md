# Agent Onboarding

## Layer 1 — The Kitchen-Table Explanation (ELI5)

This repository has one source of project memory so coding assistants do not
have to rediscover protocol rules and previous dead ends. Start with
[CLAUDE.md](CLAUDE.md): it explains Project Anchor in plain language first,
then gives the engineering details, architecture map, and verification steps.

For the **date-stamped architectural history** — every milestone from v0.1.0 to
v0.1.16 with its real git tag date, the design choices, and the trade-offs —
read [ARCH_DECISIONS.md](ARCH_DECISIONS.md). It preserves the chronological
progression even if the git history is ever reset for a clean public launch.

## Layer 2 — The Under-the-Hood Instructions

Read [CLAUDE.md](CLAUDE.md) before changing code. Follow its six documented
dead ends, keep dry-runs non-actuating, and preserve the **ELI5 First,
Engineering Second** rule in every file you add or modify. Validate with the
linked C and Python commands after making changes.

## Testing Pyramid (v0.1.13) — run before every change lands

SHiP Anchor is verified bottom-up. All three layers run on a plain computer
(no HA box, no radios) — the top two just get slower and more realistic:

1. **Unit** (`tests/test_compiler_unit.py`, `tests/test_config_flow_unit.py`, `tests/test_multihop_unit.py`, `tests/test_options_dashboard.py`)
   — pure logic: protocol matching, target kind, capabilities, LEAT row
   mapping, and the multi-hop chain compiler.
   Run: `python3 -m pytest tests/ -v` (needs `pip install pytest`).
2. **Integration** (`tests/test_integration.py`) — wizard steps, the
   `.storage/ship_anchor.bindings` round-trip, and resilient `erase_all`.
   Home Assistant is stubbed by `tests/conftest.py`.
3. **System / disaster** — the C core (`sim/test_ship_core.c`, target `11/11`)
   and the simulation + MCP drills (`sim/run_anchor_benchmarks.py`, target
   `6/6`). These touch the real 19-byte encoder, SipHash, LEAT, Mesh-RTC, and
   radio HAL.

**Invariant to protect:** the LEAT rows produced at Layer 1 are field-for-field
identical to the `ship_leat_entry_t` struct asserted by the C suite at Layer 3.
A mismatch is a release blocker.

## v0.1.11 — New Architecture (read before touching the wizard or MCP)

- **Target-first wizard:** the config flow is now `target → source → behavior
  → finish`. The target's protocol anchors the flow and the source step
  lists only switches that share that protocol — see
  `_device_protocols_match()` in `custom_components/ship_anchor/config_flow.py`.
  Never list a source whose protocol cannot reach the selected target.
- **LEAT rule compiler:** gesture + time-window rules compile into rows that
  match the C struct `ship_leat_entry_t` field-for-field
  (`AnchorCompiler.build_leat_table`). Keep the LEAT row to the single
  `action_param` byte; put wider values (mireds, fade time) in the optional
  `frame_params` annotation, not the struct.
- **MCP surface is eight tools**, all preview-only: inspect, compile,
  `ship_bake_behavior` (LEAT), `ship_list_bindings`, encode, decode, the
  verification suite, and the v0.1.18 `ship_diagnose_mesh` mesh diagnostic.
  Any new tool must keep the schema under the lexical token ceiling asserted by
  Drill 6 (see `sim/run_anchor_benchmarks.py`).

## v0.1.14 — Bidirectional Wizard & Multi-Hop Canopy (read before the wizard or compiler)

- **Wizard is now bidirectional:** `async_step_user` asks direction first
  (source-first OR target-first). Both ends funnel through one
  direction-agnostic `async_step_anchor(side)`; the *other* end is
  protocol-filtered by `_device_protocols_match()` in either direction.
  `async_step_source` / `async_step_target` remain as back-compat aliases.
- **Optional canopy step** (`async_step_canopy`): an intermediate relay /
  ceiling-canopy module between source and target. Empty = direct one-hop
  binding (v0.1.13 behaviour). Non-empty = a two-hop chain.
- **Multi-hop compiler:** `AnchorCompiler.async_compile_multihop(chain, rules,
  dry_run, group_name)` compiles a `chain` of wizard device entries
  sequentially (hop *i* = `chain[i] → chain[i+1]`) through the same
  per-protocol handlers as a one-hop binding. LEAT rows are compiled once and
  attached to every hop; each hop is tagged with `hop_index`.
- **Chain-Like vs. Broadcast-Like:** Chain-Like (physical power-gating ceiling
  canopies) is the shipping path. Broadcast-Like (Multicast Group Mesh) is
  roadmap only — documented in GUIDE_FOR_HUMANS.md Part 14, not implemented.
- **Handler compatibility:** the Shelly handler accepts a `next_hop_ip`
  fallback and the Tasmota handler accepts a single `target_ip`, so a one-hop
  handler can also drive a chain leg.

## v0.1.15 — Visual Options Flow Dashboard (read before the options flow)

The Options Flow is now a **Visual LEAT & Multi-Hop Inspector** — a dashboard
in `Settings → Devices & Services → SHiP Anchor → Configure` that lets a
non-engineer *read and edit* a binding in plain English.

- **Flow steps** (`ShipAnchorOptionsFlow` in `custom_components/ship_anchor/config_flow.py`):
  `dashboard` (init) → `add_rule` → `select_rule` → `delete_binding`.
  The `dashboard` step renders the whole binding (chain + protocol + group +
  rules) as a markdown `description` and offers an **action** dropdown:
  *Add a rule*, *Delete a rule*, *Delete a source binding*, *Erase all
  device programming*, *Recompile (restart the wizard)*, or *Just close this
  dialog*.
- **ELI5 rule translators (pure, unit-testable):** `_GESTURE_ELI5`,
  `_rule_window_phrase(rule)`, `_rule_actions_phrase(rule)`, and
  `_rule_eli5(rule)` turn a raw LEAT rule dict into one English sentence.
  They are stable-ordered and never touch I/O, so the unit layer asserts the
  exact wording (e.g. `"On a triple tap: set brightness to 120, set colour
  temperature to 350 mireds and fade over 2 s."`).
- **Read + write the entry:** the dashboard reads `config_entry.data`;
  `add_rule` / `select_rule` / `delete_binding` mutate `data["rules"]` or
  `data["sources"]` and persist via `hass.config_entries.async_update_entry`.
  `erase_all` still calls `AnchorCompiler.async_erase_all()` (v0.1.9
  resilience kept); `recompile` aborts with `restart_wizard`.
- **Strings:** `strings.json` `options` section now has `dashboard`,
  `add_rule`, `select_rule`, `delete_binding` steps plus their error keys.
  `translations/en.json` is kept in sync with `strings.json`.
- **Tests:** `tests/test_options_dashboard.py` (Layer 1 + 2) covers the ELI5
  rule translation, the dashboard chain rendering (including the two-hop
  canopy chain), and the add-rule / delete-rule / delete-binding / erase-all
  round trips against the fake `config_entries.async_update_entry`.

## v0.1.16 — User-Friendly Unit Abstraction & Translation Layer (read before the wizard or compiler)

The wizard and the options dashboard now accept **human units** and translate
them to protocol units:

- **Kelvin** for colour temperature (2000 K warm → 6500 K cool) instead of
  raw mireds; **a percentage (0–100 %)** for brightness instead of a raw
   0–255 byte.
- **The translation engine lives in `compiler.py`** as pure, unit-tested
   functions: `kelvin_to_mireds()` (`round(1000000 / K)`, clamped 153–500),
   `mireds_to_kelvin()` (inverse), `percent_to_brightness()` (classic
   `int(255 * percent / 100)` convention, so 50 % → 127), `brightness_to_percent()`,
   and `normalize_rule_units()` (the bridge that turns a human rule into
  protocol units before it is stored).
- **The canonical rule dict stays in protocol units** (`brightness` 0–255,
   `color_temp_mireds` 153–500) so the C struct `ship_leat_entry_t` and the
  Layer-1 compiler tests never change — translation happens at the
   `config_flow.py` boundary (wizard `async_step_behavior` + options `async_step_add_rule`).
- **The ELI5 renderer** (`_rule_actions_phrase`) shows the human value first
   with the raw value in brackets, e.g. `"set brightness to 50% (raw 127)"`
   and `"set colour temperature to ~2857K (350 mireds)"`.
- **Tests:** `tests/test_compiler_unit.py` gains 12 converter tests (2700 K →
   370 mireds, 50 % → 127, clamping, inverses, back-compat); the dashboard and
   integration tests feed human units and assert the translated protocol
   values. Total **74 passed**.

## v0.1.18 — Advanced Mesh Resilience, DAG Branching & 8th MCP Diagnostic (read before the compiler or MCP)

- **Pillar 1 — Orphaned Mesh Heartbeat** (`ship_protocol.h/.c` +
  `custom_components/ship_anchor/ship_protocol.py`): every hop remembers when it
  last heard a *genuine* frame; the gap yields a health verdict —
  `ship_hb_health_t` = UNKNOWN / HEALTHY / DEGRADED / ORPHANED
   (`ship_heartbeat_feed` / `ship_heartbeat_check` /
  `ship_heartbeat_rehandshake_needed`). The options-flow dashboard renders a
  per-hop degradation flag with a **"Re-handshake"** action that heals severed
  hops.
- **Pillar 2 — Multi-Zone DAG Branching** (`compiler.py`):
   `async_compile_multihop` now delegates to `async_compile_dag(dag, rules,
  dry_run, group_name)`, which accepts adjacency-mappings, node-with-children,
   2-tuples, or `{parent/child}` / `{source/target}` shapes, topologically
  orders branches (cycles dropped with a warning), tags each hop with
   `hop_index` + `zone`, and — "one severed zone must not blind the others" — a
  broken branch is reported but does **not** abort the rest.
- **Pillar 3 — 8th MCP tool `ship_diagnose_mesh`** (`mcp/ship_anchor_mcp.py`):
  a read-only "is my mesh alive?" diagnostic — per-hop verdict,
  orphaned/degraded counts, a re-handshake flag, and a modeled latency
   (`hop_count × 0.864 ms`). The surface is now **eight tools**, under the
   **1,500**-token tripwire (measured **1,405**).
- **Tests:** `tests/test_heartbeat_unit.py` (6), 5 DAG tests in
   `tests/test_multihop_unit.py`, `tests/test_mcp_unit.py` (6). Drill 6 asserts
  the full eight-tool surface. The 19-byte frame, SipHash, LEAT, and Mesh-RTC
  are **unchanged**; the C suite is now **11/11**.
