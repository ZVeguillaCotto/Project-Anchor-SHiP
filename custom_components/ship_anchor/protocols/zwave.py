"""
SHiP Anchor Z-Wave JS protocol handler.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This translator programs a switch to contact a Z-Wave light or lock directly.
It blocks Long Range nodes and mismatched security badges before making a plan.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Builds Association (0x85) or Multi-Channel Association (0x8E) CC API calls
for zwave_js and emits a binding record only after both safety guards pass.
"""

from __future__ import annotations

from typing import Any

from . import BindingRecord, CompileResult, ProtocolHandler

ZWAVE_S2_ACCESS_CONTROL = "S2_AccessControl"
ZWAVE_S2_AUTHENTICATED = "S2_Authenticated"
ZWAVE_S2_UNAUTHENTICATED = "S2_Unauthenticated"
ZWAVE_S0_LEGACY = "S0_Legacy"
ZWAVE_NONE = "None"
ZWAVE_SECURITY_RANK = {
    ZWAVE_S2_ACCESS_CONTROL: 4,
    ZWAVE_S2_AUTHENTICATED: 3,
    ZWAVE_S2_UNAUTHENTICATED: 2,
    ZWAVE_S0_LEGACY: 1,
    ZWAVE_NONE: 0,
}


class ZwaveHandler(ProtocolHandler):
    async def compile(
        self, compiler: Any, cfg: dict, result: CompileResult, dry_run: bool
    ) -> None:
        source_node = cfg.get("source_node_id", 5)
        source_security = cfg.get("source_security", ZWAVE_S2_AUTHENTICATED)
        target_node = cfg.get("target_node_id", 6)
        target_security = cfg.get("target_security", ZWAVE_S2_AUTHENTICATED)
        group_id = cfg.get("association_group", 1)
        target_endpoint = cfg.get("target_endpoint", 0)

        if source_node >= 256 or target_node >= 256:
            offending = source_node if source_node >= 256 else target_node
            result.errors.append(
                "ELI5 — Z-Wave Long Range BLOCK: Node {} is ZWLR and uses a "
                "hub-only topology; direct binding is not physically possible."
                .format(offending)
            )
            result.success = False
            return

        source_rank = ZWAVE_SECURITY_RANK.get(source_security, 0)
        target_rank = ZWAVE_SECURITY_RANK.get(target_security, 0)
        if source_rank != target_rank:
            result.errors.append(
                "ELI5 — Z-Wave Security Mismatch: Node {} has security class "
                "'{}' but Node {} has '{}'. They must share the same security "
                "badge to associate directly.".format(
                    source_node, source_security, target_node, target_security
                )
            )
            result.success = False
            return

        multi_channel = target_endpoint > 0
        if multi_channel:
            call_data = {
                "node_id": source_node,
                "command_class": 0x8E,
                "endpoint": 0,
                "method_name": "addDestinations",
                "parameters": [{
                    "groupId": group_id,
                    "nodeIds": [],
                    "endpoints": [{
                        "nodeId": target_node,
                        "endpoint": target_endpoint,
                    }],
                }],
            }
        else:
            call_data = {
                "node_id": source_node,
                "command_class": 0x85,
                "endpoint": 0,
                "method_name": "addNodeIds",
                "parameters": [group_id, target_node],
            }
        result.dry_run_payloads.append({
            "step": "zwave_associate",
            "cc": "MultiChannelAssociation" if multi_channel else "Association",
            "data": call_data,
        })
        if not dry_run:
            await compiler._hass_service_call("zwave_js.invoke_cc_api", call_data)
        result.bindings_added.append(BindingRecord(
            protocol="zwave",
            source_ieee="zwave_node_{}".format(source_node),
            target_ieee="zwave_node_{}".format(target_node),
            clusters=[group_id],
            group_id=None,
            preflight_done=True,
        ))
