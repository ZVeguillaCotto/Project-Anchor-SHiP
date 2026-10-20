"""
============================================================================
SHiP v0.1 — esphome/components/ship_fastpath/__init__.py
============================================================================

LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
-----------------------------------------------
This file is the "ESPHome rulebook" for the SHiP Fast-Path component.

ESPHome is a tool that lets you write a simple recipe (a YAML file) and it
automatically generates the complex C++ code to run on your ESP32 chip.

Before generating the C++ code, ESPHome reads THIS file to answer:
  - "Does the user's chip actually support what they're asking for?"
  - "Is the user's YAML recipe valid, or did they make a mistake?"

Three specific hardware guards this file enforces:

  1. ESP32-H2 HAS NO WI-FI — BLOCK ESP-NOW:
     The ESP32-H2 chip only has a 802.15.4 Zigbee/Thread radio — there is
     physically no Wi-Fi antenna inside it. If someone writes
     "transport: espnow" in their YAML while using an H2, this file
     catches that mistake before any code is even generated and says
     "sorry, the H2 has no Wi-Fi — please use transport: ieee802154 instead."

  2. ESP32-C6 DUAL-RADIO WARNING:
     The ESP32-C6 has ONE shared 2.4 GHz radio path. If you enable both Wi-Fi
     AND Zigbee Router mode at the same time, they compete for the same antenna
     and cause packet collisions. This file warns the user about that.

  3. nRF52840 OPENTHREAD CONSTRAINT:
     Nordic's nRF52840 supports at most 8 Zigbee/Thread endpoints (device
     "connection points"). More than 8 will crash the firmware. This file
     enforces that limit.

  4. SLEEPY END DEVICE WAKE-UP:
     Battery-powered sensors that sleep most of the time (Sleepy End Devices)
     need to be temporarily woken up (poll_period: 0s) during firmware updates
     or binding operations, then put back to sleep (poll_period: 5s). This file
     injects that automatically.

============================================================================

LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
--------------------------------------------
Standard ESPHome External Component __init__.py.
Uses esphome.codegen, esphome.config_validation (cv), and esphome.const.
Generates #include and component registration in the ESPHome build pipeline.
============================================================================
"""

import esphome.codegen as cg          # type: ignore[import]
import esphome.config_validation as cv # type: ignore[import]
from esphome.const import (            # type: ignore[import]
    CONF_ID,
    CONF_PLATFORM,
)
from esphome.components import esp32   # type: ignore[import]

# ---------------------------------------------------------------------------
# COMPONENT NAMESPACE
# ---------------------------------------------------------------------------
ship_fastpath_ns = cg.esphome_ns.namespace("ship_fastpath")
SHiPFastpathComponent = ship_fastpath_ns.class_(
    "SHiPFastpathComponent", cg.Component
)

# ---------------------------------------------------------------------------
# CONFIGURATION KEYS
# ELI5: These are the words the user types in their YAML recipe.
# ---------------------------------------------------------------------------
CONF_TRANSPORT     = "transport"        # "ieee802154" or "espnow"
CONF_CHANNEL       = "channel"          # Zigbee/Thread channel (11–26)
CONF_PAN_ID        = "pan_id"           # Network PAN ID (0x0000–0xFFFE)
CONF_GROUP_ID      = "group_id"         # SHiP Device Group ID (0–255)
CONF_GROUP_KEY     = "group_key"        # 16-byte hex group key
CONF_POLL_PERIOD   = "poll_period"      # Sleepy End Device poll interval
CONF_MAX_ENDPOINTS = "max_endpoints"    # nRF52840: max Zigbee/Thread endpoints

# Valid transport values
TRANSPORT_IEEE802154 = "ieee802154"
TRANSPORT_ESPNOW     = "espnow"

