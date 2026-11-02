# ============================================================================
# SHiP Anchor control-center panel (v0.1.33): sidebar panel + WebSocket API.
#
# ELI5: until v0.1.33 the only way to *see* what SHiP Anchor had programmed was
# the cramped "Configure" options flow under a gear icon. This module gives the
# integration a dedicated full-screen sidebar panel (an mdi:anchor icon,
# inspired by advanced tools like C.A.F.E.) so a non-engineer can read every
# active binding and its health, watch the mesh, and *preview* a binding without
# ever sending a real frame. The data providers are pure (no Home Assistant, no
# radios, no network -- so the unit tests exercise them on a laptop) and the
# Home Assistant registration layer is thin and defensive so a missing or new HA
# version never crashes start-up. Project law held: ELI5 First, Engineering
# Second.
#
# ELI5 NOTE: this file deliberately uses # comments instead of triple-quoted
# docstrings so the module is immune to any docstring-indentation hazard.
# ============================================================================

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .compiler import AnchorCompiler
from .runtime import engine_summary
from .ship_protocol import diagnose_mesh

_LOGGER = logging.getLogger(__name__)

DOMAIN = "ship_anchor"

# ELI5: the sidebar icon is the project's mascot.
PANEL_ICON = "mdi:anchor"
PANEL_TITLE = "SHiP Anchor"
PANEL_NAME = "ship_anchor_dashboard"
PANEL_URL = "/api/" + DOMAIN

# ELI5: where the single-page dashboard lives on disk.
_FRONTEND_DIR = Path(__file__).parent / "frontend"


# ===========================================================================
# PART 1 -- THE PURE DATA PROVIDERS (the testable heart of the panel)
# ELI5: these know nothing about HA's web server or its WebSocket protocol. They
#   take the runtime dict and return plain JSON, which is why the unit tests
#   can exercise the control center on a laptop.
# ===========================================================================


def _runtime_or_empty(runtime):
    # ELI5: normalise whatever we were handed into a runtime-shaped dict. A None
    # runtime must degrade to an empty-but-valid snapshot rather than crash.
    if runtime is None:
        return {}
    return runtime if isinstance(runtime, dict) else {}


def build_bindings_overview(runtime):
    # ELI5: "here is every binding I have programmed, and how healthy each is."
    rt = _runtime_or_empty(runtime)
    compiler = rt.get("compiler")
    bindings = []
    drift_rows = []
    if compiler is not None:
        for binding in compiler.list_bindings():
            bindings.append(
                {
                    "protocol": binding.protocol,
                    "source": binding.source_ieee,
                    "target": binding.target_ieee,
                    "clusters": list(binding.clusters or []),
                    "group": binding.group_id,
                    "preflight_done": bool(binding.preflight_done),
                    "timestamp": binding.timestamp or "",
                }
            )
        # ELI5 (v0.1.23 Pillar 2): drift report flags a renamed/removed device.
        drift_rows = rt.get("drift_report")
        if not drift_rows:
            try:
                drift_rows = compiler.detect_binding_drift()
            except Exception:
                drift_rows = []
    else:
        drift_rows = rt.get("drift_report", []) or []

    drifted = sum(1 for row in drift_rows if row.get("drifted"))
    total = len(bindings)
    return {
        "eli5_summary": (
            "SHiP Anchor is holding {total} offline binding(s); {drifted} "
            "need(s) attention because a device was renamed or removed."
        ).format(total=total, drifted=drifted),
        "bindings": bindings,
        "drift": drift_rows,
        "summary": {
            "total_bindings": total,
            "drifted": drifted,
            "healthy": max(0, total - drifted),
        },
    }


