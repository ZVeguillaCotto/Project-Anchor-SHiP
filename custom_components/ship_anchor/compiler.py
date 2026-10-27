"""
SHiP v0.1 — Home Assistant compiler orchestration.

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
This is the coordinator: it reads Home Assistant automations, recognizes the
device ecosystem, asks that ecosystem's translator to build instructions, and
saves the results so they can later be undone.

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
AnchorCompiler normalizes automation data, validates HA-specific registry
requirements, dispatches to isolated protocol handlers, persists records, and
provides the shared HA/MQTT/WebSocket/HTTP transports.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from typing import Any

from .automation import parse_ha_automation_dict
from .protocols import (
    BindingRecord,
    CompileResult,
    PROTOCOL_ALIASES,
    Protocol,
    ProtocolDispatcher,
)
from .protocols.matter import (
    MATTER_CLUSTER_ACCESS_CONTROL,
    MATTER_CLUSTER_BINDING,
    MATTER_CLUSTER_LEVEL_CTRL,
    MATTER_CLUSTER_ONOFF,
    MATTER_CLUSTER_SCENES_MGMT,
    MATTER_CLUSTER_SWITCH,
    MATTER_WS_URL,
)
from .protocols.zwave import (
    ZWAVE_NONE,
    ZWAVE_S0_LEGACY,
    ZWAVE_S2_ACCESS_CONTROL,
    ZWAVE_S2_AUTHENTICATED,
    ZWAVE_S2_UNAUTHENTICATED,
    ZWAVE_SECURITY_RANK,
)
from .ship_protocol import (
    nonce_window_accept,
    nonce_window_state,
    nonce_windows_from_storage,
    nonce_windows_to_storage,
)

_LOGGER = logging.getLogger(__name__)

# ELI5 (v0.1.23 — Pillar 2): the plain-English label the options dashboard
# shows when a saved binding points at an entity that has since been renamed
# or deleted in Home Assistant.
DRIFT_LABEL = "Entity Missing / Renamed"


# ------------------------------------------------------------------
# Unit abstraction & translation (v0.1.16)
# ------------------------------------------------------------------
# ELI5: the wizard and the options dashboard let a non-engineer type
# *human* numbers — a colour temperature in **Kelvin** (2000K warm →
# 6500K cool, like the label on a bulb box) and a **brightness
# percentage** (0–100 %). But the hardware and the LEAT table speak
# *protocol* numbers: colour temperature in **mireds** (153–500) and
# brightness in a raw **0–255** byte.
#
# The canonical rule dict that lives on the config entry and that the
# LEAT compiler reads is ALWAYS in protocol units:
#     rule["brightness"]          → 0–255
#     rule["color_temp_mireds"] → 153–500
# That keeps the C struct `ship_leat_entry_t` and every compiler test
# (Layer 1) honest. These helpers are the single, pure, unit-tested
# bridge that turns the human units a user types into the protocol
# units the hardware expects — and back again for display.

# ELI5: the bulb-box range of white light a human is comfortable with.
# ELI5 (Fix G): the mireds and Kelvin ends are *different* scales, so they
# get *different* names. 153 mireds ~= 6536 K (the coolest/bluest practical
# white); 500 mireds ~= 2000 K (the warmest/most-orange). The old code
# labelled both ends 'KELVIN', which was simply wrong.
MIRED_MIN = 153        # coolest white - about 6536 K (daylight)
MIRED_MAX = 500        # warmest white - about 2000 K (candle)
KELVIN_MIN = 2000      # ~1000000 / MIRED_MAX - the warm end
KELVIN_MAX = 6536      # ~1000000 / MIRED_MIN - the cool end


def kelvin_to_mireds(kelvin: float) -> int:
    """ELI5: mireds are "millions of a Kelvin" — ``mireds = 1000000 / K``.

    A warm bulb (2700 K) is ~370 mireds; a cool daylight bulb (5000 K) is
     200 mireds. The result is clamped to the 153–500 range the LEAT frame
    parameter (a 16-bit ``param_2``) and the hardware understand.
     """
    kelvin = float(kelvin)
    if kelvin <= 0:
        # ELI5: 0 K or below is unphysical — snap to the warmest end.
        return MIRED_MAX
    mireds = round(1000000.0 / kelvin)
    return max(MIRED_MIN, min(MIRED_MAX, mireds))
def mireds_to_kelvin(mireds: int) -> int:
    """ELI5: the inverse — ``kelvin = 1000000 / mireds`` (rounded).

    Used only to show a human what a stored rule *means* (e.g. a rule
    stored at 350 mireds reads back as "~2857 K").
     """
    mireds = int(mireds)
    if mireds <= 0:
         # ELI5: 0 mireds is unphysical — snap to the coolest end.
        return KELVIN_MAX
    return int(round(1000000.0 / mireds))


def percent_to_brightness(percent: float) -> int:
    """ELI5: map a human 0–100 % dial to the device's 0–255 byte.

     50 % → 127 (we use the classic Home Assistant convention
      ``int(255 * percent / 100)`` — a proportional mapping truncated
      toward zero — so a *half* dial lands on 127), 100 % → 255, 0 % → 0.
     Out-of-range input is clamped so a user can't type a value the
    hardware can't honour.
      """
    percent = float(percent)
    percent = max(0.0, min(100.0, percent))
    return int(255 * percent / 100.0)


def brightness_to_percent(brightness: int) -> int:
    return int(round(brightness / 255.0 * 100))


def normalize_rule_units(rule: dict) -> dict:
    """ELI5: translate a rule that still carries *human* units into the
    *protocol* units the LEAT compiler and the C struct expect.

    This is the bridge the wizard and the options dashboard call right
    before a rule is stored. It accepts both human and protocol inputs
    so it is safe to run on an already-normalised rule:

       * ``color_temp_kelvin`` (a key, e.g. 2700) → ``color_temp_mireds``
         (370). A pre-existing ``color_temp_mireds`` key is left untouched.
       * ``brightness_percent`` (a key, e.g. 50) → ``brightness`` (127).
         A pre-existing 0–255 ``brightness`` key is left untouched, so the
         compiler's existing Layer-1 contract never changes.
     """
    out = dict(rule)
    # Colour temperature: prefer an explicit Kelvin key; else translate a
    # human "mireds" key only if it actually looks like a Kelvin value.
    if "color_temp_kelvin" in out and out["color_temp_kelvin"] is not None:
        out["color_temp_mireds"] = kelvin_to_mireds(out["color_temp_kelvin"])
        out.pop("color_temp_kelvin", None)
    # Brightness: an explicit percent key always wins; a bare 0–100 value
    # in "brightness" is a percent, a value already in 101–255 is a raw
    # byte and is kept as-is (back-compat with the compiler + tests).
    if "brightness_percent" in out and out["brightness_percent"] is not None:
        out["brightness"] = percent_to_brightness(out["brightness_percent"])
        out.pop("brightness_percent", None)
    elif "brightness" in out and out["brightness"] is not None:
        value = int(out["brightness"])
        if 0 <= value <= 100:
            # ELI5: a value in the percent range is almost certainly a
            # percentage, so translate it. (100 % → 255, the brightest.)
            out["brightness"] = percent_to_brightness(value)
    return out


class AnchorCompiler:
    """Coordinate automation processing, protocol dispatch, and persistence."""

    def __init__(self, hass: Any, store: Any, stored_data: dict) -> None:
        self._hass = hass
        self._store = store
        self._stored_data = stored_data
        self._dispatcher = ProtocolDispatcher()
        from .quirks_inspector import QuirksInspector

        self._inspector = QuirksInspector()

    async def async_compile(
        self, automation_ids: list[str], dry_run: bool = True
    ) -> CompileResult:
        result = CompileResult()
        automations = await self._fetch_automations(automation_ids)
        if not automations:
            result.warnings.append(
                "ELI5: No eligible automations found. Make sure your automations "
                "have a device trigger (like a button press) and a device action "
                "(like turning on a light)."
            )
            return result

        for auto_config in automations:
            await self._compile_automation(auto_config, result, dry_run)
        if not dry_run:
            self._stored_data.setdefault("bindings", []).extend(
                asdict(binding) for binding in result.bindings_added
            )
            self._ensure_nonce_windows()
            await self._store.async_save(self._stored_data)
        return result

    async def async_compile_config(
        self, config: dict, dry_run: bool = True
    ) -> CompileResult:
        """Compile one normalized config or Home Assistant automation."""
        result = CompileResult()
        parsed = parse_ha_automation_dict(
            config, self._get_device_registry(), self._get_entity_registry()
        )
        protocol = self._detect_protocol(parsed)
        if protocol == Protocol.UNKNOWN:
            result.success = False
            result.errors.append(
                "Unable to identify a supported protocol from this automation. "
                "Include protocol metadata or a device trigger/action domain."
            )
            return result
        parsed["protocol"] = protocol.name
        # ELI5: if the wizard collected gesture + time-window "rule sheets",
        # compile them into LEAT rows BEFORE dispatching the protocol
        # binding — so the hardware gets both the binding AND the cheat
        # sheet that decides what each tap does at each hour.
        rules = config.get("rules") or parsed.get("rules") or []
        if rules:
            leat = self.build_leat_table(rules)
            if leat:
                result.warnings.append(
                    "LEAT: compiled {} rule(s) into {} device row(s)".format(
                        len(rules), len(leat)
                    )
                )
                for row in leat:
                    result.dry_run_payloads.append({"step": "leat_write", "row": row})
            else:
                result.warnings.append(
                    "LEAT: no supported rules to compile (all gestures unknown)"
                )
        await self._compile_automation(parsed, result, dry_run)
        return result

    # ------------------------------------------------------------------
    # Multi-hop chain compilation  (v0.1.14)
    # ELI5: a physical chain like "switch → ceiling canopy → fan" needs the
    # gesture + action to travel through an intermediate module before it
    # reaches the load. We build the payload chain hop-by-hop, in order, so
    # the LEAT rows and the binding payloads line up with the real wiring.
    # ------------------------------------------------------------------
    def _hop_protocol(self, device: dict) -> Protocol:
        """ELI5: a hop's protocol is the one its *source* device speaks —
        a Z-Wave switch drives the first hop; a Zigbee canopy drives the
        second. Unknown protocols are tolerated: the per-hop compile just
        reports an error and the remaining hops still produce a preview."""
        for identifier in (device.get("identifiers") or []):
            if not isinstance(identifier, (tuple, list)) or not identifier:
                continue
            namespace = str(identifier[0]).upper()
            if namespace in PROTOCOL_ALIASES:
                return PROTOCOL_ALIASES[namespace]
        return Protocol.UNKNOWN

    def _hop_source_config(self, device: dict) -> dict:
        """ELI5: map a wizard device entry to the config keys each protocol
        handler expects. For bus protocols (ZHA/Z2M/Matter/Z-Wave) the IEEE
        / node id is what matters; for IP protocols (Shelly/Tasmota) the IP
        address or a device name is the fallback."""
        ieee = str(device.get("ieee") or "")
        model = str(device.get("model") or "")
        manufacturer = str(device.get("manufacturer") or "")
        identifier_value = ""
        identifiers = device.get("identifiers") or []
        if identifiers:
            first = identifiers[0]
            if isinstance(first, (tuple, list)) and len(first) > 1:
                identifier_value = str(first[1])
        return {
            "source_ieee": ieee,
            "source_model": model,
            "source_manufacturer": manufacturer,
            "shelly_ip": identifier_value,
            "controller_ip": identifier_value,
            "group_name": str(device.get("device_id") or "anchor_grp_1"),
        }

    async def async_compile_multihop(
        self,
        chain: list[dict],
        rules: list[dict] | None = None,
        dry_run: bool = True,
        group_name: str = "",
    ) -> CompileResult:
        """
        ELI5: compile a "chain" of devices — e.g. a switch, an optional
        intermediate canopy module, and a target load — into a single set of
        payloads, one per hop, that can be replayed in order.

        `chain` is a list of wizard device entries (as produced by the
        wizard), in the order the gesture should travel through them.
        `rules` are LEAT rule sheets; they are compiled once and attached
        to every hop so the LEAT rows are identical at each stage of the
        chain.
        """
        result = CompileResult()
        if not chain:
            result.success = False
            result.errors.append(
                  "ELI5: no hops to compile — pass at least one device in the chain."
              )
            return result

            # ELI5 (v0.1.18): a linear chain is just a DAG where every
            # node has exactly one child, so we translate it into
            # adjacent (parent, child) edges and delegate to the DAG
            # compiler. This keeps the legacy linear semantics without
            # duplicating the per-hop logic.
        edges = [
             (chain[index], chain[index + 1])
             for index in range(len(chain) - 1)
         ]
        if not edges:
             # A single-device 'chain' has no hop — success stays True
             # with nothing to do, matching the pre-v0.1.18 behaviour.
            result.warnings.append(
                 "Multi-hop chain compiled: 0 hop(s) across {} device(s)".format(
                    len(chain)
                 )
             )
            return result
        return await self.async_compile_dag(
            edges, rules=rules, dry_run=dry_run, group_name=group_name
         )
    # ------------------------------------------------------------------
    # Multi-Zone Virtual Grouping / DAG routing     (v0.1.18 — Pillar 2)
    # ELI5: a real mesh is not a single line of devices — it *branches*. One
    #        ceiling canopy can feed a whole room (a light, a fan, a valve),
    #        or several canopies can feed the same zone. The compiler now
    #        understands a **DAG** (a directed acyclic graph): a set of
    #        parent->child edges. A one-to-many fan-out — one source driving
    #        many children — is exactly the multi-zone "virtual grouping" the
    #        wizard collects.
    # ------------------------------------------------------------------
    @staticmethod
    def _normalize_dag_edges(dag: Any) -> tuple[list[tuple[dict, dict]], list[str]]:
        """ELI5: turn the ways a user might *describe* a mesh into one
        canonical edge list of ``(parent, child)`` device pairs.

        Accepted shapes:
             * a list of ``[parent, child]`` / ``{parent, child}`` /
               ``{source, target}`` edge descriptors;
             * a list of node dicts each carrying a ``children``/``targets``
              list;
             * an adjacency mapping ``{parent: [child, ...]}``.

        Returns ``(edges, warnings)``; an empty graph with no usable device
        is reported as an error string so the caller can surface it honestly.
        """
        warnings: list[str] = []

        def _is_adjacency(mapping: Any) -> bool:
            return isinstance(mapping, dict) and bool(mapping) and all(
                isinstance(v, (list, tuple)) for v in mapping.values()
            )

        raw_edges: list[tuple[dict, dict]] = []
        if _is_adjacency(dag):
            # ELI5: keys are parent device ids; values are the children each
            # parent drives. Resolve each to a device dict.
            for parent_key, children in dag.items():
                parent = {"device_id": parent_key, "identifiers": []}
                for child in children:
                    child_entry = child if isinstance(child, dict) else {
                        "device_id": child, "identifiers": [],
                    }
                    raw_edges.append((parent, child_entry))
            warnings.append(
                "DAG: read adjacency mapping with {} parent node(s).".format(len(dag))
            )
        elif isinstance(dag, (list, tuple)):
            for item in dag:
                if isinstance(item, dict) and ("children" in item or "targets" in item):
                    # ELI5: a node dict that lists the children it drives.
                    for child in (item.get("children") or item.get("targets") or []):
                        child_entry = child if isinstance(child, dict) else {
                            "device_id": child, "identifiers": [],
                        }
                        raw_edges.append((item, child_entry))
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    # ELI5: a 2-tuple edge — the simplest "parent -> child".
                    raw_edges.append((item[0], item[1]))
                elif isinstance(item, dict) and ("parent" in item or "source" in item):
                    # ELI5: an edge-descriptor dict.
                    parent = item.get("parent") or item.get("source")
                    child = item.get("child") or item.get("target")
                    if parent is None or child is None:
                        continue
                    parent_entry = parent if isinstance(parent, dict) else {
                        "device_id": parent, "identifiers": [],
                    }
                    child_entry = child if isinstance(child, dict) else {
                        "device_id": child, "identifiers": [],
                    }
                    raw_edges.append((parent_entry, child_entry))

        if not raw_edges:
            return [], [
                "ELI5: no routing edges found — pass at least one "
                "parent->child hop in the mesh."
            ]
        return raw_edges, warnings

    @staticmethod
    def _hop_next_value(target: dict) -> str:
        """ELI5: the value an IP-protocol leg uses to reach its next hop —
        the child device's IP, or its IEEE as a fallback."""
        identifiers = target.get("identifiers") or []
        if identifiers and isinstance(identifiers[0], (tuple, list)) and len(identifiers[0]) > 1:
            return str(identifiers[0][1])
        return ""

    @staticmethod
    def _dag_topo_order(
        edges: list[tuple[dict, dict]],
    ) -> tuple[list[tuple[dict, dict]], list[str]]:
        """ELI5: order the edges so every parent compiles before its
        children (a depth-first walk over the edge tree).

        Returns ``(ordered_edges, cycle_warnings)``. A routing cycle — a
        device that routes back to its own ancestor, physically impossible in
        a real mesh — is dropped with an honest warning rather than looping
        forever.
        """
        def _key(device: dict) -> str:
            return str(device.get("device_id") or device.get("name") or id(device))

