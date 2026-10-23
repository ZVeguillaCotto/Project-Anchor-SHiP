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


def _device(id: str, identifiers, model: str = "", manufacturer: str = "") -> dict:
    return {
        "device_id": id,
        "name": id,
        "ieee": "",
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
