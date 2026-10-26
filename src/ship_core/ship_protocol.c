/*
 * ============================================================================
 * SHiP v0.1 — ship_protocol.c
 * ============================================================================
 *
 * LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
 * -----------------------------------------------
 * This file is the engine room behind the 19-byte SHiP radio whisper.
 *
 * It does four jobs:
 *
 * 1. PACKING THE ENVELOPE (ship_frame_encode):
 *    Like filling out a 19-slot form before dropping it in a mailbox. Each slot
 *    gets its value, then the last two slots get a secret stamp (the HMAC — a
 *    16-bit security checksum) that proves the form wasn't forged.
 *
 * 2. OPENING & VERIFYING THE ENVELOPE (ship_frame_decode):
 *    When a device receives a 19-byte packet, it checks:
 *    (a) Is slot 9 the SHiP magic byte 0xA5? If not, ignore it.
 *    (b) Does the secret stamp match? If not, it's a fake — drop it.
 *    (c) Is the counter higher than the last one accepted? If not, it's a
 *        recorded replay — drop it.
 *
 * 3. COMPUTING THE SECRET STAMP (SipHash-2-4):
 *    SipHash-2-4 is a fast, cryptographically strong "fingerprint" algorithm.
 *    Imagine putting bytes 9–16 of the frame through a special scrambler that
 *    only produces the right 2-byte answer if you have the 16-byte secret key.
 *    Even changing a single bit of the data or key gives a completely different
 *    answer — so attackers can't forge it.
 *
 * 4. MESH REAL-TIME CLOCK (Mesh-RTC):
 *    Once per minute, one device broadcasts a time-sync frame (domain = 0xF).
 *    Any device that hears it updates its local clock if the difference is ≥ 2
 *    seconds. This keeps all clocks aligned without Wi-Fi or a hub.
 *
 * ============================================================================
 *
 * LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
 * ============================================================================
 * SipHash-2-4: Reference — Aumasson & Bernstein, 2012. 128-bit key, 64-bit
 * output, truncated to 16 bits for regular SHiP frames and 8 bits for RTC.
 *
 * Anti-replay: a 16-bit monotonic counter window. A received frame is accepted
 * only if its counter > last accepted counter OR the counter is within the
 * resync window (a rollover from 0xFFFF→0x0000 increments the epoch).
 * ============================================================================
 */

#include "ship_protocol.h"
#include <string.h>

/* ============================================================================
 * INTERNAL: SipHash-2-4 full 64-bit implementation
 * ELI5: A very fast, very secure mixing function. We run it on 8 bytes of
 * frame payload + the 16-byte secret key, then keep only the bottom 2 bytes
 *    as our 16-bit HMAC stamp (8 payload bytes, or 9 bytes for Mesh-RTC).
 * ============================================================================ */

/* Rotate left a 64-bit value by 'n' bits */
static inline uint64_t rotl64(uint64_t x, int n)
{
    return (x << n) | (x >> (64 - n));
}

/* One SipRound: mixes the four internal state words */
#define SIPHASH_ROUND(v0, v1, v2, v3) \
    do { \
        (v0) += (v1); (v1) = rotl64((v1), 13); (v1) ^= (v0); (v0) = rotl64((v0), 32); \
        (v2) += (v3); (v3) = rotl64((v3), 16); (v3) ^= (v2); \
        (v0) += (v3); (v3) = rotl64((v3), 21); (v3) ^= (v0); \
        (v2) += (v1); (v1) = rotl64((v1), 17); (v1) ^= (v2); (v2) = rotl64((v2), 32); \
    } while (0)

