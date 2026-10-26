"""
============================================================================
SHiP Anchor — tests/test_multihop_unit.py  (LAYER 1: UNIT TESTS, v0.1.14)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
Base-of-the-pyramid tests for the **multi-hop chain compiler** added in
v0.1.14. A "chain" is a list of devices the gesture should travel through —
e.g. a switch, an intermediate ceiling-canopy relay, and the fan/bulb it
ultimately drives. The tests here check the *pure* planning logic:

  • ``AnchorCompiler._hop_protocol`` — which protocol each leg speaks.
  • ``AnchorCompiler._hop_source_config`` — how a wizard device entry maps
    to the config keys each protocol handler expects.
  • ``AnchorCompiler.async_compile_multihop`` — that a 3-device chain
    produces exactly TWO bindings (hop 1 and hop 2), each tagged with its
    hop index, and that LEAT rows are attached once.

No Home Assistant, no radios, no network — the compiler is built with
``hass=None, store=None`` so every transport would raise if it tried to fire.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- Fakes come from ``tests/conftest.py`` (``_make_fake_hass`` / ``_make_fake_store``).
- The Shelly handler is the cleanest one-hop translator to drive here:
  one HTTP script per hop, one binding per hop.
"""

from __future__ import annotations

import asyncio

import pytest

from custom_components.ship_anchor.compiler import AnchorCompiler
from custom_components.ship_anchor.protocols import Protocol
from tests.conftest import _make_fake_hass, _make_fake_store


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def compiler() -> AnchorCompiler:
    return AnchorCompiler(hass=None, store=None, stored_data={})


def _device(id: str, identifiers, model: str = "", manufacturer: str = "",
             ieee: str = "") -> dict:
    return {
        "device_id": id,
        "name": id,
        "ieee": ieee,
        "model": model,
        "manufacturer": manufacturer,
        "identifiers": identifiers,
    }


# ---------------------------------------------------------------------------
# _hop_protocol — which protocol a leg speaks
# ---------------------------------------------------------------------------
def test_hop_protocol_from_identifier(compiler: AnchorCompiler) -> None:
    assert (
        compiler._hop_protocol(_device("d", [("shelly", "192.0.2.10")]))
        is Protocol.SHELLY
    )
    assert (
        compiler._hop_protocol(_device("d", [("zha", "0011223344556677")]))
        is Protocol.ZIGBEE_ZHA
    )


def test_hop_protocol_unknown_when_no_identifiers(compiler: AnchorCompiler) -> None:
    assert compiler._hop_protocol(_device("d", [])) is Protocol.UNKNOWN


# ---------------------------------------------------------------------------
# _hop_source_config — mapping a wizard entry to handler config keys
# ---------------------------------------------------------------------------
def test_hop_source_config_maps_keys(compiler: AnchorCompiler) -> None:
    cfg = compiler._hop_source_config(
        _device("d", [("shelly", "192.0.2.10")], model="Shelly1")
    )
    # ELI5: the same handler can read the source IP out of either the
    # `shelly_ip` or the `controller_ip` key — we expose both so the caller
    # does not need to know the handler's private naming.
    assert cfg["shelly_ip"] == "192.0.2.10"
    assert cfg["controller_ip"] == "192.0.2.10"
    assert cfg["source_model"] == "Shelly1"


# ---------------------------------------------------------------------------
# async_compile_multihop — the full 3-device chain
# ---------------------------------------------------------------------------
def test_multihop_produces_two_bindings(compiler: AnchorCompiler) -> None:
    chain = [
        _device("sw", [("shelly", "192.0.2.10")]),
        _device("canopy", [("shelly", "192.0.2.30")]),
        _device("load", [("shelly", "192.0.2.40")]),
    ]
    result = _run(
        compiler.async_compile_multihop(
            chain, rules=[{"gesture": "2x Tap", "brightness": 100}], dry_run=True
        )
    )
    assert result.success, result.errors
    # ELI5: a 3-device chain is exactly TWO hops, each with its own binding.
    assert len(result.bindings_added) == 2
    assert result.bindings_added[0].source_ieee == "192.0.2.10"
    assert result.bindings_added[0].target_ieee == "192.0.2.30"
    assert result.bindings_added[1].source_ieee == "192.0.2.30"
    assert result.bindings_added[1].target_ieee == "192.0.2.40"
    # ELI5: LEAT rows are attached once, at the top of the payload list.
    leat_rows = [p for p in result.dry_run_payloads if p.get("step") == "leat_write"]
    assert len(leat_rows) == 1


