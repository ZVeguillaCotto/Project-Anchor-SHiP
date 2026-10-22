"""
SHiP Anchor protocol handlers.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
Each protocol handler is a separate translator for one device ecosystem. The
dispatcher chooses the translator, while the shared compiler supplies storage,
device inspection, and transport services.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
This package owns protocol identity, compiler result records, the abstract
handler contract, and lazy dispatch. New protocols add one handler module and
one registry entry without extending AnchorCompiler's routing logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


class Protocol(Enum):
    ZIGBEE_ZHA = auto()
    ZIGBEE_Z2M = auto()
    MATTER = auto()
    ZWAVE = auto()
    SHELLY = auto()
    TASMOTA = auto()
    UNKNOWN = auto()


PROTOCOL_ALIASES = {
    "ZHA": Protocol.ZIGBEE_ZHA,
    "ZIGBEE": Protocol.ZIGBEE_ZHA,
    "ZIGBEE_ZHA": Protocol.ZIGBEE_ZHA,
    "Z2M": Protocol.ZIGBEE_Z2M,
    "ZIGBEE2MQTT": Protocol.ZIGBEE_Z2M,
    "ZIGBEE_Z2M": Protocol.ZIGBEE_Z2M,
    "MATTER": Protocol.MATTER,
    "ZWAVE": Protocol.ZWAVE,
    "ZWAVE_JS": Protocol.ZWAVE,
    "SHELLY": Protocol.SHELLY,
    "TASMOTA": Protocol.TASMOTA,
}


@dataclass
class BindingRecord:
    """A saved description of one programmed device relationship."""

    protocol: str
    source_ieee: str
    target_ieee: str
    clusters: list[int]
    group_id: Optional[int]
    preflight_done: bool = False
    timestamp: str = ""


@dataclass
class CompileResult:
    """Bindings, warnings, errors, and preview payloads for one compile."""

    success: bool = True
    bindings_added: list[BindingRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run_payloads: list[dict] = field(default_factory=list)


class ProtocolHandler(ABC):
    """Common contract implemented by each isolated protocol translator."""

    @abstractmethod
    async def compile(
        self, compiler: Any, config: dict, result: CompileResult, dry_run: bool
    ) -> None:
        """Build and optionally apply one protocol's binding instructions."""

    async def erase(self, compiler: Any, binding: BindingRecord) -> bool:
        """Undo a binding when the protocol exposes a supported removal path."""
        return False


class ProtocolDispatcher:
    """Select handlers without embedding protocol behavior in the compiler."""

    _HANDLERS = {
        Protocol.ZIGBEE_ZHA: ("zha", "ZhaHandler"),
        Protocol.ZIGBEE_Z2M: ("z2m", "Z2mHandler"),
        Protocol.MATTER: ("matter", "MatterHandler"),
        Protocol.ZWAVE: ("zwave", "ZwaveHandler"),
        Protocol.SHELLY: ("shelly", "ShellyHandler"),
        Protocol.TASMOTA: ("tasmota", "TasmotaHandler"),
    }

    def _handler(self, protocol: Protocol) -> ProtocolHandler:
        if protocol not in self._HANDLERS:
            raise ValueError("No protocol handler registered for {}".format(protocol.name))
        module_name, class_name = self._HANDLERS[protocol]
        module = __import__(
            "{}.{}".format(__name__, module_name), fromlist=[class_name]
        )
        handler_type = getattr(module, class_name)
        return handler_type()

    async def compile(
        self,
        protocol: Protocol,
        compiler: Any,
        config: dict,
        result: CompileResult,
        dry_run: bool,
    ) -> None:
        # ELI5: a handler can reject a binding (e.g. an unsafe group name or
        # a malformed IP). We turn that rejection into a per-binding error
        # message so one bad device does not abort the whole compile.
        try:
            await self._handler(protocol).compile(compiler, config, result, dry_run)
        except (ValueError, TypeError) as exc:
            result.success = False
            result.errors.append(
                "{}: {}".format(protocol.name, exc)
            )

    async def erase(self, compiler: Any, binding: BindingRecord) -> bool:
        aliases = {
            "zigbee_zha": Protocol.ZIGBEE_ZHA,
            "zigbee_z2m": Protocol.ZIGBEE_Z2M,
            "matter": Protocol.MATTER,
            "zwave": Protocol.ZWAVE,
            "shelly": Protocol.SHELLY,
            "tasmota": Protocol.TASMOTA,
        }
        protocol = aliases.get(binding.protocol.lower())
        if protocol is None:
            return False
        return await self._handler(protocol).erase(compiler, binding)


__all__ = [
    "BindingRecord",
    "CompileResult",
    "PROTOCOL_ALIASES",
    "Protocol",
    "ProtocolDispatcher",
    "ProtocolHandler",
]