def build_mesh_telemetry(runtime, now_ms=0):
    # ELI5: "is my mesh still alive?" -- relay health + node topology. Derives
    # one honest hop per saved binding and feeds it through the same
    # diagnose_mesh verdict the MCP tool uses, so the dashboard and the MCP tool
    # can never disagree about a hop's health.
    rt = _runtime_or_empty(runtime)
    compiler = rt.get("compiler")

    drift_missing = set()
    if compiler is not None:
        try:
            rows = rt.get("drift_report") or compiler.detect_binding_drift()
            for row in rows:
                if row.get("drifted"):
                    drift_missing.add(
                        str(row.get("binding", {}).get("source_ieee", ""))
                    )
        except Exception:
            drift_missing = set()

    # ELI5: one hop snapshot per binding so the topology is visible.
    hops = []
    if compiler is not None:
        for index, binding in enumerate(compiler.list_bindings()):
            state = (
                "orphaned"
                if str(binding.source_ieee) in drift_missing
                else "healthy"
            )
            hops.append(
                {
                    "hop_index": index,
                    "last_counter": 0,
                    "last_seen_ms": now_ms,
                    "state": state,
                    "protocol": binding.protocol,
                    "source": binding.source_ieee,
                    "target": binding.target_ieee,
                }
            )

    telemetry = diagnose_mesh(hops, now_ms=now_ms)
    telemetry["eli5_summary"] = (
        "Mesh telemetry: {n} binding-derived hop(s) -- {o} orphaned, "
        "{d} degraded, modeled {lat} ms round-trip."
    ).format(
        n=telemetry["hop_count"],
        o=telemetry["orphaned_hops"],
        d=telemetry["degraded_hops"],
        lat=telemetry["modeled_latency_ms"],
    )
    # ELI5: the node topology is the chain of hops, what canopy routing looks
    # like to a human.
    telemetry["topology"] = [
        {
            "from": h.get("source"),
            "to": h.get("target"),
            "protocol": h.get("protocol"),
            "health": h.get("state"),
            "badge": h.get("badge"),
        }
        for h in telemetry["hops"]
    ]
    return telemetry


async def async_build_compile_preview(compiler, config, dry_run=True):
    # ELI5: "what *would* happen if I bound this source to this target?" Always
    # a preview: dry_run is forced True so no real 19-byte frame ever leaves the
    # box. The preview is the compiler's CompileResult flattened to JSON, with a
    # plain-English headline.
    if compiler is None:
        return {
            "eli5_summary": "No compiler is ready yet, so nothing to preview.",
            "ok": False,
            "dry_run": True,
            "bindings_added": [],
            "warnings": ["ELI5: SHiP Anchor is still starting up."],
            "errors": [],
            "dry_run_payloads": [],
            "skipped": 0,
        }
    result = await compiler.async_compile_config(config or {}, dry_run=True)
    return {
        "eli5_summary": (
            "Dry-run preview: {a} binding(s) would be programmed, {w} "
            "warning(s), {e} error(s) -- nothing was sent to any device."
        ).format(
            a=len(result.bindings_added),
            w=len(result.warnings),
            e=len(result.errors),
        ),
        "ok": result.success,
        "dry_run": True,
        "bindings_added": [
            {
                "protocol": b.protocol,
                "source": b.source_ieee,
                "target": b.target_ieee,
                "clusters": list(b.clusters or []),
                "group": b.group_id,
            }
            for b in result.bindings_added
        ],
        "warnings": list(result.warnings),
        "errors": list(result.errors),
        "dry_run_payloads": list(result.dry_run_payloads),
        "skipped": int(getattr(result, "skipped", 0)),
    }


def build_dashboard_snapshot(runtime, now_ms=0):
    # ELI5: the single JSON blob the dashboard asks for on load. It stitches the
    # three providers together -- bindings + drift, mesh telemetry, and the
    # runtime engine card -- under one headline, so the front end makes exactly
    # one request instead of three.
    overview = build_bindings_overview(runtime)
    telemetry = build_mesh_telemetry(runtime, now_ms=now_ms)
    rt = _runtime_or_empty(runtime)
    engine = rt.get("engine_summary") or engine_summary()

    total = overview["summary"]["total_bindings"]
    drifted = overview["summary"]["drifted"]
    return {
        "eli5_summary": (
            "SHiP Anchor control center: {t} offline binding(s), {d} needing "
            "attention, mesh over {h} hop(s) at ~{lat} ms, {eng} engine."
        ).format(
            t=total,
            d=drifted,
            h=telemetry["hop_count"],
            lat=telemetry["modeled_latency_ms"],
            eng=engine.get("name", "unknown"),
        ),
        "bindings": overview,
        "mesh": telemetry,
        "engine": engine,
        "nonce_windows": rt.get("nonce_windows", {}) or {},
        "panel": {
            "name": PANEL_NAME,
            "icon": PANEL_ICON,
            "title": PANEL_TITLE,
            "url": PANEL_URL,
        },
    }


