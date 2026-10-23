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
