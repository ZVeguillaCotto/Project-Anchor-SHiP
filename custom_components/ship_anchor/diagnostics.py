"""
============================================================================
SHiP Anchor — custom_components/ship_anchor/diagnostics.py   (v0.1.22)
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
This file is the "print a photo of the wiring" button.

When something on a real test bench misbehaves, an engineer needs to hand
the maintainer *one* file that says exactly what SHiP Anchor is doing inside
their Home Assistant box: which engine is running, which device bindings are
baked in, which LEAT (tap → action) cheat-sheet rows are compiled, and which
mesh branches are healthy or orphaned. Home Assistant already shows a
"Download diagnostics" button on every integration page; this module is the
thing that fills in what that button downloads for SHiP Anchor.

The one rule: a diagnostics file is a *photo*, not a *key*. It must reveal
the routing structure (which device talks to which, which tap does what, which
branch is alive) but must NEVER reveal the cryptographic material that guards
the 19-byte frames — the 16-byte SipHash **key**, any **HMAC/secret**, and
per-frame **nonces**. Those are redacted in place (replaced with ``***``) so
the file is safe to post in an issue or email, while the *shape* of the data
stays intact for debugging.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
Home Assistant (2022.4+) auto-detects ``async_get_device_diagnostics`` and
``async_get_config_entry_diagnostics`` at the top level of an integration and
exposes the "Download diagnostics" button for free — no separate platform to
register. This module therefore provides both functions and the integration's
``__init__.py`` re-exports them.

The bundle is assembled in three reads, then one scrub:

   1. **Engine** — ``runtime.engine_summary()`` (which engine, native or
      pure-Python, and the full operation list).
   2. **Bindings** — the saved records in ``.storage/ship_anchor.bindings``
      (via the runtime's compiler), plus a compiled **LEAT** summary built
      with the *same* compiler the wizard uses, so the photo matches the
      table the hardware actually holds.
   3. **Heartbeat** — ``diagnose_mesh`` over the entry's hop snapshots, the
      one "is my mesh alive?" verdict shared with the dashboard and the MCP
      diagnostic.

``redact_structure`` then walks the assembled bundle and replaces every value
whose *key name* names a secret (key, secret, hmac, nonce, token, password)
with ``***`` — preserving structure and node IEEE identifiers, which ARE the
debugging payload.
"""

from __future__ import annotations

import logging
from typing import Any

from .runtime import engine_summary

_LOGGER = logging.getLogger(__name__)

# ELI5: the integration version this diagnostics module was written for.
# Kept in sync with manifest.json / mcp/ship_anchor_mcp.py at release time.
DIAGNOSTICS_VERSION = "0.1.22"

# ---------------------------------------------------------------------------
# Privacy & sanitization
# ---------------------------------------------------------------------------
# ELI5: the *names* of the things we never print in a diagnostics file.
# Anything whose key name contains one of these fragments is redacted.
# We deliberately do NOT list "ieee" / "group_id" / "counter" — those are the
# routing structure the engineer needs to see, so they are preserved.
_SENSITIVE_KEY_FRAGMENTS = (
    "key",
    "secret",
    "hmac",
    "nonce",
    "token",
    "password",
    "passwd",
    "auth_key",
)

# The replacement we drop in place of a secret, so structure is preserved.
_REDACTED = "***"


def _key_is_sensitive(key: Any) -> bool:
    """ELI5: does this field *name* look like a secret we must not print?"""
    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS)


def redact_structure(
    value: Any, path: str = "root"
) -> tuple[Any, list[str]]:
    """ELI5: make a deep copy of ``value`` with every secret *redacted in place*.

    Returns a ``(sanitized_value, redacted_paths)`` pair. ``redacted_paths`` is
    the list of dotted paths that were scrubbed, so the caller can report
    *what* was hidden. Structure is always preserved: a dict key stays a dict
    key, a list stays the same length — only the *value* behind a sensitive
    key name is replaced with ``***``.
    """
    redacted: list[str] = []

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _key_is_sensitive(key):
                out[key] = _REDACTED
                redacted.append(path + "." + str(key))
                continue
            child_path = "{}.{}".format(path, key)
            out[key], child = redact_structure(item, child_path)
            redacted.extend(child)
        return out, redacted

    if isinstance(value, (list, tuple)):
        out_seq = []
        for index, item in enumerate(value):
            child_path = "{}[{}]".format(path, index)
            redacted_item, child = redact_structure(item, child_path)
            out_seq.append(redacted_item)
            redacted.extend(child)
        if isinstance(value, tuple):
            return tuple(out_seq), redacted
        return out_seq, redacted

    # Primitives (str / int / float / bool / None) are safe to copy as-is.
    return value, redacted


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------
def _collect_bindings(runtime: dict[str, Any]) -> list[dict[str, Any]]:
    """ELI5: read every saved binding record, in a shape safe to print."""
    compiler = runtime.get("compiler")
    if compiler is not None and hasattr(compiler, "list_bindings"):
        records = []
        for binding in compiler.list_bindings():
            try:
                from dataclasses import asdict

                records.append(asdict(binding))
            except Exception:  # noqa: BLE001
                _LOGGER.debug("diagnostics: skipping non-serialisable binding")
        return records
    # Fall back to the raw stored list when no compiler is available.
    return list(runtime.get("bindings", []))


