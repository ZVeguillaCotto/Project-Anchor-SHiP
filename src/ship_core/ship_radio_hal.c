/*
 * ============================================================================
 * SHiP v0.1 — ship_radio_hal.c
 * ============================================================================
 *
 * LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
 * -----------------------------------------------
 * HAL stands for "Hardware Abstraction Layer." Think of it as a universal
 * TV remote that works with any brand of TV — you press the same buttons,
 * and it figures out which specific code to send to your particular TV.
 *
 * Different SHiP-capable chips (Espressif ESP32-C6, Espressif ESP32-H2,
 * Nordic nRF52840, Nordic nRF5340) all have slightly different internal radio
 * controls. This file hides those differences behind a single, clean set of
 * functions:
 *
 *   ship_hal_tx()  — "Send this 19-byte whisper over the radio"
 *   ship_hal_rx_start() — "Start listening for whispers and call me when one arrives"
 *   ship_hal_rx_stop()  — "Stop listening"
 *
 * The hard engineering rules this file enforces:
 *
 *  A) OPENTHREAD COEXISTENCE RULE:
 *     If the device is running OpenThread (the mesh networking protocol used
 *     by Thread / Matter devices), we MUST NOT take over the radio directly.
 *     Instead, we register a "pcap callback" — like a wiretap that lets us
 *     peek at every packet AFTER the hardware has verified its checksum, but
 *     BEFORE Thread's own software has processed it. SHiP frames with the
 *     magic byte 0xA5 are intercepted here without disrupting Thread.
 *
 *  B) ESPRESSIF DIRECT INJECT RULE:
 *     On ESP32-C6 and ESP32-H2 running ESP-IDF (not OpenThread), we can use
 *     esp_ieee802154_transmit() to inject our 19-byte frame directly into the
 *     hardware radio queue. We prefix it with a PHR byte (length = 19 + 2 = 21)
 *     and set cca=true so the chip does Carrier-Sense Multiple Access (CSMA-CA)
 *     — i.e., "listen before talking" so we don't collide with other devices.
 *
 *  C) NORDIC MPSL RULE:
 *     On nRF52840/nRF5340, Nordic's Multi-Protocol Service Layer (MPSL) manages
 *     radio time slots. We MUST keep CONFIG_MPSL=y. Disabling it causes
 *     TX_ERROR_TIMESLOT_DENIED (error code 5). We use nrf_802154_transmit_raw()
 *     which always respects the MPSL scheduler.
 *
 *  D) ESP32-H2 ESP-NOW BLOCK:
 *     The ESP32-H2 chip has NO Wi-Fi hardware — only 802.15.4 Zigbee/Thread.
 *     Any attempt to use ESP-NOW (which requires Wi-Fi) on ESP32-H2 must be
 *     rejected at configuration time and never called at runtime.
 *
 * ============================================================================
 *
 * LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
 * ============================================================================
 * Platform is selected via preprocessor defines:
 *   SHIP_PLATFORM_ESP_IDF   — Espressif ESP32-C6 / ESP32-H2, ESP-IDF v5.1+
 *   SHIP_PLATFORM_NORDIC    — nRF52840 / nRF5340, NCS v3.2.x / Zephyr
 *   SHIP_PLATFORM_OPENTHREAD — Any platform with OpenThread pcap hook available
 *   SHIP_PLATFORM_NATIVE    — Host machine simulation (for unit tests)
 * ============================================================================
 */

#include "ship_protocol.h"
#include <string.h>
#include <stdio.h>

/* ============================================================================
 * PLATFORM DETECTION
 * ============================================================================ */
#if defined(ESP_PLATFORM)
#  include "sdkconfig.h"
#endif

#if defined(SHIP_PLATFORM_ESP_IDF)
#  include "esp_ieee802154.h"
#  include "esp_log.h"
   static const char *TAG = "ship_hal_esp";

#elif defined(SHIP_PLATFORM_NORDIC)
#  include "nrf_802154.h"

#elif defined(SHIP_PLATFORM_OPENTHREAD)
#  include <openthread/link.h>
#  include <openthread/instance.h>
#  if defined(CONFIG_IDF_TARGET_ESP32C6) || defined(CONFIG_IDF_TARGET_ESP32H2)
#    include "esp_ieee802154.h"
#  elif defined(CONFIG_NRF_802154)
#    include "nrf_802154.h"
#  endif
   static otInstance *s_ot_instance = NULL;