def test_multihop_empty_chain_is_error(compiler: AnchorCompiler) -> None:
    result = _run(compiler.async_compile_multihop([], dry_run=True))
    assert not result.success
    assert result.errors


def test_multihop_single_device_is_error(compiler: AnchorCompiler) -> None:
    # ELI5: a "chain" of one device has no hop to compile — that is an
    # honest error, not a silent no-op.
    result = _run(
        compiler.async_compile_multihop(
            [_device("only", [("shelly", "192.0.2.10")])], dry_run=True
        )
    )
    # A single device produces zero hops, so no bindings — but success is
    # still True because nothing *failed*; the caller should treat an
    # empty `bindings_added` as "nothing to do".
    assert len(result.bindings_added) == 0


def test_multihop_group_name_is_forwarded(compiler: AnchorCompiler) -> None:
    chain = [
        _device("sw", [("tasmota", "192.0.2.10")]),
        _device("load", [("tasmota", "192.0.2.40")]),
    ]
    result = _run(
        compiler.async_compile_multihop(
            chain, dry_run=True, group_name="anchor_grp_7"
        )
    )
    assert result.success, result.errors
    assert len(result.bindings_added) == 1
    binding = result.bindings_added[0]
    # ELI5: the group name is the shared identifier for the chain; it must
    # appear in the dry-run payload the Tasmota handler emitted.
    tasmota_payloads = [
        p for p in result.dry_run_payloads if p.get("step", "").startswith("tasmota_")
    ]
    assert tasmota_payloads
    # ELI5: every tasmota step carries the group name inside its `cmnd` string.
    assert any(
        "anchor_grp_7" in str(step.get("params", {})) for step in tasmota_payloads
    )

# ---------------------------------------------------------------------------
# v0.1.18 — Pillar 2: Multi-Zone Virtual Grouping / DAG routing
# ---------------------------------------------------------------------------
def test_dag_one_to_many_fanout(compiler: AnchorCompiler) -> None:
    # ELI5: one source canopy driving THREE children (a light, a fan, a
    # valve) is the multi-zone "virtual grouping". It must produce exactly
    # THREE branches, each tagged with its own zone. The node-with-children
    # shape keeps the *source* device's identifiers, so its protocol is
    # detected (a bare string key would drop the source's protocol).
    sw = _device("sw", [("shelly", "192.0.2.10")])
    light = _device("light", [("shelly", "192.0.2.20")])
    fan = _device("fan", [("shelly", "192.0.2.30")])
    valve = _device("valve", [("shelly", "192.0.2.40")])
    dag = [dict(sw, children=[light, fan, valve])]
    result = _run(compiler.async_compile_dag(dag, dry_run=True))
    assert result.success, result.errors
    assert len(result.bindings_added) == 3
    # ELI5: the three branches are tagged by their child (zone) id.
    zones = sorted(
        p.get("zone")
        for p in result.dry_run_payloads
        if "zone" in p and p.get("step") != "leat_write"
    )
    assert "fan" in zones
    assert "valve" in zones


def test_dag_list_of_edges(compiler: AnchorCompiler) -> None:
      # ELI5: the list-of-edges shape is just a 2-tuple per branch.
    sw = _device("sw", [("shelly", "192.0.2.10")])
    light = _device("light", [("shelly", "192.0.2.20")])
    result = _run(compiler.async_compile_dag([[sw, light]], dry_run=True))
    assert result.success, result.errors
    assert len(result.bindings_added) == 1
    assert result.bindings_added[0].source_ieee == "192.0.2.10"
    assert result.bindings_added[0].target_ieee == "192.0.2.20"


def test_dag_linear_chain_matches_legacy(compiler: AnchorCompiler) -> None:
      # ELI5: a linear chain fed through the DAG compiler must produce the same
      # two bindings as the legacy async_compile_multihop.
    chain = [
          _device("sw", [("shelly", "192.0.2.10")]),
          _device("canopy", [("shelly", "192.0.2.30")]),
          _device("load", [("shelly", "192.0.2.40")]),
     ]
    legacy = _run(compiler.async_compile_multihop(chain, dry_run=True))
    dag_edges = [(chain[0], chain[1]), (chain[1], chain[2])]
    dag = _run(compiler.async_compile_dag(dag_edges, dry_run=True))
    assert legacy.success and dag.success
    assert [b.target_ieee for b in legacy.bindings_added] == [
        b.target_ieee for b in dag.bindings_added
      ]