# ===========================================================================
# PART 2 -- THE WEBSOCKET COMMAND DISPATCHER (still pure & testable)
# ELI5: the front end sends a command name + params; this one function is the
#   only thing that maps a command to a provider, so the handlers are thin
#   wrappers and the logic is unit-tested. Everything is preview-only.
# ===========================================================================


async def async_handle_panel_command(command, runtime, params=None):
    # ELI5: turn a named dashboard request into a JSON answer. Known commands:
    # snapshot, bindings, telemetry, compile_preview (always dry-run), and ping.
    # An unknown command degrades to a calm ELI5 error instead of a traceback.
    params = params or {}
    if command == "ping":
        return {
            "eli5_summary": "SHiP Anchor panel is alive.",
            "ok": True,
            "command": "ping",
        }
    if command == "snapshot":
        return build_dashboard_snapshot(
            runtime, int(params.get("now_ms", 0) or 0)
        )
    if command == "bindings":
        return build_bindings_overview(runtime)
    if command == "telemetry":
        return build_mesh_telemetry(runtime, int(params.get("now_ms", 0) or 0))
    if command == "compile_preview":
        return await async_build_compile_preview(
            _runtime_or_empty(runtime).get("compiler"),
            params.get("config", {}) or {},
            dry_run=True,
        )
    return {
        "eli5_summary": (
            "The panel asked for '{cmd}', which SHiP Anchor does not know. "
            "Known: snapshot, bindings, telemetry, compile_preview, ping."
        ).format(cmd=command),
        "ok": False,
        "command": command,
        "errors": ["ELI5: unknown panel command '%s'." % command],
    }


# ===========================================================================
# PART 3 -- THE HOME ASSISTANT REGISTRATION LAYER (thin & defensive)
# ELI5: the only part that touches HA's web server, sidebar, and WebSocket.
#   Written so a missing or new HA version logs a calm warning and keeps the
#   rest of the integration alive, instead of crashing start-up.
# ===========================================================================


def load_dashboard_asset(name):
    # ELI5: read one of the dashboard's static files from disk. Returns the file
    # text, or an empty string if it is missing -- a missing asset must never
    # crash the panel.
    path = _FRONTEND_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        _LOGGER.warning("SHiP Anchor: dashboard asset %s not found", name)
        return ""


def _dashboard_html():
    # ELI5: the front page of the control center. Reads frontend/index.html off
    # disk; if the asset is missing the panel still serves a tiny visible
    # fallback so a user is never staring at a blank page with no idea what
    # happened.
    html = load_dashboard_asset("index.html")
    if not html:
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>SHiP Anchor</title></head>"
            "<body style='font-family:sans-serif;padding:2rem'>"
            "<h1>SHiP Anchor Control Center</h1>"
            "<p>ELI5: the dashboard assets are not installed yet. The backend "
            "WebSocket API (ship_anchor/get_snapshot) is fully available."
            "</p></body></html>"
        )
    return html


class ShipAnchorDashboardView:
    # ELI5: the static web page the sidebar icon opens. Serves the single-file
    # dashboard from a plain GET request. Home Assistant's http component owns
    # it, so it is only ever constructed and torn down inside the guarded
    # try-blocks.
    def __init__(self):
        self._html = _dashboard_html()

    async def get(self, request):
        # ELI5: answer a page request with the dashboard HTML.
        from homeassistant.components.http import HomeAssistantView

        return HomeAssistantView.get_content_response(self._html)

    async def post(self, request):
        # ELI5: the dashboard is read-only; refuse writes politely.
        from homeassistant.components.http import HomeAssistantView

        return HomeAssistantView.get_content_response(self._html, status=405)