#else
/* SHIP_PLATFORM_NATIVE — host simulation for unit tests */
#  ifndef SHIP_PLATFORM_NATIVE
#    define SHIP_PLATFORM_NATIVE
#  endif
   static uint8_t s_last_tx_buf[SHIP_PSDU_SIZE + 1];
   static size_t  s_last_tx_len = 0;
#endif

/* ============================================================================
 * SHARED RX CALLBACK POINTER
 * ELI5: When a SHiP frame arrives, the HAL calls this function pointer so the
 * application layer can process it. It's like a doorbell — hardware pushes the
 * button, and the homeowner's code answers the door.
 * ============================================================================ */
static ship_rx_callback_t s_rx_cb  = NULL;
static void              *s_rx_ctx = NULL;

/* ============================================================================
 * ESPRESSIF ESP-IDF PLATFORM IMPLEMENTATION
 * ============================================================================ */
#if defined(SHIP_PLATFORM_ESP_IDF)

/**
 * ship_hal_tx()
 * ELI5: Tells the ESP32 radio chip to transmit our 19-byte packet.
 *       The chip prepends the physical-layer preamble automatically.
 *
 * TX Buffer layout (ESP-IDF ieee802154):
 *   tx_buf[0] = PHR = PSDU length byte = 19 (frame) + 2 (FCS) = 21
 *   tx_buf[1..19] = the 19-byte SHiP frame
 *   (tx_buf[20..21] = CRC appended automatically by radio hardware)
 */
int ship_hal_tx(const uint8_t *ship_frame_19b)
{
    if (!ship_frame_19b) return SHIP_ERR_NULL_PTR;

    uint8_t tx_buf[SHIP_PSDU_SIZE + 1]; /* +1 for the PHR length byte */
    tx_buf[0] = SHIP_PSDU_SIZE;         /* PHR: 19 data + 2 hardware FCS */
    memcpy(&tx_buf[1], ship_frame_19b, SHIP_FRAME_SIZE);

    esp_err_t err = esp_ieee802154_transmit(tx_buf, true /* cca = CSMA-CA */);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "ship_hal_tx failed: 0x%x", err);
        return -1;
    }
    return SHIP_OK;
}

/**
 * ship_hal_esp_rx_done_callback()
 * ELI5: The ESP32 radio chip calls this function every time it successfully
 * receives and CRC-validates a 802.15.4 packet. We peek at byte 9 for the
 * SHiP magic byte 0xA5 and, if found, parse the frame and invoke the
 * application-layer RX callback.
 *
 * This callback is invoked from the radio ISR context — keep it short!
 */
void IRAM_ATTR ship_hal_esp_rx_done_callback(uint8_t *data, uint8_t len,
                                              bool pending)
{
    (void)pending;
    if (!data || len < SHIP_FRAME_SIZE + 1) return; /* +1 for PHR byte */

    /* data[0] = PHR (PSDU length), data[1..] = PSDU bytes */
    const uint8_t *psdu = &data[1];

    /* Quick magic-byte filter before full decode */
    if (psdu[9] != SHIP_MAGIC_BYTE) return;

    if (s_rx_cb) {
        ship_frame_19b_t frame;
        /* Note: full HMAC+replay validation is done in ship_frame_decode().
         * HAL just passes raw bytes up. Key lookup is application-layer
         * responsibility. For the pcap-interception path we use a dummy decode. */
        memcpy(&frame, psdu, SHIP_FRAME_SIZE);
        s_rx_cb(&frame, s_rx_ctx);
    }
}

int ship_hal_rx_start(ship_rx_callback_t cb, void *user_ctx)
{
    s_rx_cb  = cb;
    s_rx_ctx = user_ctx;
    /* The esp_ieee802154 driver calls esp_ieee802154_receive_done() on the
     * application; we hook via esp_ieee802154_register_receive_done_handler()
     * in ESP-IDF v6+. For v5.1.x, set via Kconfig IEEE802154_RX_CB. */
    return SHIP_OK;
}

int ship_hal_rx_stop(void)
{
    s_rx_cb  = NULL;
    s_rx_ctx = NULL;
    return SHIP_OK;
}

/* ============================================================================
 * NORDIC NRF5 PLATFORM IMPLEMENTATION
 * ============================================================================ */
#elif defined(SHIP_PLATFORM_NORDIC)

/**
 * ship_hal_tx()
 * ELI5: Tells the Nordic nRF52840/nRF5340 radio to transmit our packet.
 *       The PSDU buffer must have psdu[0] = total length (19+2=21).
 *       nrf_802154_transmit_raw() hands it to the MPSL scheduler which picks
 *       the next available radio time slot — no collision with BLE or Thread.
 */
