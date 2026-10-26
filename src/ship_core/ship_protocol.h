/*
 * ============================================================================
 * SHiP v0.1 — ship_protocol.h
 * ============================================================================
 *
 * LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
 * -----------------------------------------------
 * Think of this file as the "language rulebook" for the SHiP radio whisper.
 *
 * Imagine two walkie-talkies on the same channel. For them to understand each
 * other, both must agree on a common language: which button press means "turn
 * on the lights," which means "close the blinds," and so on. This file writes
 * down that entire language in a single tiny 19-word sentence (19 bytes).
 *
 * The "19 bytes" are like 19 precisely labelled mailbox slots. Every smart
 * device that speaks SHiP knows exactly which slot holds the security stamp,
 * which holds the command type, and which holds the dial-up value. Because the
 * rulebook is only 19 bytes, a device can shout its command over radio in
 * under 1 millisecond—far faster than a human blink.
 *
 * Key ideas in plain English:
 *  - "Group ID": Like a radio channel number. All devices tuned to the same
 *    group hear the same shouts.
 *  - "Replay Counter": A stamp that counts up so nobody can record an old shout
 *    and replay it to trick a device (anti-replay guard).
 *  - "HMAC" (pronounced H-MAC): A tiny secret signature that proves the shout
 *    is genuine and hasn't been tampered with.
 *  - "LEAT" (Local Event-Action Table): A cheat-sheet burned into each device's
 *    memory that says "when I hear THIS shout, do THAT"—no hub needed.
 *  - "Mesh-RTC" (Mesh Real-Time Clock): All SHiP devices keep the same clock by
 *    whispering the current time to each other over the radio, so time-of-day
 *    rules still work even if your router is unplugged.
 *  - "Domain": The category of the thing being controlled (lights, water valve,
 *    smart lock, HVAC, window shades, etc.).
 *
 * ============================================================================
 *
 * LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
 * --------------------------------------------
 * IEEE 802.15.4 O-QPSK PHY, 250 kbps, 2.4 GHz band.
 * Total Software Frame: 19 Bytes (packed, little-endian) + 2-byte hardware-
 * appended CRC-16/ITU-T FCS = 21-byte PSDU.
 * Total on-air time @ 250 kbps:
 *   (6 B Preamble+SFD+PHR + 21 B PSDU) × 32 µs/byte = 0.864 ms  (<1 ms!)
 * ============================================================================
 */

#pragma once
#ifndef SHIP_PROTOCOL_H
#define SHIP_PROTOCOL_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * VERSION
 * ============================================================================ */
#define SHIP_VERSION_MAJOR   0
#define SHIP_VERSION_MINOR   1
#define SHIP_MAGIC_BYTE      0xA5u   /* 'SHiP v0.1' fast-path discriminator   */
#define SHIP_FRAME_SIZE      19u     /* Total software frame size in bytes      */
#define SHIP_PSDU_SIZE       21u     /* PSDU = 19B frame + 2B hardware FCS      */

/* ============================================================================
 * IEEE 802.15.4 MAC FRAME CONTROL FIELD VALUES
 * ELI5: The first two bytes of every radio packet describe what kind of packet
 * it is. 0x8841 means "broadcast to everyone on our channel, no receipt needed."
 * 0x8861 means "direct message to one device, please confirm receipt."
 * ============================================================================ */
#define SHIP_FCF_BROADCAST   0x8841u /* Data | Intra-PAN compress | Short addr | No ACK */
#define SHIP_FCF_UNICAST     0x8861u /* Data | Intra-PAN compress | Short addr | ACK req */

/* Broadcast destination short address (all devices on the PAN hear this) */
#define SHIP_ADDR_BROADCAST  0xFFFFu

/* ============================================================================
 * DOMAIN / COMMAND NIBBLE  (upper 4 bits of cmd_click byte)
 * ELI5: Think of this like the category icon on a remote control:
 *   a lightbulb icon, a tap icon, a padlock icon, etc.
 * ============================================================================ */
