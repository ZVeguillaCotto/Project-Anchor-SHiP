# Agent Onboarding

## Layer 1 — The Kitchen-Table Explanation (ELI5)

This repository has one source of project memory so coding assistants do not
have to rediscover protocol rules and previous dead ends. Start with
[CLAUDE.md](CLAUDE.md): it explains Project Anchor in plain language first,
then gives the engineering details, architecture map, and verification steps.

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
3. **System / disaster** — the C core (`sim/test_ship_core.c`, target `10/10`)
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
- **MCP surface is seven tools**, all preview-only: inspect, compile,
  `ship_bake_behavior` (LEAT), `ship_list_bindings`, encode, decode, and the
  verification suite. Any new tool must keep the schema under the lexical
  token ceiling asserted by Drill 6 (see `sim/run_anchor_benchmarks.py`).

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
