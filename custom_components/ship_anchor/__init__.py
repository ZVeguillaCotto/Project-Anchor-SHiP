"""
============================================================================
SHiP v0.1 — custom_components/ship_anchor/__init__.py
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
This file is the "front door" of the SHiP Anchor Home Assistant integration.

When Home Assistant starts up, it opens this file and asks three things:
  1. "Is everything you need available?" (async_setup checks dependencies)
  2. "Set yourself up." (registers services and loads saved binding data)
  3. "Where did you save the last binding list?" (Persistent Store in
      .storage/ship_anchor.bindings — a JSON file HA keeps safe across reboots)

It also registers two Home Assistant "services" that any automation or
dashboard can call:
  - ship_anchor.compile: "Read my automations and push offline bindings
    to my devices right now."
  - ship_anchor.erase:   "Remove all offline bindings I previously pushed."

Think of it like a switchboard operator who (a) loads the office phone book
from the filing cabinet when arriving in the morning, and (b) listens for
calls from staff saying "please connect Room A directly to Room B."

============================================================================

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
HACS-compatible HA integration entry point.
- Uses hass.data[DOMAIN] as the integration runtime namespace.
- Persists binding records via hass.helpers.storage.Store (JSON, version 1).
- Registers zha_service_call, MQTT publish, and WebSocket helpers as
  shared utilities accessible from compiler.py.
============================================================================
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.storage import Store
    from homeassistant.helpers.typing import ConfigType
except ImportError:
    # Running outside Home Assistant (unit tests / benchmarks)
    HomeAssistant = Any   # type: ignore[misc,assignment]
    ServiceCall   = Any   # type: ignore[misc,assignment]
    Store         = Any   # type: ignore[misc,assignment]
    ConfigType    = Any   # type: ignore[misc,assignment]

from .compiler import AnchorCompiler

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ship_anchor"
STORAGE_KEY = "ship_anchor.bindings"
STORAGE_VERSION = 1


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Allow Home Assistant to prepare the integration before its config entry."""
    return True


async def _async_setup_runtime(hass: HomeAssistant) -> None:
    """Load persisted bindings and register the integration's services."""
    if DOMAIN in hass.data:
        return

    _LOGGER.info(
        "SHiP Anchor v0.1 starting up — Smart Home Independent Protocol"
    )

    # Load the persistent binding store from HA's .storage directory
    store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
    stored_data: dict[str, Any] = await store.async_load() or {}

    compiler = AnchorCompiler(hass, store, stored_data)

    hass.data[DOMAIN] = {
        "compiler": compiler,
        "store": store,
        "bindings": stored_data,
    }

    # -----------------------------------------------------------------------
    # Service: ship_anchor.compile
    # ELI5: The user (or a scheduled automation) asks SHiP Anchor to read all
    # Home Assistant automations and push offline backup bindings to devices.
    # -----------------------------------------------------------------------
    async def handle_compile(call: ServiceCall) -> None:
        """Handle the ship_anchor.compile service call."""
        automation_ids: list[str] = call.data.get("automation_ids", [])
        dry_run: bool = call.data.get("dry_run", True)

        _LOGGER.info(
            "SHiP Anchor: compile service called (automation_ids=%s, dry_run=%s)",
            automation_ids,
            dry_run,
        )
        result = await compiler.async_compile(
            automation_ids=automation_ids, dry_run=dry_run
        )
        _LOGGER.info("SHiP Anchor compile result: %s", result)

    # -----------------------------------------------------------------------
    # Service: ship_anchor.erase
    # ELI5: Removes all offline bindings previously pushed, restoring devices
    # to their original hub-dependent state.
    # -----------------------------------------------------------------------
    async def handle_erase(call: ServiceCall) -> None:
        """Handle the ship_anchor.erase service call."""
        _LOGGER.info("SHiP Anchor: erasing all offline bindings")
        await compiler.async_erase_all()

    hass.services.async_register(DOMAIN, "compile", handle_compile)
    hass.services.async_register(DOMAIN, "erase", handle_erase)

    _LOGGER.info("SHiP Anchor: services registered — ready.")


async def async_setup_entry(hass: HomeAssistant, entry: Any) -> bool:
    """Set up services and persistent state for the configured integration."""
    await _async_setup_runtime(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Any) -> bool:
    """Clean up when the integration is unloaded."""
    if DOMAIN in hass.data:
        hass.services.async_remove(DOMAIN, "compile")
        hass.services.async_remove(DOMAIN, "erase")
    hass.data.pop(DOMAIN, None)
    return True
