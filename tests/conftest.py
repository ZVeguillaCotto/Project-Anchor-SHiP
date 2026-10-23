"""
============================================================================
SHiP Anchor — tests/conftest.py  (pytest fixture / stub bootstrap)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
This file is a "loading dock". The real Home Assistant and its helper
libraries are only available *inside* a running Home Assistant box. Our tests
run on a plain computer (CI, laptops, this repo), so before we import any SHiP
Anchor module we lay down a set of faithful "dummy" versions of just the few
third-party modules that SHiP Anchor imports at load time:

  • voluptuous            (form schema helpers)
  • homeassistant.core    (HomeAssistant, ServiceCall)
  • homeassistant.helpers (storage.Store, typing, device/entity registry,
                           form selectors)
  • homeassistant.config_entries (ConfigFlow, OptionsFlow base classes)

We do NOT stub Home Assistant's *behaviour* (states, automations, live radios).
That is the whole point of the Testing Pyramid: the base (unit) and middle
(integration) layers exercise pure logic and component interaction against
these dummies, and only the top (system/disaster) layers touch real C code and
the full simulation harness.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
The stubs are installed into ``sys.modules`` *before* ``custom_components``
is first imported, and the repository root is put on ``sys.path`` so the
namespace package resolves exactly like it does for the MCP bridge.
"""

from __future__ import annotations

import os
import sys
import types
from typing import Any

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


# ---------------------------------------------------------------------------
# voluptuous (form schemas)
# ---------------------------------------------------------------------------
def _install_voluptuous() -> None:
    if "voluptuous" in sys.modules:
        return
    mod = types.ModuleType("voluptuous")

    class _Key:  # Required / Optional — used as dict keys in a schema
        def __init__(self, key: Any, default: Any = None) -> None:
            self.key = key
            self.default = default

        def __repr__(self) -> str:  # pragma: no cover - cosmetic
            return "{}({!r})".format(type(self).__name__, self.key)

    class Schema:
        def __init__(self, mapping: Any = None, *args: Any, **kwargs: Any) -> None:
            self._schema = mapping if mapping is not None else {}

    mod.Schema = Schema
    mod.Required = _Key
    mod.Optional = _Key
    mod.Invalid = type("Invalid", (ValueError,), {})
    mod.Coerce = lambda t: t
    sys.modules["voluptuous"] = mod


# ---------------------------------------------------------------------------
# homeassistant.* (just enough to import the integration modules)
# ---------------------------------------------------------------------------
def _module(name: str) -> types.ModuleType:
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
    return sys.modules[name]