typedef enum {
    SHIP_DOMAIN_ONOFF          = 0x1, /* Light switches, plug sockets               */
    SHIP_DOMAIN_LIGHTING_COMBO = 0x2, /* Dimmer level + colour temperature together */
    SHIP_DOMAIN_SCENE_RECALL   = 0x3, /* "Movie Night" scene: lights+shades+temp    */
    SHIP_DOMAIN_WATER_VALVE    = 0x4, /* Water / gas shutoff valves                 */
    SHIP_DOMAIN_SMART_LOCK     = 0x5, /* Door locks                                 */
    SHIP_DOMAIN_HVAC_CLIMATE   = 0x6, /* Thermostat / HVAC systems                  */
    SHIP_DOMAIN_WINDOW_SHADE   = 0x7, /* Motorised roller blinds / shades           */
    SHIP_DOMAIN_ENERGY_SHED    = 0x8, /* Load-shedding (emergency power save)       */
    SHIP_DOMAIN_MESH_CLOCK     = 0xF, /* Mesh Real-Time Clock (Mesh-RTC) sync pulse */
} ship_domain_t;

/* ============================================================================
 * GESTURE / FLAG NIBBLE  (lower 4 bits of cmd_click byte)
 * ELI5: This says HOW the button was pressed:
 *   1 click, 2 clicks (double-tap), ... 5 clicks, long hold, release, or
 *   a special "don't echo back" lock flag.
 * ============================================================================ */
typedef enum {
    SHIP_GESTURE_CLICK_1       = 1,  /* Single press                               */
    SHIP_GESTURE_CLICK_2       = 2,  /* Double tap                                 */
    SHIP_GESTURE_CLICK_3       = 3,  /* Triple tap                                 */
    SHIP_GESTURE_CLICK_4       = 4,  /* Quadruple tap                              */
    SHIP_GESTURE_CLICK_5       = 5,  /* Quintuple tap (5× click)                   */
    SHIP_GESTURE_HOLD          = 6,  /* Long-press hold                            */
    SHIP_GESTURE_RELEASE       = 7,  /* Button released after hold                 */
    SHIP_GESTURE_NO_STATUS_SHARE = 8,/* "Do not echo status back" — Tasmota-style  */
                                     /* NoStatusShare scene override lock          */
} ship_gesture_t;

/* ============================================================================
 * THE 19-BYTE SHiP MICRO-PACKET  (ship_frame_19b_t)
 * ELI5: This is the exact 19-word sentence every SHiP device shouts over radio.
 * Each labelled slot has one specific job, like fields on a tiny form.
 * The whole form fits in a single short radio burst lasting under 1 millisecond.
 * ============================================================================ */
typedef struct __attribute__((packed)) {
    /* ---- 802.15.4 MAC Header (9 bytes) ---- */
    uint16_t fcf;            /* Bytes  0–1:  Frame Control Field (see SHIP_FCF_*)      */
    uint8_t  seq_num;        /* Byte   2:    802.15.4 MAC sequence number (0–255)       */
    uint16_t pan_id;         /* Bytes  3–4:  Active PAN ID (same as Thread/Zigbee net)  */
    uint16_t dst_short;      /* Bytes  5–6:  0xFFFF = group broadcast, else unicast     */
    uint16_t src_short;      /* Bytes  7–8:  Sender's 16-bit short address              */

    /* ---- SHiP Application Payload (10 bytes) ---- */
    uint8_t  ship_magic;     /* Byte   9:    0xA5 — identifies this as a SHiP v0.1 frame*/
    uint8_t  group_id;       /* Byte  10:    SHiP Device Group ID (0–255)               */
    uint16_t replay_counter; /* Bytes 11–12: Monotonic counter for anti-replay/dedup     */
    uint8_t  cmd_click;      /* Byte  13:    Bits[7:4]=Domain, Bits[3:0]=Gesture/Flag   */
    uint8_t  param_1;        /* Byte  14:    Primary param:
                              *               OnOff:   relay bitmask
                              *               Lighting: brightness 0–255
                              *               Scene:   scene ID
                              *               Valve:   open percentage 0–100
                              *               Lock:    0=lock, 1=unlock
                              *               HVAC:    temperature setpoint (°C×2)
                              *               Shade:   lift position 0–100%
                              *               MeshRTC: UTC epoch byte 0 (LSB)           */
    uint8_t  param_2;        /* Byte  15:    Secondary param:
                              *               Lighting: colour temperature (Mireds)
                              *               HVAC:    fan speed 0–5
                              *               Shade:   tilt angle 0–100%
                              *               MeshRTC: UTC epoch byte 1                  */
    uint8_t  param_3;        /* Byte  16:    Tertiary param:
                              *               Lighting: transition fade time (×100ms)
                              *               DGR:     share mask override
                              *               Safety:  emergency override flags
                              *               MeshRTC: UTC epoch byte 2                  */
    uint16_t hmac_trunc16;   /* Bytes 17–18: Truncated 16-bit SipHash-2-4 (or AES-CCM*)
                              *              MAC over bytes 9–16 + 128-bit group key.
                              *              When cmd_click domain == 0xF (Mesh_Clock):
                              * Byte 17 (hmac_trunc16 lower byte): UTC timestamp byte 3 (MSB)
                              * Byte 18 (hmac_trunc16 upper byte): 8-bit SipHash-2-4 HMAC
                              * (compact signature over bytes 9..17).                  */
} ship_frame_19b_t;

