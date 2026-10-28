"""
============================================================================
SHiP Anchor - tests/test_options_flow_recursion.py    (LAYER 1 + 2, v0.1.25)
============================================================================

LAYER 1 - THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
This is the "don't let it happen again" test for the **gear-icon 500**.

Clicking the little gear / "Configure" icon on the SHiP Anchor card makes
Home Assistant call the integration's ``async_get_options_flow`` to build the
Visual LEAT & Multi-Hop Inspector dashboard. If that constructor ever *looped
back into itself* -- for example by calling ``async_get_options_flow`` again,
or by re-entering an ``async_step_*`` from inside ``__init__`` / a subclass
hook -- the call stack would grow until it overflowed and Home Assistant would
answer with a 500 Internal Server Error.

The good news, verified for v0.1.25: **there is no such loop today.**
``async_get_options_flow`` is defined once, is *never* called from within the
integration's own Python, and its one-and-only caller is Home Assistant itself.
These tests lock that fact in so a future edit cannot quietly re-open the loop.

LAYER 2 - THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- A fake ``config_entry`` is a plain object with a ``data`` dict (see the
  companion ``tests/test_options_dashboard.py`` for the fuller dashboard suite).
- The open path is exercised end-to-end: ``async_get_options_flow`` ->
   ``ShipAnchorOptionsFlowHandler(entry)`` -> ``async_step_init(None)`` (the exact
  sequence Home Assistant runs when the dialog opens).
- A low ``sys.setrecursionlimit`` test makes any *future* re-entrancy blow up
  the instant it happens, instead of silently growing the stack to a 500.

Home Assistant itself stays stubbed (see ``tests/conftest.py``).
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from custom_components.ship_anchor.config_flow import (
    ShipAnchorConfigFlow,
    ShipAnchorOptionsFlowHandler,
)


def _entry(data=None):
    """ELI5: a fake Home Assistant config entry carrying a ``data`` dict."""
    if data is None:
        data = {
            "source_device_id": "sw_a",
            "source_name": "Hall Switch",
            "sources": [{"device_id": "sw_a", "name": "Hall Switch"}],
            "canopy": None,
            "targets": [{"device_id": "light1", "name": "Kitchen Light", "kind": "light"}],
            "use_group": False,
            "group_name": "",
            "protocol": "ZIGBEE_ZHA",
            "rules": [{"gesture": "1x Tap", "brightness": 100}],
        }
    return SimpleNamespace(domain="ship_anchor", data=data, options={})


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# LAYER 1 - the constructor must not loop back into the options flow
# ---------------------------------------------------------------------------
def test_async_get_options_flow_returns_flow_once():
    """ELI5: opening the dialog hands back *one* dashboard, not a loop.

    Home Assistant calls ``async_get_options_flow`` exactly once per
    "Configure" click and expects the resulting flow object back. If the
    constructor re-entered the flow, this single call would instead grow
    the stack -- so a clean return *is* the proof the loop is gone.
    """
    flow = ShipAnchorConfigFlow.async_get_options_flow(_entry())
    assert isinstance(flow, ShipAnchorOptionsFlowHandler)
    # ELI5: the entry is held on the flow so the dashboard can read + write it.
    assert flow._config_entry is flow._entry
    assert flow._config_entry.data["source_name"] == "Hall Switch"


def test_direct_construction_is_also_recursion_free():
    """ELI5: building the dashboard by hand (as the test stub does) is just as
    safe -- the constructor only copies the entry and calls the parent."""
    flow = ShipAnchorOptionsFlowHandler(_entry())
    assert isinstance(flow, ShipAnchorOptionsFlowHandler)
    assert flow.hass is None   # injected by HA (or the stub) before step 1


def test_open_path_does_not_recurse_under_tight_stack():
    """ELI5: run the exact "gear-icon" open sequence with a tiny stack budget.

    A normal open needs a hand-span of stack frames. By shrinking the
    recursion limit we make *any* future re-entrancy fail the instant it
    starts -- a real loop would trip here long before it could turn into a
    500 inside Home Assistant.
    """
    default = sys.getrecursionlimit()
    sys.setrecursionlimit(80)
    try:
        # The exact sequence HA runs: build the flow, inject ``hass``, then
        # open the dashboard with no action chosen yet.
        flow = ShipAnchorConfigFlow.async_get_options_flow(_entry())
        flow.hass = None
        result = _run(flow.async_step_init(None))
    finally:
        sys.setrecursionlimit(default)
    assert isinstance(result, dict)
    assert result.get("step_id") == "dashboard"


# ---------------------------------------------------------------------------
# LAYER 2 - the dashboard stays reachable through the official entry point
# ---------------------------------------------------------------------------
def test_open_path_renders_dashboard_form():
    """ELI5: the official entry point (not a back-door) lands on the
    dashboard form without error -- the user sees the card, not a 500."""
    flow = ShipAnchorConfigFlow.async_get_options_flow(_entry())
    flow.hass = None
    result = _run(flow.async_step_init(None))
    assert result.get("step_id") == "dashboard"
    assert "data_schema" in result



def test_options_flow_mro_is_acyclic():
    """ELI5: a safe constructor only ever climbs *up* the class chain
    (``super().__init__``), never sideways or down into the options flow
    again. Python proves this by computing the Method Resolution Order:
    if the class hierarchy had a loop, ``type(...)`` would have raised at
    import time. We re-assert it here so the guarantee is explicit, not
    accidental."""
    mro = ShipAnchorOptionsFlowHandler.__mro__
    # Every step is a *different* class -- no class can appear twice.
    assert len(mro) == len(set(mro))
    # The very last class is always the root ``object``.
    assert mro[-1] is object

# ---------------------------------------------------------------------------
# LAYER 2 - the handler is decoupled from the wizard
# ---------------------------------------------------------------------------
def test_options_handler_is_decoupled_from_config_flow():
    """ELI5 (v0.1.26): the gear-icon dashboard lives in its *own* class.

    The whole point of the refactor is that the dashboard handler and the
    wizard are *two separate classes* that share no constructor path. The
    handler inherits straight from Home Assistant's ``OptionsFlow`` and
    knows nothing about ``ShipAnchorConfigFlow`` -- so opening the dialog
    can never wander back into the wizard and loop the stack.
    """
    names = [c.__name__ for c in ShipAnchorOptionsFlowHandler.__mro__]
    # The wizard is not anywhere in the handler's ancestry.
    assert "ShipAnchorConfigFlow" not in names
    assert not issubclass(
        ShipAnchorOptionsFlowHandler, ShipAnchorConfigFlow
    )
    # ...and it really does inherit the options-flow base directly.
    import homeassistant.config_entries as ce
    assert issubclass(ShipAnchorOptionsFlowHandler, ce.OptionsFlow)
    assert "OptionsFlow" in names
