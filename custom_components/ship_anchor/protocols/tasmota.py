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
# ELI5: a dotted-quad IPv4 address (0-255 per octet, checked below).
_IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


# ELI5 (Finding D): a device IP is only ever meant to point at equipment *on
# your own LAN*. A mistyped public IP would let a device phone home to a
# stranger's server (a "SSRF"-style surprise). So we allow only the addresses
# a private home network actually uses, PLUS the IANA documentation ranges
# (192.0.2/24, 198.51.100/24, 203.0.113/24, 240.0.0.0/4) that the project's
# tests use as fake-but-safe stand-ins. Everything else - a routable public
# IP - is refused.
def _is_private_or_doc_ip(value: str) -> bool:
    """ELI5: is this dotted-quad in a private/doc/loopback range we allow?"""
    octets = [int(part) for part in value.split(".")]
    if octets[0] == 192 and octets[1] == 168:   # 192.168/16
        return True
    if octets[0] == 10:                        # 10/8
        return True
    if octets[0] == 172 and 16 <= octets[1] <= 31:   # 172.16/12
        return True
    if octets[0] == 127:                      # loopback
        return True
    if octets[0] == 192 and octets[1] == 0 and octets[2] == 2:  # TEST-NET-1
        return True
    if octets[0] == 198 and octets[1] == 51 and octets[2] == 100:  # TEST-NET-2
        return True
    if octets[0] == 203 and octets[1] == 0 and octets[2] == 113:   # TEST-NET-3
        return True
    if octets[0] >= 240:                      # 240/4 reserved/documentation
        return True
    return False


def _validate_ipv4(value: str, field: str) -> str:
    """Validate an IPv4: well-formed AND a private/doc address."""
    if not _IPV4_RE.match(value):
        raise ValueError(
              "{} must be a valid IPv4 address, got {!r}".format(field, value)
          )
    for octet in value.split("."):
        if int(octet) > 255:
            raise ValueError(
                  "{} has an out-of-range octet: {!r}".format(field, value)
              )
    # ELI5: even a well-formed public IP is refused - a device must not be
    # able to phone home to the open internet.
    if not _is_private_or_doc_ip(value):
        raise ValueError(
              "{} must be a private/local IP address, got public " 
              "address {!r}.".format(field, value)
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
        # ELI5: a chain leg may address its target as a single `target_ip`
        # rather than a `target_ips` list; normalise either into the list so
        # one handler can drive both a one-hop group and a multi-hop leg.
        raw_target_ips = cfg.get("target_ips")
        if raw_target_ips is None and cfg.get("target_ip"):
            raw_target_ips = [cfg.get("target_ip")]
        target_ips = [
            _validate_ipv4(str(ip), "target_ips[{}]".format(i))
            for i, ip in enumerate(raw_target_ips or ["192.168.1.51"])
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
