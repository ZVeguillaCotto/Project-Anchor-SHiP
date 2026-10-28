# -*- coding: utf-8 -*-
# ---------------------------------------------------------------------------
# Layer 1 - the v0.1.27 options-flow CONSTRUCTOR SIGNATURE
# ---------------------------------------------------------------------------
# ELI5 (v0.1.27): why this test exists
#
# Home Assistant Core's modern ``OptionsFlow`` base class takes NO argument in
# its constructor -- the config entry is carried on a ``config_entry`` *property*
# instead. The old habit of writing ``super().__init__(config_entry)`` copied
# the now-deprecated ``OptionsFlowWithConfigEntry`` pattern, which Core logs as
# an ERROR and which, in real Core, hands a positional argument into a
# parameterless base and corrupts the flow -- the 500 on the gear icon.
#
# This suite locks in the two halves of the fix:
#    * the handler's constructor is parameterless-compatible (you can build it
#     with NO argument, exactly the way modern Core builds a flow);
#    * it still knows which config entry it is about, via the ``_resolve_entry``
#     fallback -- the entry it was handed first, else the base ``config_entry``
#     property.
# ---------------------------------------------------------------------------
from types import SimpleNamespace

import custom_components.ship_anchor.config_flow as cf


def _entry(data=None):
    """ELI5: a stand-in config entry carrying a data dict."""
    data = data or {"source_name": "Hall Switch", "target_name": "Kitchen Light"}
    return SimpleNamespace(domain="ship_anchor", data=data, options={})


# ---------------------------------------------------------------------------
# The handler can be built with NO argument, like modern Core builds a flow
# ---------------------------------------------------------------------------
def test_options_handler_is_parameterless():
    """ELI5: the constructor accepts zero arguments.

    Real Home Assistant Core builds the dashboard handler with no entry argument
     (the entry arrives later on a property). So the handler must be buildable
    the same way -- this is the exact signature mismatch that 500'd the gear icon.
    """
    flow = cf.ShipAnchorOptionsFlowHandler()
    assert flow._config_entry is None
    assert flow._resolve_entry() is None
    assert flow._entry_data() == {}


def test_captured_entry_takes_priority_over_property():
    """ELI5: when we *were* handed an entry, that one wins."""
    entry = _entry()
    flow = cf.ShipAnchorOptionsFlowHandler(entry)
    assert flow._resolve_entry() is entry
    assert flow._entry_data() == entry.data


def test_resolver_falls_back_to_config_entry_property():
    """ELI5: with no captured entry, resolve via the base ``config_entry``."""
    flow = cf.ShipAnchorOptionsFlowHandler()
    entry = _entry()
    flow.config_entry = entry
    assert flow._resolve_entry() is entry
    assert flow._entry_data() == entry.data


def test_uninitialised_flow_returns_none_not_raises():
    """ELI5: a not-yet-initialised flow shows an empty card, not a crash."""
    flow = cf.ShipAnchorOptionsFlowHandler()
    assert not hasattr(flow, "config_entry")
    assert flow._resolve_entry() is None
    assert flow._entry_data() == {}


def test_mro_is_acyclic_with_parameterless_base():
    """ELI5: climbing the class chain still never loops."""
    mro = cf.ShipAnchorOptionsFlowHandler.__mro__
    assert len(mro) == len(set(mro))
    assert mro[-1] is object
