"""
SHiP Anchor Tasmota Device Groups protocol handler.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This translator tunes Tasmota switches and plugs to a shared Device Group so
they can mirror actions locally, while preventing targets from echoing status.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Builds controller and target command backlogs plus the NoStatusShare scene
lock, then routes them through the compiler's HTTP GET transport.
"""

from __future__ import annotations

import re
from typing import Any

from . import BindingRecord, CompileResult, ProtocolHandler


# ELI5: Tasmota "Backlog" commands are separated by semicolons, so a group
# name containing a ';' (or '|', '@', space, or quote) would let a caller
# smuggle extra commands into the device. We only accept a safe identifier.
_SAFE_GROUP_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# ELI5: a dotted-quad IPv4 address (0–255 per octet, checked below).
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def _validate_ipv4(value: str, field: str) -> str:
    """Validate an IPv4 address; raise ValueError if it is not well-formed."""
    if not _IPV4_RE.match(value):
        raise ValueError(
            "{} must be a valid IPv4 address, got {!r}".format(field, value)
        )
    for octet in value.split("."):
        if int(octet) > 255:
            raise ValueError(
                "{} has an out-of-range octet: {!r}".format(field, value)
            )
    return value


def _validate_group_name(value: str) -> str:
    """Validate a Tasmota group name against a safe identifier charset."""
    if not _SAFE_GROUP_NAME.match(value):
        raise ValueError(
            "group_name must be a safe identifier (letters, digits, '.', '_', "
            "'-'); semicolons, quotes, spaces and shell metacharacters are "
            "not allowed. Got {!r}".format(value)
        )
    return value


class TasmotaHandler(ProtocolHandler):
    async def compile(
        self, compiler: Any, cfg: dict, result: CompileResult, dry_run: bool
    ) -> None:
        group_name = _validate_group_name(str(cfg.get("group_name", "anchor_grp_1")))
        controller_ip = _validate_ipv4(
            str(cfg.get("controller_ip", "192.168.1.50")), "controller_ip"
        )
        target_ips = [
            _validate_ipv4(str(ip), "target_ips[{}]".format(i))
            for i, ip in enumerate(cfg.get("target_ips", ["192.168.1.51"]))
        ]
        controller_backlog = (
            "SetOption85 1; SetOption88 1; "
            "DevGroupName1 {}; DevGroupShare 0xffffffff,0xffffffff"
        ).format(group_name)
        target_backlog = (
            "SetOption85 1; DevGroupName1 {}; DevGroupShare 0xffffffff,0"
        ).format(group_name)
        steps = [{
            "step": "tasmota_controller",
            "url": "http://{}/cm".format(controller_ip),
            "params": {"cmnd": "Backlog {}".format(controller_backlog)},
            "description": (
                "ELI5: Tell the controller to broadcast group state and detach "
                "its local relay."
            ),
        }]
        for target_ip in target_ips:
            steps.append({
                "step": "tasmota_target",
                "url": "http://{}/cm".format(target_ip),
                "params": {"cmnd": "Backlog {}".format(target_backlog)},
                "description": (
                    "ELI5: Make the target listen to group commands without "
                    "sending status back."
                ),
            })
        steps.append({
            "step": "tasmota_scene_lock",
            "url": "http://{}/cm".format(controller_ip),
            "params": {"cmnd": "DevGroupSend 129=@|18"},
            "description": (
                "ELI5: Prevent old brightness and colour status from "
                "overriding this scene."
            ),
        })
        for step in steps:
            result.dry_run_payloads.append(step)
            if not dry_run:
                await compiler._http_get_params(step["url"], step["params"])
        result.bindings_added.append(BindingRecord(
            protocol="tasmota",
            source_ieee=controller_ip,
            target_ieee=",".join(target_ips),
            clusters=[],
            group_id=None,
            preflight_done=True,
        ))
