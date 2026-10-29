"""
============================================================================
SHiP Anchor — tests/test_options_flow_dashboard_step.py   (LAYER 1 + 2, v0.1.28)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
Regresses the **"does not support step dashboard" 500** that the SHiP Anchor
"gear icon" (Options Flow) used to throw.

The symptom, in plain English: you open the *Configure* dialog and it shows the
Visual LEAT & Multi-Hop dashboard card. The moment you pick an action from the
dropdown and submit, Home Assistant Core tries to hand that form back to a
method named after the card's ``step_id`` -- here, ``dashboard``. Because the
dashboard card was being *shown from inside* ``async_step_init`` but there was
no matching ``async_step_dashboard`` method, Core raised
``UnknownStep("Handler ShipAnchorOptionsFlowHandler doesn't support step
dashboard")`` -- a 500 on the gear icon.

The fix (v0.1.28) makes ``dashboard`` a *real* step: the dashboard body now
lives in ``async_step_dashboard``, and ``async_step_init`` is a thin door that
just hands the request over to it. This file locks that fix so a future
refactor can't silently move the dashboard back into ``async_step_init`` and
lose the matching handler again.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- ``hasattr(handler, "async_step_dashboard")`` is True (the matching handler
   exists).
- the card the dashboard renders carries ``step_id == "dashboard"``, and
   ``async_step_dashboard`` is a *method of the options-flow class itself*
   (not the wizard's) -- so Core's re-dispatch always finds a handler.
- ``async_step_init`` remains a door to the dashboard (back-compat for any
   caller still invoking it by name) and routes to the real step.
All with Home Assistant stubbed out (see ``conftest.py``).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import custom_components.ship_anchor.config_flow as config_flow
from custom_components.ship_anchor.config_flow import (
    ShipAnchorConfigFlow,
    ShipAnchorOptionsFlowHandler,
)
from tests.conftest import _make_fake_hass


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _entry(data: dict) -> SimpleNamespace:
    return SimpleNamespace(domain="ship_anchor", data=data, options={})


def _base_entry_data() -> dict:
    return {
        "source_device_id": "sw_a",
        "source_name": "Hall Switch",
        "source_ieee": "001788fffe00aa11",
        "protocol": "ZIGBEE_ZHA",
        "sources": [{"device_id": "sw_a", "name": "Hall Switch"}],
        "canopy": None,
        "targets": [{"device_id": "light1", "name": "Kitchen Light", "kind": "light"}],
        "use_group": False,
        "group_name": "",
        "rules": [
            {"gesture": "1x Tap", "brightness": 100},
        ],
    }


def _flow_with_entry() -> tuple[ShipAnchorOptionsFlowHandler, SimpleNamespace]:
    entry = _entry(_base_entry_data())
    flow = ShipAnchorOptionsFlowHandler(entry)
    flow.hass = _make_fake_hass()
    return flow, entry


# ---------------------------------------------------------------------------
# LAYER 1 — the matching handler exists (the actual fix)
# ---------------------------------------------------------------------------
def test_async_step_dashboard_exists_on_options_handler() -> None:
    # ELI5 (v0.1.28): the bug was that Core re-dispatched the dashboard form to
    # ``async_step_dashboard`` and found none. Lock that the method now exists.
    assert hasattr(ShipAnchorOptionsFlowHandler, "async_step_dashboard")
    assert callable(getattr(ShipAnchorOptionsFlowHandler, "async_step_dashboard", None))


def test_async_step_init_is_a_door_to_dashboard() -> None:
    # ELI5 (v0.1.28): ``async_step_init`` is still a valid entry point, but it
    # now routes to the real dashboard step.
    assert hasattr(ShipAnchorOptionsFlowHandler, "async_step_init")
    flow, _entry = _flow_with_entry()
    form = _run(flow.async_step_init(None))
    # The card the user sees is unchanged -- it still carries the "dashboard"
    # step id.
    assert form["step_id"] == "dashboard"


def test_dashboard_step_id_matches_a_real_handler() -> None:
    # ELI5 (v0.1.28): the core invariant that prevents the 500 -- whatever
    # ``step_id`` the rendered card carries must have a matching
    # ``async_step_<id>`` method on the SAME class that rendered it. This is
    # exactly the check Core's ``_raise_if_step_does_not_exist`` performs.
    flow, _entry = _flow_with_entry()
    form = _run(flow.async_step_init(None))
    step_id = form["step_id"]
    handler_name = f"async_step_{step_id}"
    assert hasattr(type(flow), handler_name), (
        f"card advertised step_id={step_id!r} but no {handler_name!r} "
        f"on {type(flow).__name__!r} -- this is the 500 bug"
    )


# ---------------------------------------------------------------------------
# LAYER 2 — the dashboard is owned by the options flow, not the wizard
# ---------------------------------------------------------------------------
def test_dashboard_lives_on_options_flow_not_the_wizard() -> None:
    # ELI5 (v0.1.28): the dashboard step belongs to the standalone Options Flow
    # handler. The wizard (ConfigFlow) must NOT gain an ``async_step_dashboard``
    # -- the two handlers stay decoupled.
    assert hasattr(ShipAnchorOptionsFlowHandler, "async_step_dashboard")
    assert not hasattr(ShipAnchorConfigFlow, "async_step_dashboard")