async def async_register_ship_panel(hass, runtime):
    # ELI5: give SHiP Anchor its sidebar icon and wire up the API. Registers
    # three things, each guarded so a missing piece is a warning, not a crash:
    # (1) the sidebar panel via frontend; (2) the static view that serves the
    # dashboard; (3) the WebSocket commands. Returns a record of what was
    # registered for clean teardown.
    registered = {"panel": False, "view": False, "ws": False}
    runtime["panel_registration"] = registered

    # --- 1. Sidebar panel (the mdi:anchor icon) ----------------------------
    try:
        from homeassistant.components.frontend import (
            Panel,
            async_register_panels,
        )

        panel = Panel(
            name=PANEL_NAME,
            title=PANEL_TITLE,
            icon=PANEL_ICON,
            component_name=DOMAIN,
            url=PANEL_URL,
        )
        async_register_panels(hass, DOMAIN, panel)
        registered["panel"] = True
        _LOGGER.warning(
            "SHiP Anchor: sidebar panel (%s) registered.", PANEL_ICON
        )
    except Exception as exc:
        _LOGGER.warning(
            "SHiP Anchor: sidebar panel not registered (%s) -- "
            "the WebSocket API is still available.",
            exc,
        )

    # --- 2. Static view (the dashboard HTML/JS) ----------------------------
    try:
        view = ShipAnchorDashboardView()
        hass.http.register_view(view)
        runtime["dashboard_view"] = view
        registered["view"] = True
        _LOGGER.warning("SHiP Anchor: dashboard view registered at %s", PANEL_URL)
    except Exception as exc:
        _LOGGER.warning("SHiP Anchor: dashboard view not registered: %s", exc)

    # --- 3. WebSocket commands (the backend API) ---------------------------
    try:
        await async_register_ws_commands(hass, runtime)
        registered["ws"] = True
    except Exception as exc:
        _LOGGER.warning("SHiP Anchor: WebSocket API not registered: %s", exc)

    return registered


async def async_register_ws_commands(hass, runtime):
    # ELI5: register the dashboard's WebSocket commands. Each command is a thin
    # wrapper around the pure async_handle_panel_command dispatcher, so the logic
    # is tested separately. Every command is preview-only.
    from homeassistant.components import websocket_api

    def _runtime_now():
        # ELI5: read the *current* runtime so the panel sees fresh bindings.
        return hass.data.get(DOMAIN, runtime)

    def make_handler(name):
        # ELI5: build one WebSocket handler that defers to the pure dispatcher.
        async def _handler(hass_inner, connection, msg):
            result = await async_handle_panel_command(
                name, _runtime_now(), msg.get("params", {}) or {}
            )
            connection.send_result(msg["id"], result)
        return _handler

    # ELI5: the WebSocket verb and the dispatcher command differ only for the
    # full snapshot; map them in one loop so the verbs stay in sync.
    for verb in ("get_snapshot", "bindings", "telemetry", "compile_preview", "ping"):
        command = "snapshot" if verb == "get_snapshot" else verb
        websocket_api.async_register_command(
            hass, "ship_anchor/%s" % verb, make_handler(command)
        )


async def async_unregister_ship_panel(hass):
    # ELI5: take the panel down when the last binding goes away. Best-effort: any
    # missing piece is ignored, so teardown never blocks the unload of the real
    # (load-bearing) services.
    try:
        from homeassistant.components.frontend import async_unregister_panels

        async_unregister_panels(hass, DOMAIN)
    except Exception:
        pass
    # ELI5: stop serving the dashboard view so old assets are gone.
    view = hass.data.get(DOMAIN, {}).get("dashboard_view")
    if view is not None:
        try:
            hass.http.unregister_view(view)
        except Exception:
            pass
    _LOGGER.info("SHiP Anchor: panel teardown attempted")