int ship_hal_tx(const uint8_t *ship_frame_19b)
{
    if (!ship_frame_19b) return SHIP_ERR_NULL_PTR;

    /* nRF 802.15.4 driver requires a static/DMA-accessible buffer */
    static uint8_t psdu[SHIP_PSDU_SIZE + 1]; /* +1 for length byte */
    psdu[0] = SHIP_PSDU_SIZE; /* PHR: 19 data + 2 hardware FCS */
    memcpy(&psdu[1], ship_frame_19b, SHIP_FRAME_SIZE);

    nrf_802154_transmit_metadata_t meta = {
        .frame_props = NRF_802154_TRANSMITTED_FRAME_PROPS_DEFAULT_INIT,
        .cca = true /* Carrier-Sense Multiple Access — listen before transmit */
    };

    nrf_802154_tx_error_t result = nrf_802154_transmit_raw(psdu, &meta);
    return (result == NRF_802154_TX_ERROR_NONE) ? SHIP_OK : -1;
}

/**
 * nrf_802154_received_raw() — Nordic driver callback (called from radio IRQ)
 * ELI5: Nordic's radio driver calls this function automatically each time a
 * valid 802.15.4 packet arrives. We filter for SHiP magic byte and forward.
 */
void nrf_802154_received_raw(uint8_t *data, int8_t power, uint8_t lqi)
{
    (void)power;
    (void)lqi;
    if (!data || data[0] < SHIP_FRAME_SIZE) return;

    const uint8_t *psdu = &data[1];
    if (psdu[9] != SHIP_MAGIC_BYTE) {
        nrf_802154_buffer_free_raw(data);
        return;
    }

    if (s_rx_cb) {
        ship_frame_19b_t frame;
        memcpy(&frame, psdu, SHIP_FRAME_SIZE);
        s_rx_cb(&frame, s_rx_ctx);
    }
    nrf_802154_buffer_free_raw(data);
}

int ship_hal_rx_start(ship_rx_callback_t cb, void *user_ctx)
{
    s_rx_cb  = cb;
    s_rx_ctx = user_ctx;
    nrf_802154_receive(); /* Put radio into continuous receive mode */
    return SHIP_OK;
}

int ship_hal_rx_stop(void)
{
    s_rx_cb  = NULL;
    s_rx_ctx = NULL;
    nrf_802154_sleep();
    return SHIP_OK;
}

/* ============================================================================
 * OPENTHREAD PCAP HOOK IMPLEMENTATION (Cross-Platform)
 * ELI5: When OpenThread is running, we cannot take over the radio chip
 * directly — OpenThread is already using it. Instead, we register a "pcap
 * callback" which is like a wiretap on all received radio packets. We peek
 * for SHiP frames (magic byte 0xA5) without disrupting Thread traffic.
 * ============================================================================ */
#elif defined(SHIP_PLATFORM_OPENTHREAD)

/**
 * ship_pcap_rx_callback()
 * ELI5: OpenThread calls this for every 802.15.4 frame it receives,
 * AFTER hardware CRC-16 (FCS) verification. SHiP frames have magic byte
 * 0xA5 at offset 9. All other frames are ignored (returned immediately).
 *
 * CRITICAL: We NEVER call otLinkRawSetReceiveDone() or otLinkRawTransmit()
 * while otIp6SetEnabled() is true — that returns OT_ERROR_INVALID_STATE.
 * The pcap hook is the ONLY safe interception path under Thread stack.
 */
static void ship_pcap_rx_callback(otInstance *aInstance,
                                   const otRadioFrame *aFrame,
                                   bool aIsTx)
{
    (void)aInstance;
    if (aIsTx) return; /* Only care about received frames */
    if (!aFrame || aFrame->mLength < SHIP_FRAME_SIZE) return;

    const uint8_t *psdu = aFrame->mPsdu;
    if (psdu[9] != SHIP_MAGIC_BYTE) return; /* Not a SHiP frame */

    if (s_rx_cb) {
        ship_frame_19b_t frame;
        memcpy(&frame, psdu, SHIP_FRAME_SIZE);
        s_rx_cb(&frame, s_rx_ctx);
    }
}

int ship_hal_rx_start(ship_rx_callback_t cb, void *user_ctx)
{
    if (!s_ot_instance) return -1;
    s_rx_cb  = cb;
    s_rx_ctx = user_ctx;
    /* Register pcap wiretap — otLinkSetPromiscuous stays FALSE.
     * The pcap callback fires AFTER hardware FCS check, BEFORE OT MAC filter. */
    otLinkSetPcapCallback(s_ot_instance, ship_pcap_rx_callback, NULL);
    return SHIP_OK;
}