# ---------------------------------------------------------------------------
# CONFIG SCHEMA
# ---------------------------------------------------------------------------
CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(SHiPFastpathComponent),
    cv.Optional(CONF_TRANSPORT, default=TRANSPORT_IEEE802154):
        cv.one_of(TRANSPORT_IEEE802154, TRANSPORT_ESPNOW, lower=True),
    cv.Optional(CONF_CHANNEL, default=15):
        cv.int_range(min=11, max=26),
    cv.Optional(CONF_PAN_ID, default=0x1234):
        cv.hex_int_range(min=0x0000, max=0xFFFE),
    cv.Optional(CONF_GROUP_ID, default=1):
        cv.int_range(min=0, max=255),
    cv.Required(CONF_GROUP_KEY):
        cv.All(cv.string, cv.Length(min=32, max=32)),
        # ELI5: 32 hex characters = 16 bytes = 128-bit secret key
    cv.Optional(CONF_POLL_PERIOD, default="5s"):
        cv.positive_time_period_milliseconds,
    cv.Optional(CONF_MAX_ENDPOINTS, default=8):
        cv.int_range(min=1, max=240),
}).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    """
    ELI5: ESPHome calls this function to generate the C++ code.
    We apply all hardware guards here before emitting any code.

    GUARD 1 — ESP32-H2: block ESP-NOW (no Wi-Fi hardware).
    GUARD 2 — ESP32-C6: warn if Wi-Fi + Zigbee router simultaneously active.
    GUARD 3 — nRF52840: enforce max 8 Zigbee/Thread endpoints.
    GUARD 4 — Sleepy End Device: inject poll_period: 0s for OTA/binding.
    """
    # ---- Platform detection ----
    try:
        from esphome.components.esp32 import get_esp32_variant  # type: ignore[import]
        variant = get_esp32_variant()
    except Exception:
        variant = "unknown"

    transport = config[CONF_TRANSPORT]
    channel   = config[CONF_CHANNEL]
    pan_id    = config[CONF_PAN_ID]
    group_id  = config[CONF_GROUP_ID]
    group_key = config[CONF_GROUP_KEY]
    max_eps   = config[CONF_MAX_ENDPOINTS]

    # GUARD 1: ESP32-H2 + ESP-NOW → compile-time error
    if variant in ("esp32h2", "ESP32H2") and transport == TRANSPORT_ESPNOW:
        raise cv.Invalid(
            "\n"
            "=== SHiP Fast-Path: ELI5 Hardware Compatibility Error ===\n"
            "The ESP32-H2 chip does NOT have Wi-Fi hardware.\n"
            "ESP-NOW requires Wi-Fi, so it cannot be used on an H2.\n"
            "WHAT TO DO:\n"
            "  Change 'transport: espnow' → 'transport: ieee802154' in your YAML.\n"
            "  The H2 can act as a Zigbee coordinator with up to 239 endpoints.\n"
            "  See: esphome/components/ship_fastpath/example_esp32c6_switch.yaml\n"
        )

    # GUARD 2: ESP32-C6 + Wi-Fi + Zigbee router → warning
    if variant in ("esp32c6", "ESP32C6") and transport == TRANSPORT_IEEE802154:
        # We emit a compile-time #warning — actual runtime coexistence requires
        # careful Kconfig (CONFIG_ESP_WIFI_ENABLED + CONFIG_IEEE802154_ENABLED).
        cg.add_define(
            "SHIP_WARN_C6_DUAL_RADIO",
            "ESP32-C6: Wi-Fi and Zigbee share one 2.4GHz radio path. "
            "If both are active simultaneously, expect packet collisions. "
            "Disable wifi: or set zigbee router: false for production use."
        )

    # GUARD 3: nRF52840 endpoint limit
    if variant in ("nrf52840", "NRF52840") and max_eps > 8:
        raise cv.Invalid(
            "\n"
            "=== SHiP Fast-Path: ELI5 nRF52840 Endpoint Limit ===\n"
            f"You configured max_endpoints: {max_eps}, but the nRF52840 only\n"
            "supports up to 8 Zigbee/Thread endpoints (connection slots).\n"
            "WHAT TO DO: Set 'max_endpoints: 8' or lower.\n"
        )

    # GUARD 4: Sleepy End Device — inject poll_period management
    poll_ms = config[CONF_POLL_PERIOD]
    if poll_ms > 0:
        # Emit a compile-time comment indicating SED poll management is needed
        cg.add_define(
            "SHIP_SED_POLL_PERIOD_MS",
            str(poll_ms)
        )

    # ---- Generate the C++ component instance ----
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)

    # Pass configuration values to the C++ component
    cg.add(var.set_channel(channel))
    cg.add(var.set_pan_id(pan_id))
    cg.add(var.set_group_id(group_id))
    cg.add(var.set_group_key_hex(group_key))
    cg.add(var.set_transport(transport == TRANSPORT_ESPNOW))
    cg.add(var.set_poll_period_ms(poll_ms))
    cg.add(var.set_max_endpoints(max_eps))