/* Compile-time guarantee: the frame must be exactly 19 bytes. */
_Static_assert(sizeof(ship_frame_19b_t) == SHIP_FRAME_SIZE,
               "SHiP frame must be exactly 19 bytes!");

/* ============================================================================
 * LOCAL EVENT-ACTION TABLE (LEAT)
 * ELI5: This is the "cheat sheet" stored inside each device's memory.
 * When the device hears a SHiP radio whisper matching a row in this table,
 * it immediately does the action listed—without asking anyone for permission.
 * Up to SHIP_LEAT_MAX_ENTRIES rules can be stored per device.
 * ============================================================================ */
#define SHIP_LEAT_MAX_ENTRIES  32u

typedef struct {
    uint8_t  group_id;       /* Which SHiP group triggers this rule                 */
    uint8_t  domain;         /* Which domain nibble to match (ship_domain_t)        */
    uint8_t  gesture;        /* Which gesture nibble to match (ship_gesture_t)      */
    uint8_t  action_type;    /* 0=relay, 1=brightness, 2=scene, 3=valve, 4=climate  */
    uint8_t  action_param;   /* Action payload (e.g. brightness level, scene ID)    */
    uint8_t  hour_start;     /* Time-of-day rule: active from this hour (0–23)      */
    uint8_t  hour_end;       /* Time-of-day rule: active until this hour (0–23)     */
    bool     enabled;        /* Whether this row is currently active                */
} ship_leat_entry_t;

/* ============================================================================
 * MESH REAL-TIME CLOCK (Mesh-RTC)
 * ELI5: All SHiP devices share a clock by occasionally broadcasting the current
 * time. Any device that drifts off by more than 2 seconds gets corrected by the
 * next clock-sync whisper it hears. This means time-of-day rules ("dim lights
 * at 2 AM") keep working even if every router and server in the home is off.
 * ============================================================================ */
typedef struct {
    uint32_t utc_epoch;      /* Current UTC Unix timestamp (seconds since 1970)     */
    uint32_t last_sync_ms;   /* Local monotonic ms timestamp when last synced        */
    bool     is_synced;      /* True once at least one sync frame has been received  */
} ship_mesh_rtc_t;

#define SHIP_RTC_SYNC_INTERVAL_S   60u   /* Broadcast RTC sync every 60 seconds          */
#define SHIP_RTC_MAX_DRIFT_S        2u   /* Accept correction if drift ≥ 2 seconds        */
/* ============================================================================
 * ORPHANED-MESH HEARTBEAT  (v0.1.18 — Pillar 1)
 * ELI5: A multi-hop chain only works while every link still hears the next
 * device. A "heartbeat" is each hop remembering *when* it last heard a
 * genuine (HMAC-checked) frame. If a relay goes quiet for too long, the hop
 * ahead of it cannot tell "the relay is asleep" from "the relay is dead" —
 * so the whole downstream branch silently goes dark. To catch that, every hop
 * counts how many heartbeat intervals it has MISSED since the last genuine
 * frame. Missed 0–1 intervals → HEALTHY; 2 → DEGRADED (warn the user); 3 or
 * more → ORPHANED (the branch is severed, flag it and offer a re-handshake).
 *
 * The "cryptographic" half is that a hop only refreshes its last-seen clock
 * when a frame PAST ship_frame_decode() — i.e. its SipHash tag matched and its
 * replay nonce advanced — so a recorded/replayed frame can never fake a
 * heartbeat. A hop is "re-handshake-able" whenever its state is anything but
 * HEALTHY: the UI then shows a degradation flag and a "Re-handshake" action.
 * ============================================================================ */