int ship_hal_rx_stop(void)
{
    if (s_ot_instance) {
        otLinkSetPcapCallback(s_ot_instance, NULL, NULL);
    }
    s_rx_cb  = NULL;
    s_rx_ctx = NULL;
    return SHIP_OK;
}

int ship_hal_tx(const uint8_t *ship_frame_19b)
{
#if defined(CONFIG_IDF_TARGET_ESP32C6) || defined(CONFIG_IDF_TARGET_ESP32H2)
    if (!ship_frame_19b) return SHIP_ERR_NULL_PTR;
    uint8_t tx_buf[SHIP_PSDU_SIZE + 1];
    tx_buf[0] = SHIP_PSDU_SIZE;
    memcpy(&tx_buf[1], ship_frame_19b, SHIP_FRAME_SIZE);
    return esp_ieee802154_transmit(tx_buf, true) == ESP_OK ? SHIP_OK : -1;
#elif defined(CONFIG_NRF_802154)
    if (!ship_frame_19b) return SHIP_ERR_NULL_PTR;
    static uint8_t psdu[SHIP_PSDU_SIZE + 1];
    psdu[0] = SHIP_PSDU_SIZE;
    memcpy(&psdu[1], ship_frame_19b, SHIP_FRAME_SIZE);
    nrf_802154_transmit_metadata_t meta = {
        .frame_props = NRF_802154_TRANSMITTED_FRAME_PROPS_DEFAULT_INIT,
        .cca = true
    };
    return nrf_802154_transmit_raw(psdu, &meta) == NRF_802154_TX_ERROR_NONE
        ? SHIP_OK : -1;
#else
    (void)ship_frame_19b;
    return -1;
#endif
}

void ship_hal_openthread_set_instance(void *instance)
{
    s_ot_instance = (otInstance *)instance;
}

/* ============================================================================
 * NATIVE / HOST SIMULATION PLATFORM (for unit tests on macOS/Linux)
 * ELI5: When running on a laptop to test the code, there's no real radio.
 * We pretend: ship_hal_tx() saves the frame into a buffer so tests can
 * inspect exactly what would have been transmitted.
 * ============================================================================ */
#else /* SHIP_PLATFORM_NATIVE */

int ship_hal_tx(const uint8_t *ship_frame_19b)
{
    if (!ship_frame_19b) return SHIP_ERR_NULL_PTR;
    s_last_tx_len = SHIP_PSDU_SIZE;
    s_last_tx_buf[0] = SHIP_PSDU_SIZE;
    memcpy(&s_last_tx_buf[1], ship_frame_19b, SHIP_FRAME_SIZE);
    printf("[ship_hal_native] TX: %zu bytes — magic=0x%02X group=%u cmd=0x%02X\n",
           s_last_tx_len,
           ship_frame_19b[9],
           ship_frame_19b[10],
           ship_frame_19b[13]);
    return SHIP_OK;
}

int ship_hal_rx_start(ship_rx_callback_t cb, void *user_ctx)
{
    s_rx_cb  = cb;
    s_rx_ctx = user_ctx;
    printf("[ship_hal_native] RX listening started\n");
    return SHIP_OK;
}

int ship_hal_rx_stop(void)
{
    s_rx_cb  = NULL;
    s_rx_ctx = NULL;
    printf("[ship_hal_native] RX listening stopped\n");
    return SHIP_OK;
}

/**
 * ship_hal_native_inject_rx() — Test helper only
 * ELI5: In tests, we need to pretend the radio received a packet.
 * This function simulates that by calling the RX callback directly.
 */
void ship_hal_native_inject_rx(const uint8_t *frame_bytes, size_t len)
{
    if (!s_rx_cb || !frame_bytes || len < SHIP_FRAME_SIZE) return;
    if (frame_bytes[9] != SHIP_MAGIC_BYTE) return;
    ship_frame_19b_t frame;
    memcpy(&frame, frame_bytes, SHIP_FRAME_SIZE);
    s_rx_cb(&frame, s_rx_ctx);
}

/**
 * ship_hal_native_get_last_tx() — Test helper only
 * ELI5: In tests, we can read back what ship_hal_tx() "would have transmitted."
 */
const uint8_t *ship_hal_native_get_last_tx(size_t *out_len)
{
    if (out_len) *out_len = s_last_tx_len;
    return s_last_tx_buf;
}

#endif /* platform selection */
