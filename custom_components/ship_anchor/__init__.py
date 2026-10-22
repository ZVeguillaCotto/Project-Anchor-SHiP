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
  - ship_anchor.compile:   "Read my automations and push offline bindings
    to my devices right now."
  - ship_anchor.erase_all: "Remove all offline bindings I previously pushed."

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

    # ELI5: Home Assistant hides INFO logs from custom components by default,
    # so the user-visible milestones use WARNING to guarantee they appear.
    _LOGGER.warning(
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
        dry_run: bool = call.data.get("dry_run", True)
        # ELI5: there are two ways to ask for a compile.
        #  (a) "Look through ALL my automations" — pass automation_ids (or
        #      nothing at all), and SHiP Anchor scans your automations.yaml.
        #  (b) "Compile THIS exact binding" — pass the fields directly
        #      (protocol, source_ieee, source_model, targets, ...). In that
        #      case we skip the automation scan entirely and hand the payload
        #      straight to the protocol translator. This is how you test a
        #      single device pair without touching your automations.
        direct_config_keys = ("protocol", "source_ieee", "source_node_id")
        has_direct_config = any(call.data.get(key) for key in direct_config_keys)
        raw_ids = call.data.get("automation_ids", [])
        # ELI5: the service UI gives us a single string (possibly
        # comma-separated); a YAML caller may pass a list. Normalise to a list.
        if isinstance(raw_ids, str):
            automation_ids: list[str] = [
                part.strip() for part in raw_ids.split(",") if part.strip()
            ]
        elif isinstance(raw_ids, list):
            automation_ids = [str(item) for item in raw_ids if item]
        else:
            automation_ids = []

        _LOGGER.warning(
            "SHiP Anchor: compile service called (mode=%s, automation_ids=%s, dry_run=%s)",
            "direct-config" if has_direct_config else "automation-scan",
            automation_ids,
            dry_run,
        )
        try:
            if has_direct_config:
                result = await compiler.async_compile_config(
                    dict(call.data), dry_run=dry_run
                )
                # ELI5: a live (non-dry-run) direct compile still has to be
                # written to the persistent store, otherwise erase_all has
                # nothing to undo. async_compile_config only builds the result.
                if not dry_run and result.bindings_added:
                    from dataclasses import asdict

                    compiler._stored_data.setdefault("bindings", []).extend(
                        asdict(binding) for binding in result.bindings_added
                    )
                    await compiler._store.async_save(compiler._stored_data)
            else:
                result = await compiler.async_compile(
                    automation_ids=automation_ids, dry_run=dry_run
                )
        except Exception:
            # ELI5: a failed compile should never be silent — the user must
            # be able to see what went wrong in the log.
            _LOGGER.exception("SHiP Anchor: compile service failed")
            raise

        # ELI5: report every part of the outcome on its own log line so it is
        # easy to scan in the system log, even with a long result object.
        _LOGGER.warning(
            "SHiP Anchor compile result: success=%s, bindings_added=%d, warnings=%d, errors=%d, dry_run_payloads=%d",
            result.success,
            len(result.bindings_added),
            len(result.warnings),
            len(result.errors),
            len(result.dry_run_payloads),
        )
        for binding in result.bindings_added:
            _LOGGER.warning(
                "  binding: %s | %s → %s | clusters=%s group=%s",
                binding.protocol,
                binding.source_ieee,
                binding.target_ieee,
                binding.clusters,
                binding.group_id,
            )
        for warning in result.warnings:
            _LOGGER.warning("  warning: %s", warning)
        for error in result.errors:
            _LOGGER.warning("  error: %s", error)
        for payload in result.dry_run_payloads:
            _LOGGER.warning("  payload: %s", payload)

    # -----------------------------------------------------------------------
    # Service: ship_anchor.erase_all
    # ELI5: Removes all offline bindings previously pushed, restoring devices
    # to their original hub-dependent state.
    # -----------------------------------------------------------------------
    async def handle_erase(call: ServiceCall) -> None:
        """Handle the ship_anchor.erase_all service call."""
        _LOGGER.warning("SHiP Anchor: erasing all offline bindings")
        try:
            await compiler.async_erase_all()
        except Exception:
            # ELI5: the erase must end in a clean log line, never in a
            # re-raised traceback. (Raising inside the except block is the
            # classic source of RecursionError stack overflow — we log the
            # full details with _LOGGER.exception and stop there.)
            _LOGGER.exception("SHiP Anchor: erase_all failed")

    hass.services.async_register(DOMAIN, "compile", handle_compile)
    hass.services.async_register(DOMAIN, "erase_all", handle_erase)

    _LOGGER.warning("SHiP Anchor: services registered — ready.")


async def async_setup_entry(hass: HomeAssistant, entry: Any) -> bool:
    """Set up services and persistent state for the configured integration.

    ELI5: the services and the binding store are SHARED by every config
    entry (there is only one switchboard operator, not one per binding).
    Each entry bumps a reference counter; the runtime is only created on
    the first entry and only torn down when the last one goes away.
    """
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        await _async_setup_runtime(hass)
        runtime = hass.data[DOMAIN]
    runtime["entry_count"] = int(runtime.get("entry_count", 0)) + 1
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Any) -> bool:
    """Clean up when the integration is unloaded.

    ELI5: dropping one binding must NOT shut the shared services down for
    the other bindings. We decrement the reference counter, and only when
    it reaches zero (the last entry is gone) do we remove the services and
    the runtime.
    """
    runtime = hass.data.get(DOMAIN)
    if runtime is None:
        return True
    remaining = max(0, int(runtime.get("entry_count", 0)) - 1)
    if remaining > 0:
        runtime["entry_count"] = remaining
        _LOGGER.debug(
            "SHiP Anchor: entry removed, %d binding(s) still active — keeping services",
            remaining,
        )
        return True
    # Last entry — tear the shared runtime down.
    hass.services.async_remove(DOMAIN, "compile")
    hass.services.async_remove(DOMAIN, "erase_all")
    hass.data.pop(DOMAIN, None)
    _LOGGER.info("SHiP Anchor: last entry removed — services deregistered")
    return True