def _install_homeassistant() -> None:
    if "homeassistant" in sys.modules and getattr(
        sys.modules["homeassistant"], "__ship_stubbed__", False
    ):
        return

    homeassistant = _module("homeassistant")
    homeassistant.__ship_stubbed__ = True  # type: ignore[attr-defined]

    # homeassistant.core
    core = _module("homeassistant.core")
    core.HomeAssistant = type("HomeAssistant", (), {})
    core.ServiceCall = type("ServiceCall", (), {})

    # homeassistant.helpers (namespace)
    helpers = _module("homeassistant.helpers")
    helpers.__path__ = []  # mark as a package

    # homeassistant.helpers.typing
    typing_mod = _module("homeassistant.helpers.typing")
    typing_mod.ConfigType = dict

    # homeassistant.helpers.storage
    storage = _module("homeassistant.helpers.storage")

    class Store:  # minimal — tests build their own fakes over this
        def __init__(self, hass: Any, version: int, key: str) -> None:
            self.hass = hass
            self.version = version
            self.key = key

    storage.Store = Store

    # homeassistant.helpers.device_registry / entity_registry
    device_registry = _module("homeassistant.helpers.device_registry")
    device_registry.async_get = lambda hass: None
    entity_registry = _module("homeassistant.helpers.entity_registry")
    entity_registry.async_get = lambda hass: None

    # homeassistant.helpers.selector
    selector = _module("homeassistant.helpers.selector")

    class SelectSelectorMode:
        DROPDOWN = "dropdown"
        LIST = "list"
        RADIO = "radio"
        MULTIPLE = "multiple"

    class _SelectorBase:
        def __init__(self, config: Any = None, **kwargs: Any) -> None:
            self.config = config

    class SelectSelector(_SelectorBase):
        pass

    class NumberSelector(_SelectorBase):
        pass

    class SelectSelectorConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.options = kwargs.get("options")
            self.mode = kwargs.get("mode")
            self.placeholder = kwargs.get("placeholder")

    class NumberSelectorConfig:
        def __init__(self, **kwargs: Any) -> None:
            self.min = kwargs.get("min")
            self.max = kwargs.get("max")
            self.step = kwargs.get("step")
            self.unit_of_measurement = kwargs.get("unit_of_measurement")

    selector.SelectSelector = SelectSelector
    selector.SelectSelectorConfig = SelectSelectorConfig
    selector.SelectSelectorMode = SelectSelectorMode
    selector.NumberSelector = NumberSelector
    selector.NumberSelectorConfig = NumberSelectorConfig

    # homeassistant.config_entries
    config_entries = _module("homeassistant.config_entries")

    class _FlowBase:
        def __init_subclass__(cls, domain: Any = None, **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            cls._domain = domain

        def __init__(self) -> None:
            self.hass = None
            self._input = {}

        def async_show_form(self, **kwargs: Any) -> dict:
            return kwargs

        def async_create_entry(self, title: str = "", data: Any = None) -> dict:
            return {"created": True, "title": title, "data": data}

        def async_abort(self, reason: str = "", **kwargs: Any) -> dict:
            return {"aborted": True, "reason": reason}

    class ConfigFlow(_FlowBase):
        pass

    class OptionsFlow(_FlowBase):
        def __init__(self, config_entry: Any = None) -> None:
            super().__init__()
            self._config_entry = config_entry

    config_entries.ConfigFlow = ConfigFlow
    config_entries.OptionsFlow = OptionsFlow


# ---------------------------------------------------------------------------
# Install once, before any test module imports the integration.
# ---------------------------------------------------------------------------
_install_voluptuous()
_install_homeassistant()


def _make_fake_hass(services_calls: list | None = None) -> Any:
    """ELI5: a stand-in ``hass`` object with a no-op service bus.

    Returns a plain object exposing ``.data`` (dict), ``.services.async_call``
    (async, records the call), and ``.states.async_all`` (empty). This is the
    minimum surface the compiler's transport layer touches when it is asked to
    "apply" a binding in a test.
    """

    class _Services:
        async def async_call(self, domain: str, name: str, data: dict) -> None:
            if services_calls is not None:
                services_calls.append((domain, name, data))

    class _States:
        def async_all(self, domain: str | None = None) -> list:
            return []

    class _Config:
        def path(self, *parts: str) -> str:
            return "/tmp/ship_anchor_tests/{}".format("/".join(parts))

    class FakeHass:
        def __init__(self) -> None:
            self.data: dict = {}
            self.services = _Services()
            self.states = _States()
            self.config = _Config()

    return FakeHass()


def _make_fake_store(persisted: dict | None = None) -> Any:
    """ELI5: a stand-in persistent store that simulates
    ``.storage/ship_anchor.bindings`` — it deep-copies on save and can hand
    the data back on load, exactly like Home Assistant's real Store does."""
    import copy
    import json

    class FakeStore:
        def __init__(self) -> None:
            self.saved: dict | None = None

        async def async_load(self) -> dict:
            if persisted is None:
                return {}
            # Round-trip through JSON so we prove the data is serialisable.
            return json.loads(json.dumps(persisted))

        async def async_save(self, data: dict) -> None:
            # Deep copy: a later mutation by the compiler must not corrupt
            # what we "wrote to disk" (mirrors the real Store's copy-on-save).
            self.saved = copy.deepcopy(data)

    return FakeStore()


# Expose the fakes to the test modules via pytest fixtures.
import pytest  # noqa: E402


@pytest.fixture
def fake_hass() -> Any:
    return _make_fake_hass()


@pytest.fixture
def fake_store() -> Any:
    return _make_fake_store()