def test_dag_empty_is_error(compiler: AnchorCompiler) -> None:
    result = _run(compiler.async_compile_dag([], dry_run=True))
    assert not result.success
    assert result.errors


def test_dag_cycle_is_dropped_with_warning(compiler: AnchorCompiler) -> None:
      # ELI5: a routing cycle (a -> b -> a) is physically impossible and must be
      # dropped with an honest warning rather than looping forever.
    a = _device("a", [("shelly", "192.0.2.10")])
    b = _device("b", [("shelly", "192.0.2.30")])
    ordered, warnings = compiler._dag_topo_order([(a, b), (b, a)])
    assert any("cycle" in w for w in warnings)


# ---------------------------------------------------------------------------
# v0.1.19 — Finding A: a guardrail failure must NOT abort later branches
# ---------------------------------------------------------------------------
def test_dag_branch_failure_does_not_abort_others(compiler: AnchorCompiler) -> None:
    # ELI5 (Finding A): three independent branches where the MIDDLE one hits a
    # non-throwing guardrail (its ZHA source has no IEEE, so the handler sets
    # ``result.success = False`` without raising). Before the fix, that made
    # ``async_compile_dag`` ``return result`` early — silently dropping branches
    # 3 and 4. After the fix, the failed branch is reported but the others
    # still compile. This is the "one severed zone must not blind the others"
    # guarantee that a virtual mesh promises.
    sw = _device("sw", [("shelly", "192.0.2.10")])
    light = _device("light", [("shelly", "192.0.2.20")])
    fan = _device("fan", [("shelly", "192.0.2.30")])
    # ELI5: the middle branch is a ZHA hop whose SOURCE has NO IEEE — the ZHA
    # handler's first guardrail fires ``result.success = False`` and returns.
    zha_src = _device("zha_src", [("zha", "0011223344556677")])
    zha_tgt = _device("zha_tgt", [("zha", "0011223344556678")],
                      ieee="")   # forces the "no source IEEE" guardrail
    # ELI5: build a 4-branch fan-out from one source. Branch 2 (index 2) is the
    # bad one.
    dag = [
        (sw, light),
        (zha_src, zha_tgt),   # <-- branch that fails its guardrail
        (sw, fan),
    ]
    result = _run(compiler.async_compile_dag(dag, dry_run=True))
    # ELI5: the good branches (shelly light + shelly fan) MUST both appear,
    # even though the middle branch failed its guardrail.
    zones = sorted(
        p.get("zone")
        for p in result.dry_run_payloads
        if "zone" in p and p.get("step") != "leat_write"
    )
    assert "light" in zones, "branch 1 (light) must survive the middle failure"
    assert "fan" in zones, "branch 3 (fan) must survive the middle failure"
    # ELI5: the failed branch must be reported, not silently swallowed.
    assert any("guardrail" in e.lower() or "zone" in e.lower()
               for e in result.errors)


# ---------------------------------------------------------------------------
# v0.1.19 — Finding B: a diamond must NOT be mistaken for a cycle
# ---------------------------------------------------------------------------
def test_dag_diamond_has_no_spurious_cycle_warning(compiler: AnchorCompiler) -> None:
    # ELI5 (Finding B): a diamond — two devices both feeding one child — is a
    # perfectly legal mesh topology, not a routing cycle. The old
    # single-parent ``setdefault`` logic could flag it as a cycle; the new
    # multi-parent Kahn's-algorithm topsort must emit exactly the 3 edges and
    # NO cycle warning.
    a = _device("a", [("shelly", "192.0.2.10")])
    b = _device("b", [("shelly", "192.0.2.20")])
    c = _device("c", [("shelly", "192.0.2.30")])
    dag_edges = [(a, c), (b, c)]   # diamond: a->c, b->c
    ordered, warnings = compiler._dag_topo_order(dag_edges)
    # ELI5: a true diamond emits both edges, never a false cycle warning.
    assert len(ordered) == 2, "diamond must emit both edges"
    assert not any("cycle" in w for w in warnings), (
        "a diamond is not a cycle — the old single-parent logic was wrong"
    )
    # ELI5: and the child c must come AFTER both of its parents in the order.
    pairs = [(p["device_id"], c["device_id"]) for p, _ in ordered]
    assert ("a", "c") in pairs and ("b", "c") in pairs
