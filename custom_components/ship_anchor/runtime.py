"""
============================================================================
SHiP Anchor — custom_components/ship_anchor/runtime.py    (v0.1.21)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
SHiP Anchor's "engine" is the little program that does the radio-language
work: it builds the 19-byte packets, checks their secret stamps (SipHash),
matches a tap to a cheat-sheet rule (LEAT), debounces button-mashing, and
decides whether a mesh branch is still alive (heartbeat).

There are two engines that do exactly the same job:

  • The **pure-Python engine** lives right here in this project (ship_protocol
     .py). It needs *nothing* but Python — no compiler, no C headers. This is
     what runs on a locked-down Home Assistant box (HAOS) that has no
     `gcc`.

  • The **native accelerator** is an *optional* compiled version that does the
     same work faster on the hardware. If a device ships one, great — we use
     it. If it does not (the common case), we never even notice: the
     pure-Python engine steps in seamlessly.

This file is the **switchboard** that picks the engine at start-up: it *tries*
to load the native accelerator, and if that fails for any reason it quietly
falls back to pure Python and logs a one-line debug note. Everything the rest
of the integration uses goes through this switchboard, so the two engines are
interchangeable and the project works on a plain computer as well as on a
real device.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
- ``ShipEngine`` is a small record naming which operations a given engine
  provides, all with an identical signature, so callers never branch on the
  engine kind.
- ``PURE_PYTHON_ENGINE`` wires every operation to the ship_protocol.py mirror.
- ``_try_load_native`` attempts to import the optional ``ship_anchor_native``
  module and probes that it exposes the full operation set; any ImportError
  (the normal case — the binary is absent) or any other failure is swallowed
  and we fall back.
- ``select_engine`` returns the native engine when available, otherwise the
  pure-Python one. ``RUNTIME_ENGINE`` is the engine chosen at import time.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .ship_protocol import (
    burst_accept,
    burst_reset,
    decode_verify_19b_frame,
    diagnose_mesh,
    encode_19b_frame,
    heartbeat_check,
    heartbeat_feed,
    heartbeat_rehandshake_needed,
    leat_match,
    siphash24,
)

_LOGGER = logging.getLogger(__name__)

# ELI5: the canonical names every engine must expose. The native accelerator
# and the pure-Python mirror both speak this exact interface, so callers
# (compiler, options flow, handlers, telemetry) never branch on engine kind.
OPERATIONS = (
    "siphash24",
    "encode_19b_frame",
    "decode_verify_19b_frame",
    "leat_match",
    "heartbeat_check",
    "heartbeat_feed",
    "heartbeat_rehandshake_needed",
    "burst_accept",
    "burst_reset",
    "diagnose_mesh",
)

# The pure-Python mirror is the default and always-available engine.
_PURE_PYTHON_OPERATIONS = {
    "siphash24": siphash24,
    "encode_19b_frame": encode_19b_frame,
    "decode_verify_19b_frame": decode_verify_19b_frame,
    "leat_match": leat_match,
    "heartbeat_check": heartbeat_check,
    "heartbeat_feed": heartbeat_feed,
    "heartbeat_rehandshake_needed": heartbeat_rehandshake_needed,
    "burst_accept": burst_accept,
    "burst_reset": burst_reset,
    "diagnose_mesh": diagnose_mesh,
}

# The optional native accelerator module, if a device ships a compiled one.
# It is deliberately a name that is NOT present in the repository, so a
# plain install (HACS, HAOS, a laptop) simply falls back to pure Python.
NATIVE_MODULE_NAME = "ship_anchor_native"


class ShipEngine:
    """ELI5: a named bundle of the radio-language operations.

    Both the native accelerator and the pure-Python mirror are wrapped in a
    ``ShipEngine`` so callers use ``engine.leat_match(...)``, ``engine.
    siphash24(...)`` and friends regardless of which one is running.
    """

    def __init__(self, name: str, operations: dict) -> None:
        self.name = name
        self._ops = operations

    @property
    def is_native(self) -> bool:
        """ELI5: True only when a compiled accelerator is in use."""
        return self.name == "native"

    def __getattr__(self, item: str) -> Callable:
        if item in self._ops:
            return self._ops[item]
        raise AttributeError(item)

    def __contains__(self, item: str) -> bool:
        return item in self._ops


# ELI5: the pure-Python engine, always available, needs no compiler.
PURE_PYTHON_ENGINE = ShipEngine("pure-python", dict(_PURE_PYTHON_OPERATIONS))


def _try_load_native() -> Optional[ShipEngine]:
    """ELI5: try to load the optional native accelerator.

    Returns a ``ShipEngine`` when a compiled module is present and exposes the
    full operation set, otherwise ``None``. Any failure — a missing module
    (the normal case on HACS / HAOS), a bad build, or a mismatched API — is
    swallowed so the integration always ends up on a working engine.
    """
    try:
        module = __import__(NATIVE_MODULE_NAME, fromlist=[NATIVE_MODULE_NAME])
    except ImportError:
        # ELI5: the compiled binary is simply not installed. This is the
        # expected, healthy case on a plain Home Assistant box.
        return None
    except Exception:  # noqa: BLE001
        # ELI5: a corrupt or incompatible build must not crash start-up.
        _LOGGER.debug(
            "SHiP Anchor: native accelerator failed to import; "
            "falling back to the pure-Python engine."
        )
        return None

    # ELI5: a capability probe — the accelerator must speak the full
    # interface, or it is not a real engine and we fall back.
    for op in OPERATIONS:
        if not hasattr(module, op) or not callable(getattr(module, op)):
            _LOGGER.debug(
                "SHiP Anchor: native accelerator is missing %r; "
                "falling back to the pure-Python engine.",
                op,
            )
            return None
    return ShipEngine(
        "native",
        {op: getattr(module, op) for op in OPERATIONS},
    )


def select_engine() -> ShipEngine:
    """ELI5: pick the engine at start-up.

    Prefer the native accelerator when a device ships one; otherwise run on
    the pure-Python engine. The choice is logged at DEBUG so a locked-down
    box (no ``gcc``) never sees a warning, yet an engineer can confirm which
    engine is live.
    """
    native = _try_load_native()
    if native is not None:
        _LOGGER.debug("SHiP Anchor: running on the native accelerator engine.")
        return native
    _LOGGER.debug(
        "SHiP Anchor: no native accelerator present — running on the "
        "pure-Python engine (no compiler required)."
    )
    return PURE_PYTHON_ENGINE


# ELI5: the engine the integration actually uses, chosen once at import time.
# Callers import this single object and use it, so the two engines stay
# interchangeable everywhere.
RUNTIME_ENGINE = select_engine()


def engine_summary() -> dict:
    """ELI5: a small status card describing which engine is live.

    Useful for the dashboard and for tests: it reports the engine name,
    whether a native accelerator is in use, and the full operation list so
    a consumer can confirm every runtime operation is present.
    """
    engine = RUNTIME_ENGINE
    return {
        "name": engine.name,
        "is_native": engine.is_native,
        "operations": list(OPERATIONS),
        "pure_python_fallback": not engine.is_native,
    }


__all__ = [
    "ShipEngine",
    "PURE_PYTHON_ENGINE",
    "RUNTIME_ENGINE",
    "OPERATIONS",
    "NATIVE_MODULE_NAME",
    "select_engine",
    "_try_load_native",
    "engine_summary",
]
