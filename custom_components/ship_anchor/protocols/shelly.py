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

from typing import Any

from . import BindingRecord, CompileResult, ProtocolHandler


class ShellyHandler(ProtocolHandler):
    async def compile(
        self, compiler: Any, cfg: dict, result: CompileResult, dry_run: bool
    ) -> None:
        shelly_ip = cfg.get("shelly_ip", "192.168.1.100")
        target_ip = cfg.get("target_ip", "192.168.1.200")
        target_command = cfg.get("target_command", "on")
        script_name = cfg.get("script_name", "ship_anchor_relay")
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
        shelly_ip = binding.source_ieee
        await compiler._http_post_json(
            "http://{}/rpc/Script.Stop".format(shelly_ip), {"id": 1}
        )
        await compiler._http_post_json(
            "http://{}/rpc/Script.Delete".format(shelly_ip), {"id": 1}
        )
        return True