/* ELI5: how often a genuine frame must arrive. Mirrors the 60s Mesh-RTC
 * sync so a single heartbeat budget covers one clock-sync period. */
#define SHIP_HEARTBEAT_INTERVAL_MS  60000u /* One heartbeat window = 60 s            */
/* ELI5: thresholds, expressed as a number of MISSED intervals. 0–1 missed =
 * healthy, 2 missed = degraded (warn), 3+ missed = orphaned (severed). */
#define SHIP_HEARTBEAT_DEGRADED   2u       /* 2 missed intervals → degrade/warn      */
#define SHIP_HEARTBEAT_ORPHANED   3u       /* 3 missed intervals → orphan/severed     */

/* ELI5: the four health states a hop can be in. */
typedef enum {
    SHIP_HB_UNKNOWN   = 0,    /* Never heard a genuine frame yet           */
    SHIP_HB_HEALTHY   = 1,    /* Heard a genuine frame within 1 window     */
    SHIP_HB_DEGRADED = 2,     /* Heard a frame 2 windows ago — warn        */
    SHIP_HB_ORPHANED = 3,     /* Heard a frame 3+ windows ago — severed    */
} ship_hb_health_t;

/* ELI5: one row in the live routing/heartbeat table. Each hop remembers the
 * last genuine frame it heard (its nonce + local timestamp) and caches its
 * health verdict. This is what ship_diagnose_mesh reads and the dashboard
 * turns into a degradation flag. */
typedef struct {
    uint16_t        hop_index;    /* Which hop in the chain/DAG (1-based)    */
    uint16_t        last_counter; /* Last accepted anti-replay nonce         */
    uint32_t        last_seen_ms; /* Local monotonic ms of last genuine frame*/
    ship_hb_health_t state;        /* Cached health verdict (0..3 above)     */
} ship_hb_state_t;

/* ============================================================================
 * BURST DEBOUNCE   (v0.1.20 — physical edge-case hardening)
 * ELI5: A person can mash a button far faster than a single gesture means.
 *       A 5-tap *intent* is one thing; a 30-tap *stumble* is a bug. If a
 *       device turned every tap into a separate radio command, a clumsy
 *       press could flood the mesh with a "command storm". The burst
 *       debounce is a sliding 500 ms window: the first tap opens it and
 *       passes, up to SHIP_BURST_MAX_TAPS taps are allowed through, and
 *       anything beyond that inside the window is SWALLOWED so the radio
 *       stays quiet. Only genuine (already-decoded) taps count, so a
 *       replay can never manufacture a fake burst either.
 * ==========================================================================*/

/* ELI5: 5+ taps in under 500 ms is treated as one burst, not 5 commands. */
#define SHIP_BURST_MAX_TAPS     5u        /* taps per window before dropping   */
#define SHIP_BURST_WINDOW_MS    500u      /* the sliding window: 500 ms        */

/* ELI5: one row of burst-debounce state kept per (group, gesture) lane. */
typedef struct {
    uint32_t window_start_ms;  /* when the current burst window opened (0=empty)*/
    uint8_t  taps_in_window;   /* genuine taps counted since the window opened  */
    uint16_t accepted;         /* total taps the debounce let through           */
    uint16_t dropped;          /* total taps the debounce swallowed             */
} ship_burst_state_t;

/* ============================================================================
 * SIPHASH-2-4 KEY SIZE
 * ELI5: The secret key is 16 bytes (128 bits) shared among devices in a group.
 * Think of it as the 16-character passphrase that lets devices verify each other.
 * ============================================================================ */
#define SHIP_KEY_SIZE  16u

/* ============================================================================
 * FUNCTION DECLARATIONS
 * ============================================================================ */

