"""
SHiP Anchor Shelly protocol handler.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This translator prepares a small Shelly-local script that reacts to a button
press and sends a command to its target without asking the hub.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
Generates the Shelly Gen2+ mJS body and the Script.Create, PutCode, SetConfig,
and Start RPC requests; delivery uses the compiler's HTTP transport.
"""

from __future__ import annotations

import re
from typing import Any

from . import BindingRecord, CompileResult, ProtocolHandler

# Reuse the IPv4 validator from the Tasmota handler (single source of truth).
try:
    from .tasmota import _is_private_or_doc_ip, _validate_ipv4
except ImportError:  # pragma: no cover - fallback if module layout changes
      _IPV4_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")

      def _is_private_or_doc_ip(value: str) -> bool:
          octets = [int(part) for part in value.split(".")]
          if octets[0] == 192 and octets[1] == 168:
              return True
          if octets[0] == 10:
              return True
          if octets[0] == 172 and 16 <= octets[1] <= 31:
              return True
          if octets[0] == 127:
              return True
          if octets[0] == 192 and octets[1] == 0 and octets[2] == 2:
              return True
          if octets[0] == 198 and octets[1] == 51 and octets[2] == 100:
              return True
          if octets[0] == 203 and octets[1] == 0 and octets[2] == 113:
              return True
          if octets[0] >= 240:
              return True
          return False

      def _validate_ipv4(value: str, field: str) -> str:
          if not _IPV4_RE.match(value):
              raise ValueError(
                     "{} must be a valid IPv4 address, got {!r}".format(field, value)
                 )
          for octet in value.split("."):
              if int(octet) > 255:
                  raise ValueError(
                         "{} has an out-of-range octet: {!r}".format(field, value)
                     )
          if not _is_private_or_doc_ip(value):
              raise ValueError(
                     "{} must be a private/local IP address, got public "
                     "address {!r}.".format(field, value)
                 )
          return value




# ELI5: the target command is written inside a JavaScript string in the
# generated Shelly script, so a quote or backslash could break out of the
# string and run arbitrary JS on the device. We only allow a plain token.
_SAFE_COMMAND = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_SAFE_SCRIPT_NAME = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ShellyHandler(ProtocolHandler):
    async def compile(
        self, compiler: Any, cfg: dict, result: CompileResult, dry_run: bool
    ) -> None:
        shelly_ip = _validate_ipv4(str(cfg.get("shelly_ip", "192.168.1.100")), "shelly_ip")
        # ELI5: the target may be expressed as `target_ip` (direct) or, in a
        # multi-hop chain, as the next device's IEEE / identifier value. We
        # accept either so the same handler can drive a one-hop relay or the
        # second leg of a "switch → canopy → load" chain.
        target_ip = _validate_ipv4(
            str(cfg.get("target_ip") or cfg.get("next_hop_ip") or "192.168.1.200"),
            "target_ip",
        )
        target_command = str(cfg.get("target_command", "on"))
        if not _SAFE_COMMAND.match(target_command):
            raise ValueError(
                "target_command must be a plain token (letters, digits, '_', "
                "'-'); quotes and backslashes are not allowed because this "
                "value is embedded in a JavaScript string. Got {!r}".format(
                    target_command
                )
            )
        script_name = str(cfg.get("script_name", "ship_anchor_relay"))
        if not _SAFE_SCRIPT_NAME.match(script_name):
            raise ValueError(
                "script_name must be a safe identifier (letters, digits, "
                "'.', '_', '-'). Got {!r}".format(script_name)
            )
        code = """
// SHiP Anchor — Shelly Local Relay Script
// ELI5: A button press asks the target device to act without the hub.
let targetIp = "{target_ip}";
let targetCmd = "{target_command}";
Shelly.addEventHandler(function(e) {{
  if (e.component === "input:0" && e.event === "btn_down") {{
    Shelly.call("HTTP.GET", {{
      url: "http://" + targetIp + "/rpc/Switch.Set?id=0&on=" +
        (targetCmd === "on" ? "true" : "false"),
      timeout: 2
    }}, null, null);
  }}
}});
""".format(target_ip=target_ip, target_command=target_command).strip()
        steps = [
            {
                "step": "Script.Create",
                "url": "http://{}/rpc/Script.Create".format(shelly_ip),
                "body": {"name": script_name},
            },
            {
                "step": "Script.PutCode",
                "url": "http://{}/rpc/Script.PutCode".format(shelly_ip),
                "body": {"id": 1, "code": code, "append": False},
            },
            {
                "step": "Script.SetConfig",
                "url": "http://{}/rpc/Script.SetConfig".format(shelly_ip),
                "body": {"id": 1, "config": {"enable": True}},
            },
            {
                "step": "Script.Start",
                "url": "http://{}/rpc/Script.Start".format(shelly_ip),
                "body": {"id": 1},
            },
        ]
        for step in steps:
            result.dry_run_payloads.append(step)
            if not dry_run:
                await compiler._http_post_json(step["url"], step["body"])
        result.bindings_added.append(BindingRecord(
            protocol="shelly",
            source_ieee=shelly_ip,
            target_ieee=target_ip,
            clusters=[],
            group_id=None,
            preflight_done=True,
        ))

    async def erase(self, compiler: Any, binding: BindingRecord) -> bool:
         # ELI5 (Finding E): re-validate the stored IP *at erase time*, so a
         # binding saved before the private-range check (or a mistyped one)
         # must not let an erase phone home to a public address.
         shelly_ip = _validate_ipv4(str(binding.source_ieee), "shelly_ip")
         await compiler._http_post_json(
              "http://{}/rpc/Script.Stop".format(shelly_ip), {"id": 1}
          )
         await compiler._http_post_json(
              "http://{}/rpc/Script.Delete".format(shelly_ip), {"id": 1}
          )
         return True