uint16_t ship_siphash24_trunc16(const uint8_t *data, size_t len,
                                 const uint8_t key[SHIP_KEY_SIZE])
{
    /* Load the 128-bit key as two 64-bit little-endian words */
    uint64_t k0 = 0, k1 = 0;
    for (int i = 0; i < 8; i++) {
        k0 |= (uint64_t)key[i]     << (i * 8);
        k1 |= (uint64_t)key[i + 8] << (i * 8);
    }

    /* ELI5: Initialise the four mixing jars with SipHash magic constants */
    uint64_t v0 = k0 ^ 0x736f6d6570736575ULL;
    uint64_t v1 = k1 ^ 0x646f72616e646f6dULL;
    uint64_t v2 = k0 ^ 0x6c7967656e657261ULL;
    uint64_t v3 = k1 ^ 0x7465646279746573ULL;

    /* Process complete 8-byte blocks */
    size_t nblocks = len / 8;
    for (size_t i = 0; i < nblocks; i++) {
        uint64_t m = 0;
        for (int j = 0; j < 8; j++) {
            m |= (uint64_t)data[i * 8 + j] << (j * 8);
        }
        v3 ^= m;
        SIPHASH_ROUND(v0, v1, v2, v3); /* 2 compression rounds */
        SIPHASH_ROUND(v0, v1, v2, v3);
        v0 ^= m;
    }

    /* Process remaining bytes + encode total length in the last word */
    uint64_t last = (uint64_t)(len & 0xFF) << 56;
    size_t remaining = len % 8;
    const uint8_t *tail = data + nblocks * 8;
    for (size_t i = 0; i < remaining; i++) {
        last |= (uint64_t)tail[i] << (i * 8);
    }
    v3 ^= last;
    SIPHASH_ROUND(v0, v1, v2, v3);
    SIPHASH_ROUND(v0, v1, v2, v3);
    v0 ^= last;

    /* Finalisation: 4 finalization rounds */
    v2 ^= 0xFF;
    SIPHASH_ROUND(v0, v1, v2, v3);
    SIPHASH_ROUND(v0, v1, v2, v3);
    SIPHASH_ROUND(v0, v1, v2, v3);
    SIPHASH_ROUND(v0, v1, v2, v3);

    uint64_t result = v0 ^ v1 ^ v2 ^ v3;

    /* Truncate to 16 bits: keep the bottom two bytes of the 64-bit digest */
    return (uint16_t)(result & 0xFFFFu);
}

/* ============================================================================
 * ship_frame_encode()
 * ELI5: Fills all 19 slots of the SHiP envelope and stamps it with the HMAC.
 * ============================================================================ */
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
                      const uint8_t key[SHIP_KEY_SIZE])
{
    if (!out || !key) return SHIP_ERR_NULL_PTR;
    if ((domain & 0x0Fu) == SHIP_DOMAIN_MESH_CLOCK) {
        return SHIP_ERR_INVALID_DOMAIN;
    }

    ship_frame_19b_t f;
    memset(&f, 0, sizeof(f));

    /* Choose Frame Control Field based on destination */
    f.fcf         = (dst_short == SHIP_ADDR_BROADCAST) ? SHIP_FCF_BROADCAST
                                                        : SHIP_FCF_UNICAST;
    f.seq_num     = seq_num;
    f.pan_id      = pan_id;
    f.dst_short   = dst_short;
    f.src_short   = src_short;
    f.ship_magic  = SHIP_MAGIC_BYTE;
    f.group_id    = group_id;
    f.replay_counter = counter;
    /* Pack domain into upper nibble, gesture into lower nibble */
    f.cmd_click   = (uint8_t)(((domain & 0x0Fu) << 4) | (gesture & 0x0Fu));
    f.param_1     = p1;
    f.param_2     = p2;
    f.param_3     = p3;

    /* Compute HMAC over the 8-byte SHiP application payload (bytes 9–16) */
    const uint8_t *payload_start = (const uint8_t *)&f + offsetof(ship_frame_19b_t, ship_magic);
    /* payload length = from ship_magic through param_3 = 8 bytes */
    uint16_t mac = ship_siphash24_trunc16(payload_start, 8, key);
    f.hmac_trunc16 = mac;

    memcpy(out, &f, SHIP_FRAME_SIZE);
    return (int)SHIP_FRAME_SIZE;
}

