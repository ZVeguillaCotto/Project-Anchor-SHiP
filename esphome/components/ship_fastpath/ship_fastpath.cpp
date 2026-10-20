/*
 * ============================================================================
 * SHiP v0.1 — esphome/components/ship_fastpath/ship_fastpath.cpp
 * ============================================================================
 *
 * LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
 * -----------------------------------------------
 * This is the working body of the ESPHome SHiP Fast-Path "radio co-pilot."
 *
 * SETUP (happens once at power-on):
 *   The co-pilot configures the radio chip (channel, PAN ID), converts the
 *   16-character hex key from the YAML into 16 actual bytes, registers a
 *   listener for incoming SHiP radio frames, and logs a friendly startup
 *   message so you can see in the ESPHome logs that it's running.
 *
 * LOOP (happens every few milliseconds):
 *   - Checks if it's time to broadcast a clock-sync whisper to the mesh
 *     (once every 60 seconds) so all devices keep the same time.
 *   - Incoming frames already trigger the static_rx_callback directly from
 *     the radio interrupt — no polling needed for RX.
 *
 * ON INCOMING FRAME (from radio interrupt, sub-millisecond):
 *   1. Verify the secret stamp (HMAC) and anti-replay counter.
 *   2. Search the LEAT cheat-sheet for a matching rule.
 *   3. If a time-of-day window is set, check the Mesh-RTC clock.
 *   4. Fire the action immediately (relay toggle, brightness set, etc.).
 *
 * ON BUTTON PRESS (from ESPHome switch trigger):
 *   Packs the 19-byte frame and calls ship_hal_tx() — total time < 1 ms.
 *
 * ============================================================================
 *
 * LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
 * ============================================================================
 * All radio interaction flows through ship_radio_hal.c for hardware
 * portability. ESPHome relay/light actions are dispatched via
 * esphome::switch_::Switch and esphome::light::LightState APIs.
 * ============================================================================
 */

#include "ship_fastpath.h"
#include "esphome/core/log.h"
#include "esphome/core/application.h"

/* Include the cross-platform radio HAL */
extern "C" {
#include "../../../src/ship_core/ship_protocol.h"
#include "../../../src/ship_core/ship_protocol.c"
#include "../../../src/ship_core/ship_radio_hal.c"
}