# ELI5 (Fix B): keep *every* parent of a node, not just the first one
        # `setdefault` remembered. A diamond (two devices feeding one child) is
        # perfectly legal — the child must wait for BOTH parents to compile.
        parents_of: dict[str, list[str]] = {}
        children_of: dict[str, list[str]] = {}
        edge_by_pair: dict[tuple[str, str], tuple[dict, dict]] = {}
        all_keys: set[str] = set()

        for parent, child in edges:
            p_key = _key(parent)
            c_key = _key(child)
            all_keys.add(p_key)
            all_keys.add(c_key)
            pair = (p_key, c_key)
            if pair not in edge_by_pair:
                edge_by_pair[pair] = (parent, child)
                children_of.setdefault(p_key, []).append(c_key)
                parents_of.setdefault(c_key, []).append(p_key)

        cycle_warnings: list[str] = []
        ordered: list[tuple[dict, dict]] = []

         # ELI5 (Fix B): Kahn's algorithm topsort. A node only drains when ALL
         # of its parents have drained, so a diamond (a child with two parents)
         # waits for both and is emitted exactly twice — no spurious cycle.
        indegree = {k: len(parents_of.get(k, [])) for k in all_keys}
        ready = sorted(k for k in all_keys if indegree[k] == 0)
        processed: set[str] = set()
        while ready:
            node = ready.pop(0)
            processed.add(node)
             # ELI5: emit every edge INTO this node now — its parents are all
             # already done, so each incoming edge is in order.
            for p_key in sorted(parents_of.get(node, [])):
                ordered.append(edge_by_pair[(p_key, node)])
            for c_key in sorted(children_of.get(node, [])):
                indegree[c_key] -= 1
                if indegree[c_key] == 0:
                    ready.append(c_key)

         # ELI5: any node not drained by Kahn still has an unresolved parent —
         # a genuine routing cycle (a device routing back to its own ancestor).
         # Emit its remaining edges so the user still previews every branch,
         # and warn once, honestly.
        if len(processed) < len(all_keys):
             # ELI5 (Fix B): detect only TRUE back-edge cycles with white/gray/
             # black DFS colouring, so a diamond never counts as a cycle.
            WHITE, GRAY, BLACK = 0, 1, 2
            color = {k: WHITE for k in all_keys}
            found_cycle = False

            def dfs(start: str) -> None:
                nonlocal found_cycle
                stack = [(start, 0)]
                while stack:
                    node, i = stack[-1]
                    color[node] = GRAY
                    kids = children_of.get(node, [])
                    if i < len(kids):
                        stack[-1] = (node, i + 1)
                        nxt = kids[i]
                        if color[nxt] == GRAY:
                            found_cycle = True
                        elif color[nxt] == WHITE:
                            stack.append((nxt, 0))
                    else:
                        color[node] = BLACK
                        stack.pop()

            for key in sorted(all_keys - processed):
                if color[key] == WHITE:
                    dfs(key)
            if found_cycle:
                cycle_warnings.append(
                     "ELI5: a routing cycle was detected and the looped "
                     "edge(s) dropped — a device cannot route back to its "
                     "own ancestor."
                 )
             # ELI5: emit the cycle-remnant edges so nothing is silently lost.
            emitted = set((_key(p), _key(c)) for p, c in ordered)
            for pair, (parent, child) in edge_by_pair.items():
                if pair not in emitted:
                    ordered.append((parent, child))
                    emitted.add(pair)
        return ordered, cycle_warnings

    async def async_compile_dag(
        self,
        dag: Any,
        rules: list[dict] | None = None,
        dry_run: bool = True,
        group_name: str = "",
    ) -> CompileResult:
        """
        ELI5: compile a **mesh** described as a DAG of parent->child edges --
        the multi-zone, one-to-many 'virtual grouping' a real ceiling-canopy
        mesh is. A linear chain is just a DAG where every node has one child,
        so this is the superset of ``async_compile_multihop``.
        
        Each hop (one edge) is compiled exactly like a single-hop binding,
        tagged with its `hop_index` and `zone` (the child device's id, so a
        one-to-many fan-out lands in the right zone). LEAT rows are compiled
        once and attached up front, shared by every hop. A broken hop is
        reported but does *not* abort the whole mesh -- the remaining branches
        still preview, which is the whole point of a virtual group: one
        severed zone must not blind the others (Finding A).
        """
        result = CompileResult()
        raw_edges, warnings = self._normalize_dag_edges(dag)
        result.warnings.extend(warnings)
        if not raw_edges:
            result.success = False
            for warning in warnings:
                result.errors.append(warning)
            return result
        # ELI5: order the branches so parents compile before their children, and
        # drop any physical impossibility (a routing cycle).
        edges, cycle_warnings = self._dag_topo_order(raw_edges)
        result.warnings.extend(cycle_warnings)
        # LEAT rows are compiled once up front so every hop carries the same
        # cheat-sheet -- shared at frame-encode time across the mesh.
        if rules:
            compiled_leat = self.build_leat_table(rules)
            if compiled_leat:
                result.warnings.append(
                    "LEAT: compiled {} rule(s) into {} device row(s) "
                    "(shared across every DAG branch)".format(
                        len(rules), len(compiled_leat)
                    )
                )
                for row in compiled_leat:
                    # ELI5 (Fix F): LEAT rows are *shared* cheat-sheets for the whole
                    # mesh, so tag them zone='shared' and stamp a hop_index up front.
                    # That stops the per-hop untagged filter from sweeping them into
                    # the first branch's zone (the old bug: rows mislabelled as zone-1).
                    result.dry_run_payloads.append(
                        {"step": "leat_write", "row": row,
                         "hop_index": 0, "zone": "shared"}
                    )
        node_keys: set[str] = set()
        for index, (parent, child) in enumerate(edges, start=1):
            p_key = str(parent.get("device_id") or parent.get("name") or id(parent))
            zone = str(child.get("device_id") or child.get("name") or "zone_{}".format(index))
            node_keys.add(p_key)
            node_keys.add(zone)
            hop_config: dict[str, Any] = dict(self._hop_source_config(parent))
            hop_config["targets"] = [dict(child)]
            # ELI5: IP-protocol legs address the next hop by its IP/IEEE.
            next_value = self._hop_next_value(child)
            if next_value:
                hop_config.setdefault("target_ip", next_value)
                hop_config.setdefault("target_ips", [next_value])
                hop_config.setdefault("next_hop_ip", next_value)
            if group_name:
                hop_config["group_name"] = group_name
            hop_protocol = self._hop_protocol(parent)
            hop_config["protocol"] = hop_protocol.name
            try:
                parsed = parse_ha_automation_dict(
                    hop_config, self._get_device_registry(),
                    self._get_entity_registry(),
                )
            except Exception as exc:        # noqa: BLE001
                # ELI5: one bad branch must not blind the others -- record it and
                # keep going. This is the resilience a virtual group promises.
                result.success = False
                result.errors.append(
                    "Hop {} -> {} (zone {!r}): {}".format(index, index + 1, zone, exc)
                )
                continue
            parsed["protocol"] = hop_protocol.name
            # ELI5 (Fix A): snapshot success *before* this hop compiles so we can
            # tell whether *this* branch flipped it to False. A non-throwing
            # guardrail failure (e.g. a Z-Wave Long Range block, a ZHA IEEE
            # guardrail) must report and keep going, NOT `return` and blind the
            # later branches.
            was_success = result.success
            await self._compile_automation(parsed, result, dry_run)
            # ELI5: tag only the payloads *this* hop produced so per-zone
            # grouping is correct even when one branch fans out. Always tag --
            # even after an earlier branch failed -- the zone data must stay
            # accurate.
            untagged = [
                p for p in result.dry_run_payloads
                if isinstance(p, dict) and "hop_index" not in p
            ]
            for payload in untagged:
                payload["hop_index"] = index
                payload["zone"] = zone
            if was_success and not result.success:
                result.errors.append(
                    "ELI5: branch at hop {} -> {} (zone {!r}) hit a "
                    "guardrail; the other branches still preview.".format(
                        index, index + 1, zone,
                    )
                )
        # ELI5 (Fix A): if any branch hit a guardrail, surface one honest
        # summary -- '1 branch failed, the mesh still previews' -- so the
        # caller knows the mesh is partial, not silent.
        if not result.success:
            result.warnings.append(
                "ELI5: one or more branches hit a guardrail and were "
                "skipped; the remaining branches still previewed."
            )
        # ELI5: summary line so the wizard can show '3 branches, 2 rules'.
        result.warnings.append(
             "Multi-zone mesh compiled: {} branch(es) across {} node(s)".format(
                len(edges), len(node_keys)
             )
        )
        return result
    _GESTURE_TO_NIBBLE = {
        "1x Tap": 1, "2x Tap": 2, "3x Tap": 3, "4x Tap": 4,
        "5x Tap": 5, "Hold": 6,
    }
    # ELI5: which domain nibble a rule "belongs to" depends on what the
    # rule does. A brightness rule is a lighting-combo domain (0x2); a
    # plain on/off rule is OnOff (0x1); valve rules are Water (0x4);
    # lock rules are Smart Lock (0x5). This mirrors ship_domain_t.
    @staticmethod
    def _rule_domain(rule: dict) -> int:
        if "color_temp_mireds" in rule or "brightness" in rule:
            return 0x2  # SHIP_DOMAIN_LIGHTING_COMBO
        if "valve_open" in rule:
            return 0x4  # SHIP_DOMAIN_WATER_VALVE
        if "lock_unlock" in rule:
            return 0x5  # SHIP_DOMAIN_SMART_LOCK
        if "setpoint_c" in rule:
            return 0x6  # SHIP_DOMAIN_HVAC_CLIMATE
        if "fan_speed" in rule:
            return 0x6  # SHIP_DOMAIN_HVAC_CLIMATE (fan speed param_2)
        return 0x1      # SHIP_DOMAIN_ONOFF (plain on/off)

    @staticmethod
    def _rule_action(rule: dict) -> tuple[int, int, dict]:
        """
        ELI5: pack a rule's action for the LEAT row.

        The C struct (`ship_leat_entry_t`) stores exactly ONE action byte
        (`action_param`) per rule, plus an `action_type` tag (0=relay,
        1=brightness, 2=scene, 3=valve, 4=climate). Richer values that do
        not fit in one byte (colour temperature in mireds is 153–500!) are
        NOT invented into the struct — they are carried in the
        `frame_params` annotation, which the frame encoder uses when the
        19-byte packet is actually sent (frame param_1/param_2/param_3).

        Returns (action_type, action_param 0–255, frame_params annotation).
        """
        frame_params: dict = {}
        if "valve_open" in rule:
            return 3, (1 if rule["valve_open"] else 0), frame_params
        if "lock_unlock" in rule:
            return 0, (1 if rule["lock_unlock"] else 0), frame_params
        if "setpoint_c" in rule:
            # ELI5: °C × 2 to keep one decimal of precision inside a byte.
            return 4, int(round(float(rule["setpoint_c"]) * 2)) & 0xFF, frame_params
        if "fan_speed" in rule:
            # ELI5: fan speed 0–5 fits easily in one byte.
            return 4, int(rule["fan_speed"]) & 0xFF, frame_params
        brightness = int(rule.get("brightness", 1))
        # ELI5: brightness (0–255) IS the LEAT action byte. Colour temp and
        # fade time only fit in the *frame* parameters — keep them there.
        if "color_temp_mireds" in rule:
            frame_params["param_1"] = brightness & 0xFF
            frame_params["param_2"] = int(rule["color_temp_mireds"]) & 0xFFFF
        if "transition_seconds" in rule:
            frame_params["param_3"] = min(255, int(rule["transition_seconds"]) * 10)
        if frame_params:
            return 1, brightness & 0xFF, frame_params
        return 0, (1 if brightness > 0 else 0), frame_params

    def build_leat_table(
        self, rules: list[dict], group_id: int = 0
    ) -> list[dict]:
        """
        ELI5: turn the wizard's rule list into LEAT rows that exactly
        match `ship_leat_entry_t` (ship_protocol.h):
          {group_id, domain, gesture, action_type, action_param,
           hour_start, hour_end, enabled}
        Values that need more than one byte (colour temperature in mireds,
        fade time) are NOT forced into the struct — they ride along in an
        optional `frame_params` annotation for the 19-byte frame encoder.
        """
        table: list[dict] = []
        for rule in rules:
            gesture_label = str(rule.get("gesture", ""))
            gesture = self._GESTURE_TO_NIBBLE.get(gesture_label)
            if gesture is None:
                # ELI5: a row with a gesture we don't support is skipped,
                # not fatal — the remaining rules still compile.
                _LOGGER.debug(
                    "LEAT: skipping rule with unknown gesture %r", gesture_label
                )
                continue
            domain = self._rule_domain(rule)
            action_type, action_param, frame_params = self._rule_action(rule)
            hour_start = int(rule.get("hour_start", 0) or 0) % 24
            hour_end = int(rule.get("hour_end", 0) or 0) % 24
            # ELI5: the row mirrors `ship_leat_entry_t` field-for-field so
            # the ESPHome component can memcpy it straight into the table.
            row = {
                "group_id": int(group_id) & 0xFF,
                "domain": domain,
                "gesture": gesture,
                "action_type": action_type,
                "action_param": action_param & 0xFF,
                "hour_start": hour_start,
                "hour_end": hour_end,
                "enabled": True,
            }
            # ELI5: optional annotation for the 19-byte frame encoder
            # (colour temp / fade time). Not part of the C LEAT struct.
            if frame_params:
                row["frame_params"] = frame_params
            table.append(row)
        return table

    def list_bindings(self) -> list[BindingRecord]:
        """
        ELI5: return every binding that SHiP Anchor has previously saved
        (whether from a wizard run or a service call). Used by the
        options flow to let the user *see* what is currently baked in.
        """
        out: list[BindingRecord] = []
        for raw in self._stored_data.get("bindings", []):
            try:
                out.append(BindingRecord(**{
                    key: value for key, value in raw.items()
                    if key in BindingRecord.__dataclass_fields__
                }))
            except (TypeError, ValueError):
                _LOGGER.warning("Skipping malformed binding record: %s", raw)
        return out

    # ------------------------------------------------------------------
    # Replay-window nonce persistence    (v0.1.23 — Pillar 4)
    # ELI5: every SHiP frame carries a little "nonce" counter that only
    # ever goes up, so a recorded frame replayed later can't fool a
    # device. To make that protection survive a Home Assistant reboot we
    # remember, per group, the *sliding window* of the most recent
    # nonces each hop has accepted, and we store it right alongside the
    # bindings in .storage/ship_anchor.bindings. These helpers are the
    # bridge between the persistent blob and the pure, unit-tested
    # ship_protocol helpers.
    # ------------------------------------------------------------------
    def load_nonce_windows(self) -> dict:
        """ELI5: read the anti-replay windows that survived the last
        reboot out of the persistent store, so a replay attempt that
        straddled a restart is still caught. A missing or malformed blob
        degrades to an empty dict (a first-ever start-up), never a crash."""
        return nonce_windows_from_storage(self._stored_data)

    def save_nonce_windows(self, windows: dict) -> None:
        """ELI5: write the anti-replay windows back into the store so
        they survive the next reboot. Returns the updated store dict."""
        nonce_windows_to_storage(self._stored_data, windows)

    async def persist_nonce_windows(self, windows: dict) -> dict:
        """ELI5: save the windows and flush them to disk in one step,
        so the protection is on-disk before the next reboot. Returns the
        updated windows mapping."""
        nonce_windows_to_storage(self._stored_data, windows)
        await self._store.async_save(self._stored_data)
        return windows

    async def record_nonce(self, group_id: int, counter: int,
                           now_ms: int = 0) -> dict:
        """ELI5: record that a group has just heard a fresh nonce, and
        persist the sliding window. Mirrors the C core's anti-replay
        counter check so the protection is identical on hardware and on
        the pure-Python engine."""
        windows = self.load_nonce_windows()
        window = windows.get(int(group_id) & 0xFFFF,
                             nonce_window_state(group_id))
        window = nonce_window_accept(window, counter, now_ms)
        windows[int(group_id) & 0xFFFF] = window
        await self.persist_nonce_windows(windows)
        return window

    def _ensure_nonce_windows(self) -> None:
        """ELI5: make sure the anti-replay windows key
        and well-formed right before we flush the store, so a binding-only
        save can never accidentally drop the replay guard a reboot must
        survive. A no-op round-trip when the key is already a clean dict."""
        nonce_windows_to_storage(
            self._stored_data, nonce_windows_from_storage(self._stored_data)
        )