int ship_frame_encode_rtc(uint8_t *out,
                          uint16_t pan_id,
                          uint16_t src_short,
                          uint8_t seq_num,
                          uint8_t group_id,
                          uint32_t utc_epoch,
                          uint16_t counter,
                          const uint8_t key[SHIP_KEY_SIZE])
{
    if (!out || !key) return SHIP_ERR_NULL_PTR;

    ship_frame_19b_t f;
    memset(&f, 0, sizeof(f));
    f.fcf = SHIP_FCF_BROADCAST;
    f.seq_num = seq_num;
    f.pan_id = pan_id;
    f.dst_short = SHIP_ADDR_BROADCAST;
    f.src_short = src_short;
    f.ship_magic = SHIP_MAGIC_BYTE;
    f.group_id = group_id;
    f.replay_counter = counter;
    f.cmd_click = (uint8_t)((SHIP_DOMAIN_MESH_CLOCK << 4) | SHIP_GESTURE_CLICK_1);
    f.param_1 = (uint8_t)(utc_epoch & 0xFFu);
    f.param_2 = (uint8_t)((utc_epoch >> 8) & 0xFFu);
    f.param_3 = (uint8_t)((utc_epoch >> 16) & 0xFFu);
    f.hmac_trunc16 = (uint16_t)(utc_epoch >> 24);

    const uint8_t *payload_start =
        (const uint8_t *)&f + offsetof(ship_frame_19b_t, ship_magic);
    uint16_t mac = ship_siphash24_trunc16(payload_start, 9, key);
    f.hmac_trunc16 |= (uint16_t)((mac & 0xFFu) << 8);

    memcpy(out, &f, SHIP_FRAME_SIZE);
    return (int)SHIP_FRAME_SIZE;
}

/* ============================================================================
 * ship_frame_decode()
 * ELI5: Opens and validates an incoming 19-byte SHiP envelope.
 *       Returns SHIP_OK only if the magic byte, HMAC, and replay counter
 *       are all correct. Otherwise returns an error code.
 * ============================================================================ */
int ship_frame_decode(const uint8_t *frame,
                      size_t len,
                      const uint8_t key[SHIP_KEY_SIZE],
                      uint16_t last_ctr,
                      ship_frame_19b_t *out)
{
    if (!frame || !key || !out) return SHIP_ERR_NULL_PTR;
    if (len < SHIP_FRAME_SIZE)  return SHIP_ERR_BAD_LENGTH;

    ship_frame_19b_t f;
    memcpy(&f, frame, SHIP_FRAME_SIZE);

    /* Step 1: Check magic discriminator byte */
    if (f.ship_magic != SHIP_MAGIC_BYTE) return SHIP_ERR_BAD_MAGIC;

    /* Step 2: Re-compute HMAC over the domain-specific authenticated bytes. */
    const uint8_t *payload_start = (const uint8_t *)&f + offsetof(ship_frame_19b_t, ship_magic);

    uint8_t domain_nibble = (f.cmd_click >> 4) & 0x0Fu;

    if (domain_nibble == SHIP_DOMAIN_MESH_CLOCK) {
        uint16_t expected_mac = ship_siphash24_trunc16(payload_start, 9, key);
        if ((uint8_t)expected_mac != (uint8_t)(f.hmac_trunc16 >> 8)) {
            return SHIP_ERR_BAD_HMAC;
        }
    } else {
        uint16_t expected_mac = ship_siphash24_trunc16(payload_start, 8, key);
        if (expected_mac != f.hmac_trunc16) return SHIP_ERR_BAD_HMAC;
    }

    /* Step 3: Anti-replay counter check.
     * Accept if incoming counter > last accepted counter.
     * Allow a resync window of 4 frames to handle counter rollover (0xFFFF→0). */
    uint16_t ctr = f.replay_counter;
    bool counter_ok;
    if (last_ctr <= 0xFFFCu) {
        /* Normal range: strictly monotonic */
        counter_ok = (ctr > last_ctr);
    } else {
        /* Near rollover: accept both higher values and values in [0, 8] (rollover) */
        counter_ok = (ctr > last_ctr) || (ctr <= 8u);
    }
    if (!counter_ok) return SHIP_ERR_REPLAY;

    *out = f;
    return SHIP_OK;
}

/* ============================================================================
 * ship_leat_match()
 * ELI5: Searches the device's cheat-sheet (LEAT) to find a rule that matches
 *       the incoming frame. Also checks the time-of-day window if the Mesh-RTC
 *       is synced. Returns the matched entry index, or -1 for no match.
 * ============================================================================ */