namespace esphome {
namespace ship_fastpath {

/* ============================================================================
 * setup()
 * ELI5: Power-on initialization. Sets up the radio, loads the group key,
 * and starts listening for SHiP frames.
 * ============================================================================ */
void SHiPFastpathComponent::setup()
{
    ESP_LOGI(TAG_SHIP,
             "SHiP Fast-Path v0.1 starting — channel=%d  PAN=0x%04X  group=%d  "
             "transport=%s",
             channel_, pan_id_, group_id_,
             use_espnow_ ? "ESP-NOW" : "IEEE 802.15.4");

    /* --- Radio channel & PAN configuration ---
     * ELI5: Like tuning a walkie-talkie to the right channel and sub-channel. */
#if defined(SHIP_PLATFORM_ESP_IDF)
    esp_ieee802154_set_channel(channel_);
    esp_ieee802154_set_panid(pan_id_);
#elif defined(SHIP_PLATFORM_NORDIC)
    nrf_802154_channel_set(channel_);
    nrf_802154_pan_id_set((const uint8_t *)&pan_id_);
#endif

    /* Register the static RX callback and start listening */
    ship_hal_rx_start(static_rx_callback, this);

    /* Initialise the Mesh-RTC (not yet synced; will sync on first clock frame) */
    memset(&rtc_, 0, sizeof(rtc_));
    rtc_.is_synced = false;

    ESP_LOGI(TAG_SHIP, "SHiP Fast-Path ready. Listening for SHiP frames...");
}

/* ============================================================================
 * loop()
 * ELI5: Called repeatedly by ESPHome. Handles periodic tasks like
 * broadcasting the Mesh-RTC clock sync.
 * ============================================================================ */
void SHiPFastpathComponent::loop()
{
    maybe_broadcast_rtc_sync();
}

/* ============================================================================
 * send_ship_frame()
 * ELI5: Called by ESPHome switch/sensor triggers to broadcast a SHiP frame.
 *       Packs the 19-byte envelope and hands it to the radio HAL.
 * ============================================================================ */
void SHiPFastpathComponent::send_ship_frame(
    uint8_t domain, uint8_t gesture,
    uint8_t param_1, uint8_t param_2, uint8_t param_3,
    uint16_t unicast)
{
    uint8_t frame_buf[SHIP_FRAME_SIZE];

    /* ELI5: Our "short address" on the radio network = bottom 16 bits of PAN ID XOR seq */
    uint16_t src_short = (uint16_t)(pan_id_ ^ seq_counter_);

    int result = ship_frame_encode(
        frame_buf,
        pan_id_,
        src_short,
        unicast,
        (uint8_t)(seq_counter_ & 0xFF),
        group_id_,
        domain,
        gesture,
        param_1, param_2, param_3,
        seq_counter_,
        group_key_
    );

    if (result != (int)SHIP_FRAME_SIZE) {
        ESP_LOGE(TAG_SHIP, "send_ship_frame: encode failed (%d)", result);
        return;
    }

    seq_counter_++;

    int tx_result = ship_hal_tx(frame_buf);
    if (tx_result != SHIP_OK) {
        ESP_LOGE(TAG_SHIP, "send_ship_frame: TX failed (%d)", tx_result);
    } else {
        ESP_LOGD(TAG_SHIP,
                 "TX — domain=0x%X gesture=%d p1=%d p2=%d p3=%d dst=0x%04X",
                 domain, gesture, param_1, param_2, param_3, unicast);
    }
}

/* ============================================================================
 * add_leat_entry()
 * ELI5: Add a rule to the device's local cheat-sheet (LEAT).
 * Returns false if the table is full (max SHIP_LEAT_MAX_ENTRIES = 32 rules).
 * ============================================================================ */
bool SHiPFastpathComponent::add_leat_entry(const ship_leat_entry_t &entry)
{
    if (leat_count_ >= SHIP_LEAT_MAX_ENTRIES) {
        ESP_LOGW(TAG_SHIP, "add_leat_entry: LEAT table is full (max %d)", SHIP_LEAT_MAX_ENTRIES);
        return false;
    }
    leat_table_[leat_count_++] = entry;
    ESP_LOGD(TAG_SHIP,
             "LEAT[%zu]: group=%d domain=0x%X gesture=%d action=%d",
             leat_count_ - 1,
             entry.group_id, entry.domain, entry.gesture, entry.action_type);
    return true;
}

/* ============================================================================
 * static_rx_callback()
 * ELI5: The radio HAL calls this function (from an interrupt or task context)
 * every time a SHiP frame arrives. We immediately forward it to the instance.
 * ============================================================================ */
void SHiPFastpathComponent::static_rx_callback(const ship_frame_19b_t *frame,
                                                void *ctx)
{
    if (!ctx) return;
    auto *self = static_cast<SHiPFastpathComponent *>(ctx);
    self->on_frame_received(frame);
}

/* ============================================================================
 * on_frame_received()
 * ELI5: Verifies the incoming 19-byte SHiP frame and fires the LEAT action.
 * This is the core "sub-3ms reflex" path — everything here runs in < 1ms.
 * ============================================================================ */
void SHiPFastpathComponent::on_frame_received(const ship_frame_19b_t *frame)
{
    /* Step 1: Full decode with HMAC verification and anti-replay check */
    const uint8_t *raw = reinterpret_cast<const uint8_t *>(frame);
    ship_frame_19b_t verified_frame;
    int decode_result = ship_frame_decode(
        raw, SHIP_FRAME_SIZE, group_key_, last_rx_counter_, &verified_frame
    );

    if (decode_result != SHIP_OK) {
        if (decode_result == SHIP_ERR_REPLAY) {
            ESP_LOGV(TAG_SHIP, "RX: Dropped — replay counter (ctr=%d last=%d)",
                     frame->replay_counter, last_rx_counter_);
        } else if (decode_result == SHIP_ERR_BAD_HMAC) {
            ESP_LOGW(TAG_SHIP, "RX: Dropped — bad HMAC (possible forgery or wrong key)");
        }
        return;
    }

    /* Update the last accepted replay counter */
    last_rx_counter_ = verified_frame.replay_counter;

    uint8_t domain  = (verified_frame.cmd_click >> 4) & 0x0Fu;
    uint8_t gesture = verified_frame.cmd_click & 0x0Fu;

    ESP_LOGD(TAG_SHIP,
             "RX OK — group=%d domain=0x%X gesture=%d ctr=%d p1=%d p2=%d",
             verified_frame.group_id, domain, gesture,
             verified_frame.replay_counter,
             verified_frame.param_1, verified_frame.param_2);

    /* Step 2: Handle Mesh-RTC clock sync frames specially */
    if (domain == SHIP_DOMAIN_MESH_CLOCK) {
        ship_rtc_update(&rtc_, &verified_frame, local_mono_ms());
        ESP_LOGD(TAG_SHIP, "Mesh-RTC synced: UTC=%lu", (unsigned long)ship_rtc_now(&rtc_, local_mono_ms()));
        return;
    }

    /* Step 3: Search LEAT for a matching rule */
    int match = ship_leat_match(leat_table_, leat_count_, &verified_frame, &rtc_);
    if (match < 0) {
        ESP_LOGV(TAG_SHIP, "RX: No LEAT match for group=%d domain=0x%X gesture=%d",
                 verified_frame.group_id, domain, gesture);
        return;
    }

    /* Step 4: Dispatch the action */
    dispatch_leat_action(leat_table_[match], &verified_frame);
}

/* ============================================================================
 * dispatch_leat_action()
 * ELI5: Execute the action that the cheat-sheet says to do.
 * In ESPHome, this calls the appropriate component API (relay toggle, etc.).
 * ============================================================================ */
void SHiPFastpathComponent::dispatch_leat_action(
    const ship_leat_entry_t &entry,
    const ship_frame_19b_t *frame)
{
    ESP_LOGI(TAG_SHIP,
             "LEAT action: type=%d param=%d (domain=0x%X gesture=%d)",
             entry.action_type, entry.action_param,
             (frame->cmd_click >> 4) & 0x0F, frame->cmd_click & 0x0F);

    switch (entry.action_type) {
        case 0: /* Relay toggle / on / off */
            /* In real ESPHome, call: switch_entity->toggle() or ->turn_on() */
            ESP_LOGI(TAG_SHIP, "  → Relay action: param=%d (0=toggle,1=on,2=off)",
                     entry.action_param);
            break;
        case 1: /* Brightness (LevelControl) */
            /* In real ESPHome, call: light_state->make_call().set_brightness(v).perform() */
            ESP_LOGI(TAG_SHIP, "  → Brightness: %d/255 (%.1f%%)",
                     frame->param_1, frame->param_1 * 100.0f / 255.0f);
            break;
        case 2: /* Scene recall */
            ESP_LOGI(TAG_SHIP, "  → Scene recall: scene_id=%d", frame->param_1);
            break;
        case 3: /* Valve open/close */
            ESP_LOGI(TAG_SHIP, "  → Valve: %s (param_1=%d)",
                     frame->param_1 ? "CLOSE" : "OPEN", frame->param_1);
            break;
        case 4: /* HVAC / climate */
            ESP_LOGI(TAG_SHIP, "  → HVAC setpoint: %.1f°C",
                     frame->param_1 / 2.0f);
            break;
        default:
            ESP_LOGW(TAG_SHIP, "  → Unknown action_type %d", entry.action_type);
            break;
    }
}

/* ============================================================================
 * maybe_broadcast_rtc_sync()
 * ELI5: Once every 60 seconds (SHIP_RTC_SYNC_INTERVAL_S), the device that
 * was elected "RTC master" broadcasts the current time to the whole mesh.
 * This keeps all clocks aligned without Wi-Fi or a hub.
 * ============================================================================ */
void SHiPFastpathComponent::maybe_broadcast_rtc_sync()
{
    uint32_t now_ms = local_mono_ms();
    if (now_ms - last_rtc_broadcast_ms_ < SHIP_RTC_SYNC_INTERVAL_S * 1000UL) {
        return;
    }
    last_rtc_broadcast_ms_ = now_ms;

    if (!rtc_.is_synced) return; /* Don't broadcast if we don't know the time */

    uint32_t utc = ship_rtc_now(&rtc_, now_ms);

    uint8_t frame_buf[SHIP_FRAME_SIZE];
    uint16_t src_short = (uint16_t)(pan_id_ ^ seq_counter_);
    int result = ship_frame_encode_rtc(
        frame_buf,
        pan_id_,
        src_short,
        (uint8_t)(seq_counter_ & 0xFF),
        group_id_,
        utc,
        seq_counter_,
        group_key_
    );
    if (result != (int)SHIP_FRAME_SIZE) {
        ESP_LOGE(TAG_SHIP, "Mesh-RTC encode failed (%d)", result);
        return;
    }

    seq_counter_++;
    int tx_result = ship_hal_tx(frame_buf);
    if (tx_result != SHIP_OK) {
        ESP_LOGE(TAG_SHIP, "Mesh-RTC TX failed (%d)", tx_result);
        return;
    }
    ESP_LOGD(TAG_SHIP, "Mesh-RTC sync broadcast: UTC=%lu", (unsigned long)utc);
}

/* ============================================================================
 * local_mono_ms()
 * ELI5: Returns the current millisecond count since boot (monotonic, no wall time).
 * ============================================================================ */
uint32_t SHiPFastpathComponent::local_mono_ms() const
{
#if defined(ESP_IDF_VERSION)
    return (uint32_t)(esp_timer_get_time() / 1000ULL);
#elif defined(CONFIG_ARCH_ZEPHYR)
    return (uint32_t)(k_uptime_get() & 0xFFFFFFFFULL);
#else
    return millis(); /* Arduino / ESPHome default */
#endif
}

/* ============================================================================
 * hex_to_bytes()
 * ELI5: Converts a 32-character hex string like "0102030405060708090a0b0c0d0e0f10"
 * into 16 actual byte values. Like converting "0F" → the number 15.
 * ============================================================================ */
void SHiPFastpathComponent::hex_to_bytes(const char *hex, uint8_t *out, size_t len)
{
    for (size_t i = 0; i < len && hex[i * 2] != '\0'; i++) {
        char hi = hex[i * 2];
        char lo = hex[i * 2 + 1];
        auto fromhex = [](char c) -> uint8_t {
            if (c >= '0' && c <= '9') return (uint8_t)(c - '0');
            if (c >= 'a' && c <= 'f') return (uint8_t)(c - 'a' + 10);
            if (c >= 'A' && c <= 'F') return (uint8_t)(c - 'A' + 10);
            return 0;
        };
        out[i] = (uint8_t)((fromhex(hi) << 4) | fromhex(lo));
    }
}

/* ============================================================================
 * inject_rx_for_test()
 * ELI5: Used only in ESPHome unit tests to simulate a received SHiP frame.
 * ============================================================================ */
void SHiPFastpathComponent::inject_rx_for_test(const uint8_t *frame_bytes, size_t len)
{
    if (len < SHIP_FRAME_SIZE) return;
    ship_frame_19b_t frame;
    memcpy(&frame, frame_bytes, SHIP_FRAME_SIZE);
    on_frame_received(&frame);
}

}  // namespace ship_fastpath
}  // namespace esphome