# ------------------------------------------------------------------
# Entity-registry drift detection     (v0.1.23 — Pillar 2)
# ELI5: when the integration starts up we cross-check every saved
# binding against Home Assistant's entity registry. If a binding
# points at an entity that has since been *renamed* or *deleted*, we
# flag it "Entity Missing / Renamed" so the dashboard can tell the
# user, in plain English, that this one binding needs attention.
# ------------------------------------------------------------------
    def detect_binding_drift(self) -> list[dict]:
        """ELI5: cross-reference saved binding records against the live
        entity registry and return one plain-English report row per
        binding whose source or target entity no longer resolves.
        
        Each row is ``{"binding": <dict>, "drifted": bool, "missing":
        [entity_id, ...], "reason": <ELI5 string>}``. When the registry is
        unavailable (e.g. a unit test that stubs ``entity_registry.
        async_get`` to return ``None``) every row is reported with
        ``drifted=False`` so start-up never blocks on a registry that
        simply hasn't loaded yet.
        """
        registry = self._get_entity_registry()
        bindings = self.list_bindings()
        reports: list[dict] = []
        for binding in bindings:
            row: dict[str, Any] = {
                 "binding": {
                      "protocol": binding.protocol,
                      "source_ieee": binding.source_ieee,
                      "target_ieee": binding.target_ieee,
                      "group_id": binding.group_id,
                      "clusters": list(binding.clusters),
                  },
                 "drifted": False,
                 "missing": [],
                 "reason": "",
             }
            if registry is None:
                 # ELI5: the registry hasn't loaded yet — a healthy no-op
                 # rather than a false alarm.
                reports.append(row)
                continue
            entity_ids = self._binding_entity_ids(binding)
            missing = [
                 entity_id for entity_id in entity_ids
                 if self._entity_exists(registry, entity_id) is False
             ]
            if missing:
                row["drifted"] = True
                row["missing"] = missing
                row["reason"] = (
                    "ELI5: {label} — a device this binding controls was "
                    "renamed or removed in Home Assistant. Open the "
                    "dashboard to re-point or remove it.".format(
                        label=DRIFT_LABEL
                    )
                )
                _LOGGER.warning(
                    "SHiP Anchor: binding %s → %s drifted: missing %s",
                    binding.source_ieee, binding.target_ieee, missing,
                )
            reports.append(row)
        return reports

    @staticmethod
    def _binding_entity_ids(binding: BindingRecord) -> list[str]:
        """ELI5: the entity ids a binding depends on — derived from its
        IEEE identifiers so a rename in the registry is detected."""
        ids: list[str] = []
        for ieee in (binding.source_ieee, binding.target_ieee):
            if ieee:
                ids.append(str(ieee))
        return ids

    @staticmethod
    def _entity_exists(registry: Any, entity_id: str) -> bool:
        """ELI5: does the live registry still know about this id?

        Home Assistant's real registry exposes ``.entities`` (a mapping of
        entity_id → entry) and/or ``.async_get(entity_id)``. We support
        either so the same code runs against the real registry and the
        test fakes.
        """
        async_get = getattr(registry, "async_get", None)
        if callable(async_get):
            entry = async_get(entity_id)
            return entry is not None
        entities = getattr(registry, "entities", None)
        if entities is not None:
            return entity_id in entities
        return True

    async def async_erase_binding(
        self, source_ieee: str, target_ieee: str, protocol: str
    ) -> bool:
        """
        ELI5: remove one specific binding (matched by source IEEE +
        target IEEE + protocol) from the persistent store. If the protocol
        handler supports an undo, call it first so the device itself is
        reset. Returns True if the record was removed, False if it was
        not found.
        """
        key = (protocol.lower(), source_ieee.lower(), target_ieee.lower())
        remaining = []
        removed: BindingRecord | None = None
        for raw in self._stored_data.get("bindings", []):
            try:
                binding = BindingRecord(**{
                    k: v for k, v in raw.items()
                    if k in BindingRecord.__dataclass_fields__
                })
            except (TypeError, ValueError):
                remaining.append(raw)
                continue
            if (
                binding.protocol.lower() == key[0]
                and binding.source_ieee.lower() == key[1]
                and binding.target_ieee.lower() == key[2]
            ):
                removed = binding
                continue
            remaining.append(raw)
        if removed is None:
            return False
        # Attempt a protocol-specific undo (e.g. ZHA unbind, Matter
        # AccessControl clear). If the protocol has no undo, we still
        # remove the local record so the UI reflects the new state.
        try:
            await self._erase_binding(removed)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Protocol erase hook failed for %s", removed)
        self._stored_data["bindings"] = remaining
         # ELI5 (v0.1.23 — Pillar 4): keep the replay guard present so
         # erasing a binding never drops a group's anti-replay window.
        self._ensure_nonce_windows()
        await self._store.async_save(self._stored_data)
        _LOGGER.info(
            "SHiP Anchor: erased binding %s %s → %s",
            removed.protocol, removed.source_ieee, removed.target_ieee,
        )
        return True

    async def async_erase_all(self) -> None:
        """Remove all offline bindings previously programmed.

        ELI5: "Erase everything" is a user action, not a per-device
        negotiation. We try a best-effort device reset for each saved
        binding, but ANY per-binding problem (unsupported protocol,
        malformed record, device offline, transport error) must NOT
        prevent the binding list from being cleared. Previously a single
        failing undo raised here, so the store was never saved and the
        user saw a long traceback instead of a clean success. Now the
        list is always cleared and saved; per-binding problems are only
        reported as warnings.
        """
        bindings_raw = list(self._stored_data.get("bindings", []))
        skipped = 0
        for raw in bindings_raw:
            try:
                binding = BindingRecord(**{
                    key: value for key, value in raw.items()
                    if key in BindingRecord.__dataclass_fields__
                })
                supported = await self._erase_binding(binding)
            except Exception as exc:  # noqa: BLE001 — erase must be resilient
                _LOGGER.warning(
                    "Erase: best-effort device reset failed for %s: %s",
                    raw if isinstance(raw, dict) else type(raw).__name__,
                    exc,
                )
                supported = False
            if not supported:
                skipped += 1
                _LOGGER.warning(
                    "Erase: no device-side undo available for binding %s "
                    "(record removed from the local store).",
                    raw if isinstance(raw, dict) else type(raw).__name__,
                )
        # ELI5: the local record is cleared unconditionally — that is what
        # "erase all" means to the user and to the storage file.
        self._stored_data["bindings"] = []
          # ELI5 (v0.1.23 — Pillar 4): erasing everything also clears the
          # anti-replay windows, since no group is bound anymore.
        nonce_windows_to_storage(self._stored_data, {})
        await self._store.async_save(self._stored_data)
        if skipped:
            _LOGGER.warning(
                "SHiP Anchor: all %d offline binding(s) erased from the local "
                "store (%d had no supported device-side undo).",
                len(bindings_raw), skipped,
            )
        else:
            _LOGGER.warning(
                "SHiP Anchor: all %d offline binding(s) erased.",
                len(bindings_raw),
            )

    async def _fetch_automations(self, ids: list[str]) -> list[dict]:
        """Retrieve configured automations from Home Assistant."""
        if self._hass is None or not hasattr(self._hass, "config"):
            return []
        try:
            from homeassistant.util.yaml import load_yaml  # type: ignore[import]
            path = self._hass.config.path("automations.yaml")
        except ImportError as exc:
            raise RuntimeError("Home Assistant YAML loader is unavailable") from exc

        configs = await asyncio.to_thread(load_yaml, path)
        if isinstance(configs, dict):
            configs = [configs]
        if not isinstance(configs, list):
            raise ValueError(
                "automations.yaml must contain a list of automation dictionaries"
            )
        selected_ids = set(ids)
        states = self._hass.states.async_all("automation")
        device_registry = self._get_device_registry()
        entity_registry = self._get_entity_registry()
        results = []
        for config in configs:
            if not isinstance(config, dict):
                continue
            automation_id = str(config.get("id", ""))
            alias = str(config.get("alias", ""))
            state_id_match = any(
                state.object_id in selected_ids
                and (
                    str(state.attributes.get("id", "")) == automation_id
                    or str(state.name) == alias
                )
                for state in states
            )
            if selected_ids and not (
                automation_id in selected_ids
                or alias in selected_ids
                or state_id_match
            ):
                continue
            results.append(
                parse_ha_automation_dict(config, device_registry, entity_registry)
            )
        return results

    def _get_device_registry(self) -> Any:
        if self._hass is None:
            return None
        try:
            from homeassistant.helpers import device_registry  # type: ignore[import]
        except ImportError:
            return None
        return device_registry.async_get(self._hass)

    def _get_entity_registry(self) -> Any:
        if self._hass is None:
            return None
        try:
            from homeassistant.helpers import entity_registry  # type: ignore[import]
        except ImportError:
            return None
        return entity_registry.async_get(self._hass)

    async def _compile_automation(
        self, auto_config: dict, result: CompileResult, dry_run: bool
    ) -> None:
        """Normalize one automation, fan out targets, then dispatch handlers."""
        config = parse_ha_automation_dict(
            auto_config, self._get_device_registry(), self._get_entity_registry()
        )
        protocol = self._detect_protocol(config)
        config["protocol"] = protocol.name
        automation_label = str(
            auto_config.get("alias")
            or auto_config.get("id")
            or auto_config.get("automation_id")
            or "unknown"
        )
        _LOGGER.debug(
            "SHiP Anchor: compiling automation '%s' → protocol=%s",
            automation_label,
            protocol.name,
        )
        has_ha_steps = any(
            key in config for key in ("trigger", "triggers", "action", "actions")
        )

        if protocol == Protocol.MATTER and has_ha_steps:
            targets = config.get("targets", [])
            if (
                config.get("source_node_id") is None
                or not targets
                or any(target.get("node_id") is None for target in targets)
            ):
                result.success = False
                result.errors.append(
                    "Matter automation needs source and target Node IDs from the "
                    "device registry before bindings can be compiled."
                )
                return
            for target in targets:
                target_config = dict(config)
                target_config["target_node_id"] = target["node_id"]
                target_config["target_endpoint"] = target.get(
                    "endpoint", config.get("target_endpoint", 1)
                )
                await self._dispatcher.compile(
                    protocol, self, target_config, result, dry_run
                )
            return

        if protocol == Protocol.ZWAVE and has_ha_steps:
            targets = config.get("targets", [])
            if (
                config.get("source_node_id") is None
                or not config.get("source_security")
                or not targets
                or any(
                    target.get("node_id") is None
                    or not (
                        target.get("security_class")
                        or target.get("security")
                        or config.get("target_security")
                    )
                    for target in targets
                )
            ):
                result.success = False
                result.errors.append(
                    "Z-Wave automation needs source/target node IDs and matching "
                    "security classes before direct associations can be compiled."
                )
                return
            for target in targets:
                target_config = dict(config)
                target_config["target_node_id"] = target["node_id"]
                target_config["target_security"] = (
                    target.get("security_class")
                    or target.get("security")
                    or config["target_security"]
                )
                await self._dispatcher.compile(
                    protocol, self, target_config, result, dry_run
                )
            return

        if protocol == Protocol.UNKNOWN:
            result.success = False
            result.errors.append(
                "Automation '{}' — protocol could not be detected. "
                "Use a device trigger with a supported domain (zha, zwave_js, "
                "matter, shelly, tasmota, zigbee2mqtt), or add an explicit "
                "'protocol:' key to the automation.".format(automation_label)
            )
            return
        await self._dispatcher.compile(protocol, self, config, result, dry_run)

    def _detect_protocol(self, config: dict) -> Protocol:
        parsed = parse_ha_automation_dict(
            config, self._get_device_registry(), self._get_entity_registry()
        )
        return PROTOCOL_ALIASES.get(
            str(parsed.get("protocol", "")).upper(), Protocol.UNKNOWN
        )

    async def _erase_binding(self, binding: BindingRecord) -> bool:
        return await self._dispatcher.erase(self, binding)

    async def _hass_service_call(self, service: str, data: dict) -> None:
        if not self._hass:
            raise RuntimeError("Home Assistant is unavailable for service calls")
        domain, service_name = service.split(".", 1)
        await self._hass.services.async_call(domain, service_name, data)

    async def _mqtt_publish(self, topic: str, payload: str) -> None:
        if not self._hass:
            raise RuntimeError("Home Assistant is unavailable for MQTT publishing")
        from homeassistant.components import mqtt  # type: ignore[import]

        await mqtt.async_publish(self._hass, topic, payload, qos=1)

    async def _matter_ws_send(self, message: dict) -> None:
        import websockets  # type: ignore[import]

        async with websockets.connect(MATTER_WS_URL, open_timeout=5) as ws:
            await ws.send(json.dumps(message))
            response = await asyncio.wait_for(ws.recv(), timeout=10)
            _LOGGER.debug("Matter WS response: %s", response)

    async def _http_post_json(self, url: str, body: dict) -> None:
        import aiohttp  # type: ignore[import]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=body, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                response.raise_for_status()

    async def _http_get_params(self, url: str, params: dict) -> None:
        import aiohttp  # type: ignore[import]

        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                response.raise_for_status()


__all__ = [
    "AnchorCompiler",
    "BindingRecord",
    "CompileResult",
    "Protocol",
    "parse_ha_automation_dict",
]
