#!/usr/bin/env python3
"""
SHiP Anchor MCP bridge.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This is a small adapter that lets an AI assistant ask SHiP Anchor focused
questions: how a device should be paired, what commands a binding would send,
and whether a 19-byte radio message is genuine. It keeps those operations
available without making the assistant read every project file.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Python 3.9+, standard library only. The server speaks newline-delimited
JSON-RPC over stdio, exposes eight MCP tools (device inspection, binding
preview, LEAT behaviour baking, binding inventory, 19-byte encode/decode, the
verification suite, and the v0.1.18 mesh diagnostic), and includes CLI and
demo modes. Every tool is preview-only — none of them actuate real devices.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict
from typing import Any, Optional

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

TOOL_DEFINITIONS = [
    {
        "name": "ship_inspect_device",
        "description": "Inspect device quirks, endpoints, group escalation, and preflight calls.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ieee": {"type": "string"},
                "model": {"type": "string"},
                "manufacturer": {"type": "string"},
                "target_count": {"type": "integer", "minimum": 0},
                "target_models": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["ieee", "model", "manufacturer"],
        },
    },
    {
        "name": "ship_compile_binding",
        "description": "Preview exact hardware binding payloads; this tool never actuates devices.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "protocol": {
                    "type": "string",
                    "enum": ["AUTO", "ZIGBEE_ZHA", "ZIGBEE_Z2M", "MATTER",
                             "ZWAVE", "SHELLY", "TASMOTA"],
                },
                "config_dict": {"type": "object"},
                "dry_run": {"type": "boolean", "default": True},
            },
            "required": ["protocol", "config_dict"],
        },
    },
    {
        "name": "ship_bake_behavior",
        "description": "Compile gesture + time-window rules into LEAT rows (matches C ship_leat_entry_t). Preview only, never actuates.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {"type": "integer", "minimum": 0, "maximum": 255},
                "rules": {"type": "array", "items": {"type": "object"}},
            },
            "required": ["rules"],
        },
    },
    {
        "name": "ship_list_bindings",
        "description": "Normalize a stored bindings list into an inventory (count + per-protocol). Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"bindings": {"type": "array", "items": {"type": "object"}}},
        },
    },
    {
        "name": "ship_encode_19b_frame",
        "description": "Encode and explain one authenticated 19-byte SHiP frame.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pan_id": {"type": "integer", "minimum": 0, "maximum": 65535},
                "src_short": {"type": "integer", "minimum": 0, "maximum": 65535},
                "dst_short": {"type": "integer", "minimum": 0, "maximum": 65535},
                "seq_num": {"type": "integer", "minimum": 0, "maximum": 255},
                "group_id": {"type": "integer", "minimum": 0, "maximum": 255},
                "domain": {"type": "integer", "minimum": 0, "maximum": 15},
                "gesture": {"type": "integer", "minimum": 0, "maximum": 15},
                "param_1": {"type": "integer", "minimum": 0, "maximum": 255},
                "param_2": {"type": "integer", "minimum": 0, "maximum": 255},
                "param_3": {"type": "integer", "minimum": 0, "maximum": 255},
                "counter": {"type": "integer", "minimum": 0, "maximum": 65535},
                "key_hex": {"type": "string", "minLength": 32, "maxLength": 32},
                "utc_epoch": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 4294967295,
                    "description": "Required for domain 0xF; UTC Unix seconds.",
                },
            },
            "required": ["pan_id", "src_short", "dst_short", "seq_num", "group_id",
                         "domain", "gesture", "param_1", "param_2", "param_3",
                         "counter", "key_hex"],
        },
    },
    {
        "name": "ship_decode_verify_19b_frame",
        "description": "Verify a frame's magic, SipHash tag, and anti-replay counter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "frame_hex": {"type": "string", "minLength": 38, "maxLength": 38},
                "key_hex": {"type": "string", "minLength": 32, "maxLength": 32},
                "last_counter": {"type": "integer", "minimum": 0, "maximum": 65535},
            },
            "required": ["frame_hex", "key_hex", "last_counter"],
        },
    },
    {
        "name": "ship_run_verification_suite",
        "description": "Run the linked C tests and Python disaster drills; return pass/fail.",
        "inputSchema": {"type": "object", "properties": {}},
    },
     {
         "name": "ship_diagnose_mesh",
         "description": "Diagnose a live mesh from per-hop heartbeat + anti-replay nonce state (v0.1.18). Returns per-hop health (healthy/degraded/orphaned/unknown), a re-handshake flag, and a modeled multi-hop latency. Preview only, never actuates.",
         "inputSchema": {
             "type": "object",
             "properties": {
                 "hops": {
                     "type": "array",
                     "items": {
                         "type": "object",
                         "properties": {
                             "hop_index": {"type": "integer", "minimum": 0},
                             "last_counter": {"type": "integer", "minimum": 0, "maximum": 65535},
                             "last_seen_ms": {"type": "integer", "minimum": 0},
                         },
                         "required": ["last_seen_ms"],
                     },
                 },
                 "now_ms": {"type": "integer", "minimum": 0},
             },
             "required": ["hops"],
         },
     },
]


def _schema_token_count() -> int:
    """Count stable lexical tokens in compact schema JSON (not model BPE tokens)."""
    serialized = json.dumps(TOOL_DEFINITIONS, separators=(",", ":"), sort_keys=True)
    return len(re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", serialized))

from custom_components.ship_anchor.ship_protocol import (
    decode_verify_19b_frame as ship_decode_verify_19b_frame,
    encode_19b_frame as ship_encode_19b_frame,
    diagnose_mesh,
)


async def _call_tool(name: str, arguments: dict) -> Any:
    if name == "ship_inspect_device":
        from custom_components.ship_anchor.quirks_inspector import QuirksInspector

        result = QuirksInspector().inspect(
            ieee=arguments["ieee"],
            model=arguments["model"],
            manufacturer=arguments["manufacturer"],
            target_count=int(arguments.get("target_count", 1)),
            target_models=arguments.get("target_models"),
        )
        details = asdict(result)
        return {
            "eli5_summary": (
                "The source needs {} endpoint {}, targets use endpoint {}, and {}."
            ).format(
                result.model,
                result.source_endpoint,
                result.target_endpoint,
                "ZCL group binding" if result.requires_group_binding
                else "direct device binding",
            ),
            "requires_group_binding": result.requires_group_binding,
            "preflight_service_calls": result.preflight_service_calls,
            "inspection": details,
        }

    if name == "ship_compile_binding":
        if not arguments.get("dry_run", True):
            raise ValueError("This MCP tool only previews bindings; dry_run must be true")
        from custom_components.ship_anchor.compiler import (
            AnchorCompiler,
            Protocol,
            parse_ha_automation_dict,
        )

        config = dict(arguments["config_dict"])
        requested = str(arguments["protocol"]).upper()
        device_registry = config.pop("device_registry", None)
        entity_registry = config.pop("entity_registry", None)
        if requested != "AUTO":
            protocol = Protocol.__members__.get(requested)
            if protocol is None or protocol == Protocol.UNKNOWN:
                raise ValueError("Unsupported compiler protocol: {}".format(requested))
            config["protocol"] = protocol.name
        parsed = parse_ha_automation_dict(config, device_registry, entity_registry)
        result = await AnchorCompiler(None, None, {}).async_compile_config(
            parsed, dry_run=True
        )
        return asdict(result)

    if name == "ship_bake_behavior":
        # ELI5: turn the wizard's "rule sheets" (a tap count / hold, an
        # optional time-of-day window, and action parameters) into the exact
        # LEAT rows the C core stores on hardware. This is a pure preview —
        # it only builds rows, it never touches a device.
        from custom_components.ship_anchor.compiler import AnchorCompiler

        rules = arguments.get("rules") or []
        if not isinstance(rules, list):
            raise ValueError("rules must be an array of rule objects")
        group_id = int(arguments.get("group_id", 0))
        table = AnchorCompiler(None, None, {}).build_leat_table(rules, group_id)
        return {
            "eli5_summary": (
                "Baked {} rule(s) into {} LEAT row(s) on group {}."
            ).format(len(rules), len(table), group_id),
            "row_count": len(table),
            "leat_rows": table,
        }

    if name == "ship_list_bindings":
        # ELI5: given the raw rows an agent read from
        # .storage/ship_anchor.bindings, return a tidy inventory: how many
        # bindings, broken down by protocol, plus the normalised records.
        from custom_components.ship_anchor.compiler import AnchorCompiler

        bindings = arguments.get("bindings") or []
        if not isinstance(bindings, list):
            raise ValueError("bindings must be an array of stored rows")
        compiler = AnchorCompiler(None, None, {"bindings": bindings})
        records = compiler.list_bindings()
        by_protocol: dict[str, int] = {}
        for record in records:
            by_protocol[record.protocol] = by_protocol.get(record.protocol, 0) + 1
        return {
            "eli5_summary": "{} binding(s) stored across {} protocol(s).".format(
                len(records), len(by_protocol)
            ),
            "count": len(records),
            "by_protocol": by_protocol,
            "bindings": [asdict(record) for record in records],
        }

    if name == "ship_encode_19b_frame":
        return ship_encode_19b_frame(arguments)

    if name == "ship_decode_verify_19b_frame":
        return ship_decode_verify_19b_frame(arguments)

    if name == "ship_run_verification_suite":
        runner = os.path.join(REPO_ROOT, "sim", "run_anchor_benchmarks.py")
        completed = subprocess.run(
            [sys.executable, runner, "--skip-mcp"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = completed.stdout + completed.stderr
        c_summary = re.search(r"RESULTS:\s+\d+/\d+ tests passed.*", output)
        drill_summary = re.search(r"ALL \d+/\d+ DRILLS PASSED", output)
        return {
            "passed": completed.returncode == 0,
            "exit_code": completed.returncode,
            "c_tests": c_summary.group(0) if c_summary else "C test summary unavailable",
            "python_drills": drill_summary.group(0) if drill_summary else
                "Python drill summary unavailable",
        }

    if name == "ship_diagnose_mesh":
         # ELI5: the "is my mesh still alive?" tool. It reads the per-hop
         # heartbeat snapshot (when each hop last heard a GENUINE frame, its
         # anti-replay nonce) and reports a health verdict for every hop —
         # healthy / degraded / orphaned / unknown — plus whether any branch
         # needs a re-handshake, and a modeled multi-hop latency. It is a
         # pure preview: it only reads, it never touches a device.
        hops = arguments.get("hops") or []
        if not isinstance(hops, list):
            raise ValueError("hops must be an array of hop heartbeat objects")
        for raw in hops:
            if not isinstance(raw, dict):
                raise ValueError("each hop must be an object")
        now_ms = int(arguments.get("now_ms", 0))

        # ELI5 (v0.1.20): the dashboard and this tool now share ONE
        # verdict. diagnose_mesh() is the single source of truth, so a
        # hop can't look healthy here and orphaned in the dashboard. It
        # carries the per-hop status badges (green/yellow/red/white)
        # plus the orphaned/degraded counts and the modeled latency.
        return diagnose_mesh(hops, now_ms)
async def _run_demo() -> dict:
    # >>> MOCK TEST VECTOR ONLY <<< — a well-known placeholder key used purely
    # so the demo produces a deterministic, reproducible frame. It is NOT a
    # real secret and must never be used to secure a live device.
    key_hex = "deadbeefcafebabe0102030405060708"
    frame_args = {
        "pan_id": 0x1234,
        "src_short": 1,
        "dst_short": 0xFFFF,
        "seq_num": 1,
        "group_id": 1,
        "domain": 1,
        "gesture": 1,
        "param_1": 1,
        "param_2": 0,
        "param_3": 0,
        "counter": 10,
        "key_hex": key_hex,
    }
    encoded = ship_encode_19b_frame(frame_args)
    rtc_args = dict(frame_args)
    rtc_args.update({"domain": 0xF, "utc_epoch": 1700000000})
    rtc_encoded = ship_encode_19b_frame(rtc_args)
    rtc_decoded = ship_decode_verify_19b_frame({
        "frame_hex": rtc_encoded["frame_hex"],
        "key_hex": key_hex,
        "last_counter": 9,
    })
    calls = [
        ("ship_inspect_device", {
            "ieee": "00:0d:6f:00:05:7d:2d:34",
            "model": "VZM31-SN",
            "manufacturer": "Inovelli",
            "target_count": 4,
            "target_models": ["LCA001"] * 4,
        }),
        ("ship_compile_binding", {
            "protocol": "AUTO",
            "config_dict": {
                "triggers": [{
                    "platform": "device",
                    "domain": "zha",
                    "device_id": "inovelli",
                    "subtype": "button_1_single",
                }],
                "actions": [{
                    "action": "light.turn_on",
                    "target": {"device_id": ["hue-{}".format(index)
                                              for index in range(1, 5)]},
                }],
                "device_registry": {
                    "inovelli": {
                        "ieee": "00:0d:6f:00:05:7d:2d:34",
                        "model": "VZM31-SN",
                        "manufacturer": "Inovelli",
                    },
                    **{
                        "hue-{}".format(index): {
                            "ieee": "00:17:88:01:09:aa:bb:{:02d}".format(index),
                            "model": "LCA001",
                        }
                        for index in range(1, 5)
                    },
                },
            },
        }),
        ("ship_bake_behavior", {
            "group_id": 42,
            "rules": [
                {"gesture": "1x Tap", "brightness": 100, "color_temp_mireds": 350},
                {"gesture": "5x Tap", "hour_start": 22, "hour_end": 6, "brightness": 15},
            ],
        }),
        ("ship_list_bindings", {
            "bindings": [
                {"protocol": "zigbee_zha", "source_ieee": "aa", "target_ieee": "group",
                 "clusters": [6, 8], "group_id": 42, "preflight_done": True},
                {"protocol": "shelly", "source_ieee": "192.0.2.10", "target_ieee": "192.0.2.11",
                 "clusters": [], "group_id": None, "preflight_done": True},
            ],
        }),
        ("ship_encode_19b_frame", frame_args),
        ("ship_decode_verify_19b_frame", {
            "frame_hex": encoded["frame_hex"],
            "key_hex": key_hex,
            "last_counter": 9,
        }),
        ("ship_run_verification_suite", {}),
         ("ship_diagnose_mesh", {
              "now_ms": 240000,
              "hops": [
                   {"hop_index": 1, "last_counter": 41, "last_seen_ms": 240000},
                   {"hop_index": 2, "last_counter": 39, "last_seen_ms": 120000},
                   {"hop_index": 3, "last_counter": 12, "last_seen_ms": 60000},
             ],
          }),
      ]
    results = {}
    for name, arguments in calls:
        results[name] = await _call_tool(name, arguments)
    return {
        "schema_token_count": _schema_token_count(),
        "schema_token_count_method": "exact lexical-token count of compact schema JSON",
        "mesh_rtc_round_trip": rtc_decoded,
        "tool_results": results,
    }


async def _dispatch(request: dict) -> Optional[dict]:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if "id" not in request:
        return None
    if method == "initialize":
        params = request.get("params") or {}
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ship-anchor", "version": "0.1.24"},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": TOOL_DEFINITIONS},
        }
    if method == "tools/call":
        params = request.get("params") or {}
        try:
            result = await _call_tool(params["name"], params.get("arguments") or {})
            content = [{"type": "text", "text": json.dumps(result, separators=(",", ":"))}]
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"content": content, "structuredContent": result, "isError": False},
            }
        except (
            KeyError,
            TypeError,
            ValueError,
            RuntimeError,
            OSError,
            ImportError,
            subprocess.TimeoutExpired,
        ) as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": "Method not found: {}".format(method)},
    }


async def _serve_stdio() -> None:
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            break
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request"},
                }
            else:
                response = await _dispatch(request)
        except json.JSONDecodeError as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error: {}".format(exc)},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="SHiP Anchor MCP server and local test client")
    parser.add_argument(
        "--cli",
        nargs="?",
        const="list",
        metavar="TOOL",
        help="Call one tool locally, or print the tool definitions when omitted",
    )
    parser.add_argument(
        "--args",
        default="{}",
        help="JSON object of tool arguments used with --cli TOOL",
    )
    parser.add_argument("--demo", action="store_true", help="Run one example for each tool")
    options = parser.parse_args()
    if options.demo:
        print(json.dumps(asyncio.run(_run_demo()), indent=2, sort_keys=True))
        return 0
    if options.cli is not None:
        if options.cli == "list":
            print(json.dumps({
                "tools": TOOL_DEFINITIONS,
                "schema_token_count": _schema_token_count(),
                "schema_token_count_method": "exact lexical-token count of compact schema JSON",
            }, indent=2))
            return 0
        arguments = json.loads(options.args)
        result = asyncio.run(_call_tool(options.cli, arguments))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    asyncio.run(_serve_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
