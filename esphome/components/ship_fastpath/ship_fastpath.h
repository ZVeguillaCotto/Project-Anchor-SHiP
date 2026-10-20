/*
 * ============================================================================
 * SHiP v0.1 — esphome/components/ship_fastpath/ship_fastpath.h
 * ============================================================================
 *
 * LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
 * -----------------------------------------------
 * This is the header (the "table of contents") for the ESPHome SHiP Fast-Path
 * component. It describes one C++ class: SHiPFastpathComponent.
 *
 * Think of this class as a self-contained "radio co-pilot" that runs alongside
 * the normal ESPHome code on your ESP32-C6 or nRF52840 chip:
 *
 *   - During SETUP: The co-pilot powers up the 802.15.4 radio (or ESP-NOW
 *     Wi-Fi), joins the correct channel and PAN, loads the device's cheat-sheet
 *     (LEAT — Local Event-Action Table) into RAM, and starts listening.
 *
 *   - During LOOP: The co-pilot checks if any incoming SHiP radio whispers
 *     match rules in the LEAT and immediately fires the corresponding action
 *     (toggle a relay, set a brightness level, recall a scene, etc.).
 *
 *   - On BUTTON PRESS: The ESPHome switch calls send_ship_frame() which
 *     packs the 19-byte SHiP envelope and hands it to the radio HAL to
 *     transmit — all in under 3 milliseconds.
 *
 * ============================================================================
 *
 * LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
 * ============================================================================
 * Inherits esphome::Component for setup()/loop() integration.
 * Wraps ship_protocol.h types and ship_radio_hal.c functions.
 * ============================================================================
 */

#pragma once

#include "esphome/core/component.h"
#include "esphome/core/log.h"
#include "../../../src/ship_core/ship_protocol.h"

namespace esphome {
namespace ship_fastpath {

static const char *const TAG_SHIP = "ship_fastpath";

/* ============================================================================
 * SHiPFastpathComponent
 * ELI5: The main "radio co-pilot" class for ESPHome.
 * ============================================================================ */
class SHiPFastpathComponent : public Component {
 public:
  /* ---------- ESPHome lifecycle ---------- */
  void setup() override;
  void loop() override;
  float get_setup_priority() const override { return setup_priority::HARDWARE; }

  /* ---------- Configuration setters (called from __init__.py codegen) ---------- */
  void set_channel(uint8_t ch)            { channel_ = ch; }
  void set_pan_id(uint16_t pan)           { pan_id_  = pan; }
  void set_group_id(uint8_t gid)          { group_id_ = gid; }
  void set_group_key_hex(const char *hex) { hex_to_bytes(hex, group_key_, SHIP_KEY_SIZE); }
  void set_transport(bool espnow)         { use_espnow_ = espnow; }
  void set_poll_period_ms(uint32_t ms)    { poll_period_ms_ = ms; }
  void set_max_endpoints(uint8_t n)       { max_endpoints_ = n; }

  /* ---------- Public API ---------- */

  /**
   * send_ship_frame()
   * ELI5: Call this from an ESPHome switch/sensor callback to broadcast
   *       a SHiP command immediately. Parameters match the 19-byte frame fields.
   *
   * @param domain    ship_domain_t cast to uint8_t
   * @param gesture   ship_gesture_t cast to uint8_t
   * @param param_1   Primary action parameter
   * @param param_2   Secondary parameter
   * @param param_3   Tertiary parameter
   * @param unicast   If 0xFFFF: broadcast. Otherwise: unicast short address.
   */
  void send_ship_frame(uint8_t domain, uint8_t gesture,
                       uint8_t param_1, uint8_t param_2, uint8_t param_3,
                       uint16_t unicast = SHIP_ADDR_BROADCAST);

  /**
   * add_leat_entry()
   * ELI5: Add a rule to this device's cheat-sheet (LEAT).
   *       When domain+gesture+group_id matches an incoming frame,
   *       the action fires immediately.
   */
  bool add_leat_entry(const ship_leat_entry_t &entry);

  /**
   * inject_rx_for_test()
   * ELI5: Test helper — pretend the radio received this raw frame.
   *       Used by the ESPHome test framework (not for production use).
   */
  void inject_rx_for_test(const uint8_t *frame_bytes, size_t len);

 protected:
  /* ---------- Radio RX callback (static, dispatched to instance) ---------- */
  static void static_rx_callback(const ship_frame_19b_t *frame, void *ctx);
  void on_frame_received(const ship_frame_19b_t *frame);

  /* ---------- Action dispatcher ---------- */
  void dispatch_leat_action(const ship_leat_entry_t &entry,
                             const ship_frame_19b_t *frame);

  /* ---------- Mesh-RTC helpers ---------- */
  void maybe_broadcast_rtc_sync();
  uint32_t local_mono_ms() const;

  /* ---------- Helpers ---------- */
  static void hex_to_bytes(const char *hex, uint8_t *out, size_t len);
  uint16_t next_seq_num() { return seq_counter_++; }

  /* ---------- Configuration state ---------- */
  uint8_t  channel_         {15};
  uint16_t pan_id_          {0x1234};
  uint8_t  group_id_        {1};
  uint8_t  group_key_       [SHIP_KEY_SIZE] {};
  bool     use_espnow_      {false};
  uint32_t poll_period_ms_  {5000};
  uint8_t  max_endpoints_   {8};

  /* ---------- Runtime state ---------- */
  uint16_t          seq_counter_          {0};
  uint16_t          last_rx_counter_      {0};
  ship_mesh_rtc_t   rtc_                  {};
  uint32_t          last_rtc_broadcast_ms_{0};

  /* ---------- LEAT (Local Event-Action Table) ---------- */
  ship_leat_entry_t leat_table_[SHIP_LEAT_MAX_ENTRIES] {};
  size_t            leat_count_{0};
};

}  // namespace ship_fastpath
}  // namespace esphome