/**
 * ship_frame_encode()
 * ELI5: Pack all the fields into the 19-byte envelope ready to transmit.
 *
 * @param out       Output buffer of at least SHIP_FRAME_SIZE bytes.
 * @param pan_id    The active PAN ID (Thread/Zigbee network identifier).
 * @param src_short Sender's 16-bit short address.
 * @param dst_short Destination short address (0xFFFF for broadcast).
 * @param seq_num   802.15.4 sequence number (caller increments each TX).
 * @param group_id  SHiP group identifier (0–255).
 * @param domain    Domain nibble (ship_domain_t cast to uint8_t).
 * @param gesture   Gesture nibble (ship_gesture_t cast to uint8_t).
 * @param p1        param_1 value.
 * @param p2        param_2 value.
 * @param p3        param_3 value.
 * @param counter   Monotonic replay counter.
 * @param key       16-byte group key for HMAC.
 * @return          SHIP_FRAME_SIZE on success, negative on error.
 */
int ship_frame_encode(uint8_t *out,
                      uint16_t pan_id,
                      uint16_t src_short,
                      uint16_t dst_short,
                      uint8_t  seq_num,
                      uint8_t  group_id,
                      uint8_t  domain,
                      uint8_t  gesture,
                      uint8_t  p1,
                      uint8_t  p2,
                      uint8_t  p3,
                      uint16_t counter,
                      const uint8_t key[SHIP_KEY_SIZE]);

/**
 * ship_frame_decode()
 * ELI5: Open the 19-byte envelope and check that the secret stamp is genuine.
 *       If anything looks wrong (bad stamp, replayed counter), return an error.
 *
 * @param frame     Pointer to received 19-byte frame buffer.
 * @param len       Frame buffer length (must be SHIP_FRAME_SIZE).
 * @param key       16-byte group key for HMAC verification.
 * @param last_ctr  Last accepted replay counter for this source.
 * @param out       Parsed frame struct written on success.
 * @return          0 on success, negative error code on failure.
 */
int ship_frame_decode(const uint8_t *frame,
                      size_t len,
                      const uint8_t key[SHIP_KEY_SIZE],
                      uint16_t last_ctr,
                      ship_frame_19b_t *out);

/* Error codes */
#define SHIP_OK                  0
#define SHIP_ERR_BAD_LENGTH     -1
#define SHIP_ERR_BAD_MAGIC      -2
#define SHIP_ERR_BAD_HMAC       -3
#define SHIP_ERR_REPLAY         -4
#define SHIP_ERR_NULL_PTR       -5
#define SHIP_ERR_INVALID_DOMAIN -6

/**
 * ship_siphash24_trunc16()
 * ELI5: Calculates the 16-bit security stamp using the SipHash-2-4 algorithm.
 * Think of it like a secret handshake that both sides must produce identically.
 *
 * @param data      Bytes to authenticate (bytes 9–16 normally, 9–17 for RTC).
 * @param len       Number of bytes (8 normally, 9 for RTC).
 * @param key       16-byte group key.
 * @return          Truncated 16-bit tag.
 */
uint16_t ship_siphash24_trunc16(const uint8_t *data, size_t len,
                                 const uint8_t key[SHIP_KEY_SIZE]);

/**
 * ship_frame_encode_rtc()
 * ELI5: Build a clock-sync envelope with all four UTC bytes and a one-byte
 * security stamp. Clock frames use byte 17 for the epoch's most-significant
 * byte, so their stamp is stored only in byte 18.
 */
int ship_frame_encode_rtc(uint8_t *out,
                          uint16_t pan_id,
                          uint16_t src_short,
                          uint8_t seq_num,
                          uint8_t group_id,
                          uint32_t utc_epoch,
                          uint16_t counter,
                          const uint8_t key[SHIP_KEY_SIZE]);

/* ============================================================================
 * RADIO HAL DECLARATIONS
 * ELI5: These functions connect the same SHiP frame language to a real radio
 * or to the laptop simulator, without callers needing platform-specific APIs.
 * ============================================================================ */
typedef void (*ship_rx_callback_t)(const ship_frame_19b_t *frame, void *user_ctx);

int ship_hal_tx(const uint8_t *ship_frame_19b);
int ship_hal_rx_start(ship_rx_callback_t cb, void *user_ctx);
int ship_hal_rx_stop(void);
void ship_hal_native_inject_rx(const uint8_t *frame_bytes, size_t len);
const uint8_t *ship_hal_native_get_last_tx(size_t *out_len);
void ship_hal_openthread_set_instance(void *instance);

