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

1. **Unit** (`tests/test_compiler_unit.py`, `tests/test_config_flow_unit.py`)
   — pure logic: protocol matching, target kind, capabilities, LEAT row
   mapping. Run: `python3 -m pytest tests/ -v` (needs `pip install pytest`).
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
