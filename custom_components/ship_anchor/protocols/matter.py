"""
SHiP Anchor Matter protocol handler.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
Matter direct binding is a two-step exchange: give the target permission to
listen to the source, then give the source the target's address.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Builds AccessControl (0x001F) and Binding (0x001E) writes for matter-js-server
and delegates delivery to the compiler's WebSocket transport.
"""

from __future__ import annotations

from typing import Any

from . import BindingRecord, CompileResult, ProtocolHandler

MATTER_WS_URL = "ws://localhost:5580/ws"
MATTER_CLUSTER_ACCESS_CONTROL = 0x001F
MATTER_CLUSTER_BINDING = 0x001E
MATTER_CLUSTER_SWITCH = 0x003B
MATTER_CLUSTER_ONOFF = 0x0006
MATTER_CLUSTER_LEVEL_CTRL = 0x0008
MATTER_CLUSTER_SCENES_MGMT = 0x0062


class MatterHandler(ProtocolHandler):
    async def compile(
        self, compiler: Any, cfg: dict, result: CompileResult, dry_run: bool
    ) -> None:
        source_node = cfg.get("source_node_id", 38)
        source_endpoint = cfg.get("source_endpoint", 2)
        target_node = cfg.get("target_node_id", 37)
        target_endpoint = cfg.get("target_endpoint", 1)
        fabric = cfg.get("fabric_index", 2)
        clusters = cfg.get(
            "clusters", [MATTER_CLUSTER_ONOFF, MATTER_CLUSTER_LEVEL_CTRL]
        )
        source_clusters = cfg.get("source_clusters", [])
        if MATTER_CLUSTER_SWITCH in source_clusters and not any(
            cluster in source_clusters
            for cluster in (MATTER_CLUSTER_ONOFF, MATTER_CLUSTER_LEVEL_CTRL)
        ):
            result.warnings.append(
                "ELI5 COMPATIBILITY WARNING — Matter Node {}:\n"
                "  This button-only Switch cluster has no On/Off client commands.\n"
                "  It cannot bind offline without a compatible client cluster.".format(
                    source_node
                )
            )
            result.errors.append(
                "Matter Node {}: Switch-only cluster — cannot bind offline.".format(
                    source_node
                )
            )
            result.success = False
            return

        acl_value = [
            {
                "1": 5,
                "2": 2,
                "3": [112233],
                "4": None,
                "254": fabric,
            },
            {
                "1": 3,
                "2": 2,
                "3": [source_node],
                "4": [
                    {"0": cluster, "1": target_endpoint, "2": None}
                    for cluster in clusters
                ],
                "254": fabric,
            },
        ]
        acl_message = {
            "message_id": "acl_write_{}_{}".format(source_node, target_node),
            "command": "write_attribute",
            "args": {
                "node_id": target_node,
                "attribute_path": "0/{}/0".format(MATTER_CLUSTER_ACCESS_CONTROL),
                "value": acl_value,
            },
        }
        binding_value = [
            {
                "1": target_node,
                "2": None,
                "3": target_endpoint,
                "4": cluster,
                "254": fabric,
            }
            for cluster in clusters
        ]
        binding_message = {
            "message_id": "bind_write_{}_{}".format(source_node, target_node),
            "command": "write_attribute",
            "args": {
                "node_id": source_node,
                "attribute_path": "{}/{}/0".format(
                    source_endpoint, MATTER_CLUSTER_BINDING
                ),
                "value": binding_value,
            },
        }
        result.dry_run_payloads.extend([
            {"step": "matter_acl", "ws_msg": acl_message},
            {"step": "matter_binding", "ws_msg": binding_message},
        ])
        if not dry_run:
            await compiler._matter_ws_send(acl_message)
            await compiler._matter_ws_send(binding_message)
        result.bindings_added.append(BindingRecord(
            protocol="matter",
            source_ieee="matter_node_{}".format(source_node),
            target_ieee="matter_node_{}".format(target_node),
            clusters=clusters,
            group_id=None,
            preflight_done=True,
        ))