int ship_leat_match(const ship_leat_entry_t *table,
                    size_t count,
                    const ship_frame_19b_t *frame,
                    const ship_mesh_rtc_t *rtc)
{
    if (!table || !frame || !rtc) return -1;

    uint8_t frame_domain  = (frame->cmd_click >> 4) & 0x0Fu;
    uint8_t frame_gesture = frame->cmd_click & 0x0Fu;

    /* Get current hour if clock is synced */
    uint8_t current_hour = 0;
    bool time_available = rtc->is_synced;
    if (time_available) {
        /* UTC seconds → hour of day (simplified, no timezone conversion here) */
        current_hour = (uint8_t)((rtc->utc_epoch % 86400UL) / 3600UL);
    }

    for (size_t i = 0; i < count; i++) {
        const ship_leat_entry_t *e = &table[i];
        if (!e->enabled) continue;
        if (e->group_id != frame->group_id)   continue;
        if (e->domain   != frame_domain)       continue;
        if (e->gesture  != frame_gesture)      continue;

        /* Time-of-day window check */
        if (time_available) {
            bool in_window;
            if (e->hour_start <= e->hour_end) {
                /* Normal window: e.g., 22:00–23:00 */
                in_window = (current_hour >= e->hour_start &&
                             current_hour <= e->hour_end);
            } else {
                /* Overnight window: e.g., 22:00–06:00 */
                in_window = (current_hour >= e->hour_start ||
                             current_hour <= e->hour_end);
            }
            if (!in_window) continue;
        }

        return (int)i;
    }
    return -1;
}

/* ============================================================================
 * ship_rtc_update()
 * ELI5: If the time difference between the incoming clock-sync frame and our
 *       local clock is ≥ 2 seconds, we update our clock. Otherwise we trust
 *       ours. This prevents constant tiny adjustments from noisy frames.
 * ============================================================================ */
void ship_rtc_update(ship_mesh_rtc_t *rtc,
                     const ship_frame_19b_t *frame,
                     uint32_t local_mono_ms)
{
    if (!rtc || !frame) return;
    if (((frame->cmd_click >> 4) & 0x0Fu) != SHIP_DOMAIN_MESH_CLOCK) return;

    /* The epoch occupies bytes 14–17; byte 18 carries its one-byte tag. */
    uint32_t incoming_epoch = (uint32_t)frame->param_1
                            | ((uint32_t)frame->param_2 << 8)
                            | ((uint32_t)frame->param_3 << 16)
                            | ((uint32_t)(frame->hmac_trunc16 & 0xFFu) << 24);

    if (!rtc->is_synced) {
        /* First sync: accept unconditionally */
        rtc->utc_epoch    = incoming_epoch;
        rtc->last_sync_ms = local_mono_ms;
        rtc->is_synced    = true;
        return;
    }

    /* Compute our current estimated time */
    uint32_t elapsed_s = (local_mono_ms - rtc->last_sync_ms) / 1000UL;
    uint32_t our_epoch = rtc->utc_epoch + elapsed_s;

    /* Only correct if drift is >= SHIP_RTC_MAX_DRIFT_S seconds */
    uint32_t drift = (incoming_epoch > our_epoch) ? (incoming_epoch - our_epoch)
                                                  : (our_epoch - incoming_epoch);
    if (drift >= SHIP_RTC_MAX_DRIFT_S) {
        rtc->utc_epoch    = incoming_epoch;
        rtc->last_sync_ms = local_mono_ms;
    }
}

/* ============================================================================
 * ship_rtc_now()
 * ELI5: Returns our best guess at the current UTC time by adding how many
 *       seconds have elapsed since our last clock sync.
 * ============================================================================ */
uint32_t ship_rtc_now(const ship_mesh_rtc_t *rtc, uint32_t local_mono_ms)
{
    if (!rtc || !rtc->is_synced) return 0;
    uint32_t elapsed_s = (local_mono_ms - rtc->last_sync_ms) / 1000UL;
    return rtc->utc_epoch + elapsed_s;
}

/* ============================================================================
 * ORPHANED-MESH HEARTBEAT  (v0.1.18 — Pillar 1)
 * ELI5: The "liveness" layer for a multi-hop chain. Each hop only refreshes
 *       its last-seen clock when a frame has ALREADY passed ship_frame_decode()
 *       (magic + SipHash + anti-replay). A recorded/replayed frame never
 *       reaches ship_heartbeat_feed(), so it can never fake a heartbeat. From
 *       the gap since the last genuine frame we derive HEALTHY / DEGRADED /
 *       ORPHANED, which the UI turns into a degradation flag + "Re-handshake".
 * ============================================================================ */

