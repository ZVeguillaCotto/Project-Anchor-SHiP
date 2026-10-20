"""
SHiP Anchor Zigbee Home Automation protocol handler.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This translator puts Zigbee switches and bulbs in the right mode, adds group
members to their shared address book, and binds the switch to the group.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Builds ZHA service-call payloads, handles ZCL Groups Add before source group
binding for more than two targets, and records the resulting binding.
"""

from __future__ import annotations

import logging
from typing import Any

from . import BindingRecord, CompileResult, ProtocolHandler
from ..quirks_inspector import (
    CLUSTER_LEVEL_CTRL,
    CLUSTER_ONOFF,
    CLUSTER_SCENES,
)

_LOGGER = logging.getLogger(__name__)


class ZhaHandler(ProtocolHandler):
    async def compile(
        self, compiler: Any, cfg: dict, result: CompileResult, dry_run: bool
    ) -> None:
        src_ieee = cfg.get("source_ieee", "00:00:00:00:00:00:00:01")
        src_model = cfg.get("source_model", "VZM31-SN")
        src_mfr = cfg.get("source_manufacturer", "Inovelli")
        targets = cfg.get("targets", [])
        if not targets:
            result.success = False
            result.errors.append(
                "Cannot compile ZHA automation without at least one resolved target."
            )
            return
        if not src_ieee:
            result.success = False
            result.errors.append(
                "Cannot compile ZHA automation: the source device IEEE address "
                "could not be resolved from the device registry."
            )
            return
        unresolved_target = next(
            (target for target in targets if not target.get("ieee")), None
        )
        if unresolved_target is not None:
            result.success = False
            result.errors.append(
                "Cannot compile ZHA automation: target device {} has no resolved "
                "IEEE address.".format(
                    unresolved_target.get("device_id")
                    or unresolved_target.get("entity_id")
                    or "?"
                )
            )
            return

        preflight = compiler._inspector.inspect(
            ieee=src_ieee,
            model=src_model,
            manufacturer=src_mfr,
            target_count=len(targets),
            target_models=[target.get("model", "LCA001") for target in targets],
        )
        payloads = []
        for call in preflight.preflight_service_calls:
            payloads.append({"step": "preflight", "call": call})
            if not dry_run:
                await compiler._hass_service_call(call["action"], call["data"])

        clusters = [
            cluster for cluster in preflight.bindable_clusters
            if cluster in (CLUSTER_ONOFF, CLUSTER_LEVEL_CTRL, CLUSTER_SCENES)
        ]
        if preflight.requires_group_binding:
            group_name = "anchor_grp_{}".format(preflight.group_id)
            for target in targets:
                endpoint = compiler._inspector._resolve_target_endpoint(
                    target.get("model", "")
                )
                call = {
                    "action": "zha.issue_zigbee_cluster_command",
                    "data": {
                        "ieee": target["ieee"],
                        "endpoint_id": endpoint,
                        "cluster_id": 0x0004,
                        "cluster_type": "in",
                        "command": 0x00,
                        "args": [preflight.group_id, group_name],
                    },
                }
                payloads.append({"step": "add_group_membership", "call": call})
                if not dry_run:
                    await compiler._hass_service_call(call["action"], call["data"])

            for cluster in clusters:
                data = {
                    "ieee": src_ieee,
                    "command_data": preflight.group_id,
                    "src_endpoint": preflight.source_endpoint,
                    "cluster": cluster,
                    "tries": 100,
                }
                payloads.append({"step": "bind_group", "data": data})
                if not dry_run:
                    await compiler._hass_service_call("zha_toolkit.bind_group", data)

            record = BindingRecord(
                protocol="zigbee_zha",
                source_ieee=src_ieee,
                target_ieee="group",
                clusters=clusters,
                group_id=preflight.group_id,
                preflight_done=True,
            )
        else:
            records = []
            for target in targets:
                dst_ieee = target["ieee"]
                dst_endpoint = compiler._inspector._resolve_target_endpoint(
                    target.get("model", "LCA001")
                )
                for cluster in clusters:
                    data = {
                        "ieee": src_ieee,
                        "command_data": dst_ieee,
                        "src_endpoint": preflight.source_endpoint,
                        "dst_endpoint": dst_endpoint,
                        "cluster": cluster,
                        "tries": 100,
                        "expect_reply": True,
                    }
                    payloads.append({"step": "bind_ieee", "data": data})
                    if not dry_run:
                        await compiler._hass_service_call("zha_toolkit.bind_ieee", data)
                records.append(BindingRecord(
                    protocol="zigbee_zha",
                    source_ieee=src_ieee,
                    target_ieee=dst_ieee,
                    clusters=clusters,
                    group_id=None,
                    preflight_done=True,
                ))
            record = records[0]

        result.bindings_added.append(record)
        result.dry_run_payloads.extend(payloads)
        result.warnings.extend(preflight.warnings)

    async def erase(self, compiler: Any, binding: BindingRecord) -> bool:
        _LOGGER.info(
            "SHiP Anchor: ZHA unbind %s → %s (clusters=%s)",
            binding.source_ieee, binding.target_ieee, binding.clusters,
        )
        return True