/**
 * ship_leat_match()
 * ELI5: Scan the cheat-sheet (LEAT) for a rule matching this incoming frame.
 *       If found AND the current time falls in the allowed window, fire the action.
 *
 * @param table     Array of LEAT entries.
 * @param count     Number of entries in the table.
 * @param frame     Decoded incoming SHiP frame.
 * @param rtc       Current Mesh-RTC time state.
 * @return          Index of matched entry, or -1 if no match.
 */
int ship_leat_match(const ship_leat_entry_t *table,
                    size_t count,
                    const ship_frame_19b_t *frame,
                    const ship_mesh_rtc_t *rtc);

/**
 * ship_rtc_update()
 * ELI5: If a Mesh-RTC sync frame arrives and it's more than SHIP_RTC_MAX_DRIFT_S
 *       seconds different from our own clock, update our clock to match.
 *
 * @param rtc          Current local RTC state (updated in place).
 * @param frame        The received Mesh-RTC sync frame.
 * @param local_mono_ms Current local monotonic time in milliseconds.
 */
void ship_rtc_update(ship_mesh_rtc_t *rtc,
                     const ship_frame_19b_t *frame,
                     uint32_t local_mono_ms);

/**
 * ship_rtc_now()
 * ELI5: Returns the best-guess current UTC time based on the last sync + elapsed
 *       local time.
 */
uint32_t ship_rtc_now(const ship_mesh_rtc_t *rtc, uint32_t local_mono_ms);

/* ============================================================================
 * ORPHANED-MESH HEARTBEAT FUNCTION DECLARATIONS  (v0.1.18 — Pillar 1)
 * ============================================================================ */

/**
 * ship_heartbeat_feed()
 * ELI5: Called by a hop only AFTER a frame has cleared ship_frame_decode()
 *       (magic + SipHash + anti-replay all passed). It refreshes the hop's
 *       "last genuine frame" clock and nonce and marks the hop HEALTHY. This
 *       is the cryptographic half: a replayed/recorded frame never reaches
 *       here, so it can never fake a heartbeat.
 *
 * @param hop          The per-hop heartbeat state (updated in place).
 * @param counter      The frame's accepted replay counter (the nonce).
 * @param local_mono_ms Local monotonic time in milliseconds.
 */
void ship_heartbeat_feed(ship_hb_state_t *hop,
                          uint16_t counter,
                          uint32_t local_mono_ms);

/**
 * ship_heartbeat_check()
 * ELI5: Recompute a hop's health from how long ago its last genuine frame was.
 *       Returns the new state and, if asked, the number of missed intervals.
 *
 * @param hop          The per-hop heartbeat state (state field updated).
 * @param now_ms       Current local monotonic time in milliseconds.
 * @param out_missed   Optional; if non-NULL, receives the missed-window count.
 * @return             The new ship_hb_health_t for this hop.
 */
ship_hb_health_t ship_heartbeat_check(ship_hb_state_t *hop,
                                       uint32_t now_ms,
                                       uint32_t *out_missed);

/**
 * ship_heartbeat_rehandshake_needed()
 * ELI5: True when a hop needs a "Re-handshake" — i.e. it is anything but
 *       HEALTHY (unknown / degraded / orphaned). The UI uses this to light the
 *       degradation flag and offer the re-handshake action.
 */
bool ship_heartbeat_rehandshake_needed(const ship_hb_state_t *hop, uint32_t now_ms);

/*
 * ship_burst_accept()
 * ELI5: Called by a device for every genuine gesture it wants to act on.
 *        Returns true if the tap should produce a radio command, false if
 *       the debounce swallowed it as part of a burst. The window slides:
 *       once 500 ms pass without a tap, the count resets and the next tap
 *       opens a fresh window. Returns false for a NULL state (defensive).
 *
 * @param b         The per-lane burst state (updated in place).
 * @param now_ms    Current local monotonic time in milliseconds.
 * @return          true = act on this tap; false = swallowed by the burst.
 */
bool ship_burst_accept(ship_burst_state_t *b, uint32_t now_ms);

/*
 * ship_burst_reset()
 * ELI5: forget any in-flight burst so a new gesture sequence starts clean.
 */
void ship_burst_reset(ship_burst_state_t *b, uint32_t now_ms);

#ifdef __cplusplus
}
#endif

#endif /* SHIP_PROTOCOL_H */