void ship_heartbeat_feed(ship_hb_state_t *hop,
                         uint16_t counter,
                         uint32_t local_mono_ms)
{
    if (!hop) return;
    /* A genuine frame just arrived (decode already passed) — mark it fresh. */
    hop->last_counter= counter;
    hop->last_seen_ms = local_mono_ms;
    hop->state = SHIP_HB_HEALTHY;
}

ship_hb_health_t ship_heartbeat_check(ship_hb_state_t *hop,
                                      uint32_t now_ms,
                                      uint32_t *out_missed)
{
    ship_hb_health_t state;
    uint32_t missed = 0;

    if (!hop) {
        if (out_missed) *out_missed = 0;
        return SHIP_HB_UNKNOWN;
    }

     /* ELI5: how many heartbeat windows have elapsed since the last genuine
       * frame. A hop that has never heard one (last_seen_ms == 0) is UNKNOWN —
       * it needs a re-handshake to establish its baseline, so it is never
       * reported HEALTHY. */
    if (hop->last_seen_ms == 0) {
        state = SHIP_HB_UNKNOWN;
     } else {
        /* ELI5 (Fix C): if the local clock skews *backward* (now_ms is earlier
         * than the last genuine frame), the unsigned subtraction would wrap to a
         * huge value and falsely report the hop ORPHANED. Clamp elapsed to 0 so
         * a skewed clock is treated as "just saw it" — matching the Python
         * mirror, which clamps a negative missed count to 0. */
        uint64_t elapsed =
              (uint64_t)now_ms >= (uint64_t)hop->last_seen_ms
                 ? (uint64_t)now_ms - (uint64_t)hop->last_seen_ms
                 : 0;
        missed = (uint32_t)(elapsed / SHIP_HEARTBEAT_INTERVAL_MS);
        if (missed <= (SHIP_HEARTBEAT_DEGRADED - 1)) {
            state = SHIP_HB_HEALTHY;
          } else if (missed == SHIP_HEARTBEAT_DEGRADED) {
            state = SHIP_HB_DEGRADED;
          } else {
            state = SHIP_HB_ORPHANED;
          }
     }
    hop->state = state;
    if (out_missed) *out_missed = missed;
    return state;
}

bool ship_heartbeat_rehandshake_needed(const ship_hb_state_t *hop, uint32_t now_ms)
{
    if (!hop) return true;
    return ship_heartbeat_check((ship_hb_state_t *)hop, now_ms, NULL) != SHIP_HB_HEALTHY;
}

/* ============================================================================
 * ship_burst_reset() / ship_burst_accept()   (v0.1.20 — burst debounce)
 * ELI5: A sliding 500 ms window. The first tap opens the window; up to
 *       SHIP_BURST_MAX_TAPS taps pass through; anything beyond that inside
 *       the window is swallowed so a rapid-fire press can never storm the
 *       radio. After the window elapses (500 ms gap) the count resets.
 * ==========================================================================*/

void ship_burst_reset(ship_burst_state_t *b, uint32_t now_ms)
{
    if (!b) return;
    b->window_start_ms = now_ms;
    b->taps_in_window  = 0;
}

bool ship_burst_accept(ship_burst_state_t *b, uint32_t now_ms)
{
    if (!b) return false;

     /* ELI5: first tap, or a gap long enough that the window has elapsed,
     * opens a fresh window and is always accepted. */
    if (b->taps_in_window == 0 ||
        ((uint32_t)now_ms - b->window_start_ms) > SHIP_BURST_WINDOW_MS) {
        b->window_start_ms = now_ms;
        b->taps_in_window  = 1;
        b->accepted++;
        return true;
    }

    /* ELI5: a tap inside the live window. Count it; once we exceed the
     * per-window maximum the extra taps are the storm and are swallowed. */
    b->taps_in_window++;
    if (b->taps_in_window > SHIP_BURST_MAX_TAPS) {
        b->dropped++;
        return false;
    }
    b->accepted++;
    return true;
}