def _collect_leat(runtime: dict[str, Any], entry: Any) -> dict[str, Any]:
    """ELI5: compile the entry's tap → action rules into LEAT rows.

    Uses the *same* compiler the wizard uses, so the photo matches the table
    the hardware actually holds. When no compiler is present (a bare import in
    a unit test), we still surface the raw rules so the engineer can see them.
     """
    data = getattr(entry, "data", None) or {}
    rules = data.get("rules") or []
    group_id = int(data.get("group_id", 0) or 0)

    compiler = runtime.get("compiler")
    rows: list[dict[str, Any]] = []
    compiled_with = "compiler"
    if compiler is not None and hasattr(compiler, "build_leat_table"):
        try:
            rows = compiler.build_leat_table(rules, group_id=group_id)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("diagnostics: LEAT compile failed; surfacing raw rules")
            rows = [dict(rule) for rule in rules]
            compiled_with = "raw-fallback"
    else:
        rows = [dict(rule) for rule in rules]
        compiled_with = "raw-fallback"

    return {
        "group_id": group_id,
        "compiled_with": compiled_with,
        "rule_count": len(rules),
        "row_count": len(rows),
        "rows": rows,
        "eli5_summary": (
            "{} tap → action rule(s) compiled into {} LEAT row(s) for group {}."
        ).format(len(rules), len(rows), group_id),
    }


def _collect_heartbeat(runtime: dict[str, Any], entry: Any) -> dict[str, Any]:
    """ELI5: the one 'is my mesh alive?' verdict, shared with the dashboard."""
    from .ship_protocol import diagnose_mesh

    data = getattr(entry, "data", None) or {}
    heartbeats = data.get("heartbeats") or []
    now_ms = int(data.get("heartbeat_now_ms", 0) or 0)
    return diagnose_mesh(heartbeats, now_ms=now_ms)


def _bundle(hass: Any, entry: Any, device: Any = None) -> dict[str, Any]:
    """ELI5: assemble the raw, unsanitized diagnostics bundle."""
    runtime = hass.data.get("ship_anchor", {}) if hasattr(hass, "data") else {}

    summary = runtime.get("engine_summary")
    if not isinstance(summary, dict):
        summary = engine_summary()

    bundle: dict[str, Any] = {
        "integration": {
            "domain": "ship_anchor",
            "diagnostics_version": DIAGNOSTICS_VERSION,
            "note": (
                "Structure preserved for debugging; cryptographic secrets "
                "(keys, nonces) redacted."
            ),
        },
        "engine": summary,
        "bindings": _collect_bindings(runtime),
        "leat": _collect_leat(runtime, entry),
        "heartbeat": _collect_heartbeat(runtime, entry),
        "entry": dict(getattr(entry, "data", None) or {}),
    }
    if device is not None:
        bundle["device"] = {
            "id": getattr(device, "id", None),
            "name_by_user": getattr(device, "name_by_user", None),
            "manufacturer": getattr(device, "manufacturer", None),
            "model": getattr(device, "model", None),
        }
    return bundle


def _scrub(bundle: dict[str, Any]) -> dict[str, Any]:
    """ELI5: redact every secret and attach a short sanitization report."""
    sanitized, redacted_paths = redact_structure(bundle, "diagnostics")
    sanitized["sanitization"] = {
        "redacted_field_count": len(redacted_paths),
        "redacted_fields": redacted_paths,
        "note": (
            "Any field whose name names a cryptographic secret "
            "(key, secret, hmac, nonce, token, password) was replaced with "
            "'***'; routing structure and node IEEE identifiers are preserved."
        ),
    }
    return sanitized


# ---------------------------------------------------------------------------
# Home Assistant entry points (auto-detected → "Download diagnostics" button)
# ---------------------------------------------------------------------------
async def async_get_config_entry_diagnostics(hass: Any, entry: Any) -> dict[str, Any]:
    """ELI5: the bundle downloaded from the integration's own page.

    Returns a sanitized dict the UI turns into a ``ship_anchor-<id>.json``
    file. Home Assistant auto-detects this function and shows the download
    button.
     """
    bundle = _bundle(hass, entry, device=None)
    result = _scrub(bundle)
    _LOGGER.info(
        "SHiP Anchor: config-entry diagnostics assembled "
        "(%d bindings, %d LEAT rows, %d redacted field(s)).",
        len(result.get("bindings", [])),
        result.get("leat", {}).get("row_count", 0),
        result.get("sanitization", {}).get("redacted_field_count", 0),
    )
    return result


async def async_get_device_diagnostics(
    hass: Any, entry: Any, device: Any
) -> dict[str, Any]:
    """ELI5: the bundle downloaded from a specific device's page.

    Identical to the config-entry bundle, plus a small ``device`` card so the
    engineer knows which physical device the photo was taken next to.
     """
    bundle = _bundle(hass, entry, device=device)
    return _scrub(bundle)


__all__ = [
    "async_get_config_entry_diagnostics",
    "async_get_device_diagnostics",
    "redact_structure",
    "DIAGNOSTICS_VERSION",
]
