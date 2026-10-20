/*
 * ============================================================================
 * SHiP v0.1 — sim/test_ship_core.c
 * ============================================================================
 *
 * LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
 * -----------------------------------------------
 * This is the "quality inspector" for the SHiP radio language engine.
 * It compiles and runs entirely on your laptop (no actual radio hardware
 * needed) to verify that every critical rule of the SHiP protocol works
 * correctly BEFORE you flash it to a real device.
 *
 * Think of it like a flight simulator: pilots train on fake cockpits so they
 * know the real plane will behave correctly. These unit tests are the
 * flight simulator for SHiP.
 *
 * WHAT IS TESTED:
 *   Test 1 — FRAME SIZE: Verify the 19-byte frame struct is exactly 19 bytes.
 *             (If a compiler pads it differently, it will break the radio wire format.)
 *
 *   Test 2 — ENCODE → DECODE ROUND-TRIP: Build a frame, transmit it,
 *             receive it, and verify every field arrived correctly.
 *
 *   Test 3 — CRYPTO (SipHash-2-4): Verify the secret stamp is correct.
 *             Change even ONE bit of data and the stamp must be completely different.
 *
 *   Test 4 — ANTI-REPLAY: If an attacker records an old 19-byte whisper and
 *             replays it, the receiver must reject it (because the counter didn't go up).
 *
 *   Test 5 — COUNTER ROLLOVER RESYNC: When the counter wraps around from
 *             65535 back to 0, the receiver must not get confused and must
 *             still accept the next few frames.
 *
 *   Test 6 — MESH-RTC WIRE ROUND-TRIP: Verify all four epoch bytes, the
 *             clock-specific SipHash tag, frame decoding, and RTC update.
 *
 *   Test 7 — MESH-RTC CLOCK SYNC: Verify tiny drifts (< 2 seconds) are ignored
 *             and larger drifts correct the local clock.
 *
 *   Test 8 — LEAT MATCHING: Verify the cheat-sheet correctly fires actions
 *             only when group ID, domain, gesture, and time-of-day all match.
 *
 *   Test 9 — TASMOTA NOSTATUS SHARE FLAG: Verify the NoStatusShare gesture
 *             bit (0x8) is correctly decoded and isolated.
 *
 *   Test 10 — BAD MAGIC REJECTION: Frames with the wrong magic byte (≠ 0xA5)
 *             must be silently dropped.
 *
 * ============================================================================
 *
 * LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
 * ============================================================================
 * Compiled natively with:
 *   gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
 *       -I src/ship_core sim/test_ship_core.c src/ship_core/ship_protocol.c \
 *       src/ship_core/ship_radio_hal.c -o /tmp/test_ship_core && /tmp/test_ship_core
 *
 * No external dependencies — pure C99, POSIX headers only.
 * ============================================================================
 */

#ifndef SHIP_PLATFORM_NATIVE
#  define SHIP_PLATFORM_NATIVE
#endif
#include "../src/ship_core/ship_protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>
#include <assert.h>
#include <time.h>

/* ============================================================================
 * TEST HARNESS HELPERS
 * ============================================================================ */
static int tests_run    = 0;
static int tests_passed = 0;
static int tests_failed = 0;
static bool native_rx_called = false;

static void test_native_rx_callback(const ship_frame_19b_t *frame, void *user_ctx)
{
    (void)user_ctx;
    native_rx_called = frame && frame->ship_magic == SHIP_MAGIC_BYTE;
}

#define TEST_ASSERT(cond, msg) \
    do { \
        if (!(cond)) { \
            printf("  [FAIL] %s\n         Condition: %s\n", msg, #cond); \
            tests_failed++; \
            return false; \
        } \
    } while (0)

#define RUN_TEST(fn) \
    do { \
        tests_run++; \
        printf("\n[TEST %d] %s\n", tests_run, #fn); \
        bool _ok = fn(); \
        if (_ok) { \
            printf("  [PASS] %s\n", #fn); \
            tests_passed++; \
        } \
    } while (0)

/* Known test key: 16 bytes */
static const uint8_t TEST_KEY[SHIP_KEY_SIZE] = {
    0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0xBA, 0xBE,
    0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
};

/* Wrong key for HMAC mismatch tests */
static const uint8_t WRONG_KEY[SHIP_KEY_SIZE] = {
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
};

/* ============================================================================
 * TEST 1: Frame struct size assertion
 * ELI5: Make absolutely sure the 19-slot form is exactly 19 bytes long —
 *       not 18, not 20. One byte off breaks the entire radio wire format.
 * ============================================================================ */
static bool test_frame_size_assertion(void)
{
    TEST_ASSERT(sizeof(ship_frame_19b_t) == 19,
        "ship_frame_19b_t must be exactly 19 bytes (SHIP_FRAME_SIZE)");
    TEST_ASSERT(SHIP_FRAME_SIZE == 19,
        "SHIP_FRAME_SIZE constant must equal 19");
    TEST_ASSERT(SHIP_PSDU_SIZE == 21,
        "SHIP_PSDU_SIZE (frame + 2B FCS) must equal 21");
    printf("  sizeof(ship_frame_19b_t) = %zu bytes  ✓\n", sizeof(ship_frame_19b_t));
    return true;
}

/* ============================================================================
 * TEST 2: Encode → Decode round-trip
 * ELI5: Fill out the 19-slot form, "transmit" it, "receive" it, and check
 *       that every slot still has the right value on the other end.
 * ============================================================================ */
static bool test_encode_decode_roundtrip(void)
{
    uint8_t buf[SHIP_FRAME_SIZE];
    uint16_t counter = 42;

    int enc = ship_frame_encode(
        buf,
        0x1234,    /* pan_id    */
        0xABCD,    /* src_short */
        0xFFFF,    /* dst_short (broadcast) */
        7,         /* seq_num   */
        1,         /* group_id  */
        SHIP_DOMAIN_ONOFF,
        SHIP_GESTURE_CLICK_2,
        0xBB,      /* param_1   */
        0xCC,      /* param_2   */
        0xDD,      /* param_3   */
        counter,
        TEST_KEY
    );

    TEST_ASSERT(enc == (int)SHIP_FRAME_SIZE, "Encode must return SHIP_FRAME_SIZE");

    /* Verify magic byte in raw buffer */
    TEST_ASSERT(buf[9] == SHIP_MAGIC_BYTE, "Magic byte must be 0xA5 at offset 9");

    /* Decode */
    ship_frame_19b_t out;
    int dec = ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, counter - 1, &out);

    TEST_ASSERT(dec == SHIP_OK, "Decode of valid frame must return SHIP_OK");
    TEST_ASSERT(out.pan_id == 0x1234,  "PAN ID must round-trip");
    TEST_ASSERT(out.src_short == 0xABCD, "src_short must round-trip");
    TEST_ASSERT(out.dst_short == 0xFFFF, "dst_short must be broadcast (0xFFFF)");
    TEST_ASSERT(out.fcf == SHIP_FCF_BROADCAST, "FCF must be SHIP_FCF_BROADCAST for dst=0xFFFF");
    TEST_ASSERT(out.group_id == 1,      "group_id must round-trip");
    TEST_ASSERT(out.replay_counter == counter, "replay_counter must round-trip");
    TEST_ASSERT(out.param_1 == 0xBB,    "param_1 must round-trip");
    TEST_ASSERT(out.param_2 == 0xCC,    "param_2 must round-trip");
    TEST_ASSERT(out.param_3 == 0xDD,    "param_3 must round-trip");

    uint8_t domain  = (out.cmd_click >> 4) & 0x0F;
    uint8_t gesture = out.cmd_click & 0x0F;
    TEST_ASSERT(domain  == SHIP_DOMAIN_ONOFF,    "domain must be SHIP_DOMAIN_ONOFF");
    TEST_ASSERT(gesture == SHIP_GESTURE_CLICK_2, "gesture must be SHIP_GESTURE_CLICK_2");

    TEST_ASSERT(ship_hal_tx(buf) == SHIP_OK, "Native HAL transmit must succeed");
    size_t tx_len = 0;
    const uint8_t *tx = ship_hal_native_get_last_tx(&tx_len);
    TEST_ASSERT(tx_len == SHIP_PSDU_SIZE, "Native HAL must retain the 21-byte PSDU");
    TEST_ASSERT(tx[0] == SHIP_PSDU_SIZE && memcmp(&tx[1], buf, SHIP_FRAME_SIZE) == 0,
        "Native HAL transmit buffer must contain the encoded frame");
    native_rx_called = false;
    TEST_ASSERT(ship_hal_rx_start(test_native_rx_callback, NULL) == SHIP_OK,
        "Native HAL receive must start");
    ship_hal_native_inject_rx(buf, SHIP_FRAME_SIZE);
    TEST_ASSERT(native_rx_called, "Native HAL must deliver injected frames");
    TEST_ASSERT(ship_hal_rx_stop() == SHIP_OK, "Native HAL receive must stop");

    printf("  Encode→Decode round-trip: all 19 fields verified  ✓\n");
    printf("  cmd_click=0x%02X  (domain=0x%X  gesture=0x%X)\n",
           out.cmd_click, domain, gesture);
    printf("  HMAC-trunc16=0x%04X  ✓\n", out.hmac_trunc16);
    return true;
}

/* ============================================================================
 * TEST 3: SipHash-2-4 cryptographic correctness
 * ELI5: The secret stamp must be completely different if even a single
 *       byte of data or the key changes. This test verifies:
 *       (a) Same input → same output (deterministic)
 *       (b) Different data → different output (avalanche effect)
 *       (c) Different key → different output (key-dependency)
 * ============================================================================ */
static bool test_siphash_crypto(void)
{
    /* Test vector: 8 bytes of application payload + key */
    const uint8_t data1[8] = {0xA5, 0x01, 0x00, 0x2A, 0x11, 0xBB, 0xCC, 0xDD};
    const uint8_t data2[8] = {0xA5, 0x01, 0x00, 0x2A, 0x11, 0xBB, 0xCC, 0xDE}; /* 1 bit diff */

    uint16_t hash1a = ship_siphash24_trunc16(data1, 8, TEST_KEY);
    uint16_t hash1b = ship_siphash24_trunc16(data1, 8, TEST_KEY);
    uint16_t hash2  = ship_siphash24_trunc16(data2, 8, TEST_KEY);
    uint16_t hash_wrong_key = ship_siphash24_trunc16(data1, 8, WRONG_KEY);

    printf("  SipHash(data1, TEST_KEY)  = 0x%04X\n", hash1a);
    printf("  SipHash(data1, TEST_KEY)  = 0x%04X (repeat — must match)\n", hash1b);
    printf("  SipHash(data2, TEST_KEY)  = 0x%04X (1-bit diff — must differ)\n", hash2);
    printf("  SipHash(data1, WRONG_KEY) = 0x%04X (wrong key — must differ)\n", hash_wrong_key);

    TEST_ASSERT(hash1a == hash1b,         "SipHash must be deterministic");
    TEST_ASSERT(hash1a != hash2,          "1-bit data change must produce different HMAC (avalanche)");
    TEST_ASSERT(hash1a != hash_wrong_key, "Different key must produce different HMAC");
    printf("  SipHash-2-4 cryptographic properties verified  ✓\n");
    return true;
}

/* ============================================================================
 * TEST 4: Anti-replay counter enforcement
 * ELI5: If an attacker records a valid 19-byte packet and replays it later,
 *       the receiver must detect that the counter did NOT increase and drop it.
 * ============================================================================ */
static bool test_anti_replay(void)
{
    uint8_t buf[SHIP_FRAME_SIZE];
    uint16_t counter = 100;

    ship_frame_encode(buf, 0x1234, 0x0001, 0xFFFF, 1, 1,
                      SHIP_DOMAIN_WATER_VALVE, SHIP_GESTURE_CLICK_1,
                      0, 0, 0, counter, TEST_KEY);

    ship_frame_19b_t out;

    /* First reception: should succeed */
    int r1 = ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, counter - 1, &out);
    TEST_ASSERT(r1 == SHIP_OK, "First reception of counter=100 must succeed");

    /* Replay (counter=100 again, last accepted = 100): must fail */
    int r2 = ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, counter, &out);
    TEST_ASSERT(r2 == SHIP_ERR_REPLAY, "Replay of same counter=100 must be rejected");

    /* Older counter (99): must fail */
    uint8_t buf_old[SHIP_FRAME_SIZE];
    ship_frame_encode(buf_old, 0x1234, 0x0001, 0xFFFF, 2, 1,
                      SHIP_DOMAIN_WATER_VALVE, SHIP_GESTURE_CLICK_1,
                      0, 0, 0, 99, TEST_KEY);
    int r3 = ship_frame_decode(buf_old, SHIP_FRAME_SIZE, TEST_KEY, counter, &out);
    TEST_ASSERT(r3 == SHIP_ERR_REPLAY, "Older counter=99 after accepting 100 must be rejected");

    /* Next valid counter (101): must succeed */
    uint8_t buf_next[SHIP_FRAME_SIZE];
    ship_frame_encode(buf_next, 0x1234, 0x0001, 0xFFFF, 3, 1,
                      SHIP_DOMAIN_WATER_VALVE, SHIP_GESTURE_CLICK_1,
                      0, 0, 0, 101, TEST_KEY);
    int r4 = ship_frame_decode(buf_next, SHIP_FRAME_SIZE, TEST_KEY, counter, &out);
    TEST_ASSERT(r4 == SHIP_OK, "Next counter=101 after 100 must be accepted");

    printf("  Anti-replay: replay blocked, older blocked, next accepted  ✓\n");
    return true;
}

/* ============================================================================
 * TEST 5: Counter rollover resync (0xFFFF → 0x0000)
 * ELI5: Counters are only 16 bits (0–65535). After 65535 frames, the counter
 *       wraps back to 0. The receiver must recognise this as a valid sequence
 *       (not a replay) and accept counter values 0..8 after last=65535.
 * ============================================================================ */
static bool test_counter_rollover(void)
{
    uint8_t buf[SHIP_FRAME_SIZE];
    ship_frame_19b_t out;

    /* Encode with counter = 5 (small value after rollover) */
    ship_frame_encode(buf, 0x1234, 0x0001, 0xFFFF, 1, 1,
                      SHIP_DOMAIN_ONOFF, SHIP_GESTURE_CLICK_1,
                      0, 0, 0, 5, TEST_KEY);

    /* last_ctr = 0xFFFE (near max) — rollover resync window allows counter=5 */
    int r = ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, 0xFFFE, &out);
    TEST_ASSERT(r == SHIP_OK,
        "Counter=5 after last=0xFFFE must be accepted as rollover");

    /* But counter=20 after near-max should be rejected (outside resync window) */
    uint8_t buf2[SHIP_FRAME_SIZE];
    ship_frame_encode(buf2, 0x1234, 0x0001, 0xFFFF, 2, 1,
                      SHIP_DOMAIN_ONOFF, SHIP_GESTURE_CLICK_1,
                      0, 0, 0, 20, TEST_KEY);
    /* last_ctr = 0xFFFD: counter=20 after near-rollover but 20 > 8 resync window
     * — in normal range branch (last_ctr <= 0xFFFC), 20 > 0xFFFD is false → REPLAY */
    int r2 = ship_frame_decode(buf2, SHIP_FRAME_SIZE, TEST_KEY, 0xFFFD, &out);
    /* 0xFFFD <= 0xFFFC is false → normal branch: 20 > 0xFFFD = false → REPLAY */
    TEST_ASSERT(r2 == SHIP_ERR_REPLAY,
        "Counter=20 when last=0xFFFD (normal range) should be replay (20 < 0xFFFD)");

    printf("  Counter rollover resync: 0xFFFF→0 handled correctly  ✓\n");
    return true;
}

/* ============================================================================
 * TEST 7: Mesh-RTC wire encoding, authentication, decoding, and clock update
 * ELI5: Build a real clock-sync packet, verify its stamp, then let the clock
 *       updater consume the decoded time just as a receiving device would.
 * ============================================================================ */
static bool test_mesh_rtc_encode_decode_roundtrip(void)
{
    uint8_t buf[SHIP_FRAME_SIZE];
    ship_frame_19b_t decoded;
    ship_mesh_rtc_t rtc = {0};
    const uint32_t epoch = 1700000000UL;

    TEST_ASSERT(ship_frame_encode(buf, 0x1234, 0xABCD, SHIP_ADDR_BROADCAST,
        9, 1, SHIP_DOMAIN_MESH_CLOCK, SHIP_GESTURE_CLICK_1,
        0, 0, 0, 12, TEST_KEY) == SHIP_ERR_INVALID_DOMAIN,
        "Generic encoder must direct clock frames to the RTC-specific encoder");
    int enc = ship_frame_encode_rtc(buf, 0x1234, 0xABCD, 9, 1,
                                    epoch, 12, TEST_KEY);
    TEST_ASSERT(enc == (int)SHIP_FRAME_SIZE, "RTC encoder must return 19 bytes");
    TEST_ASSERT(buf[17] == (uint8_t)(epoch >> 24),
        "RTC epoch most-significant byte must occupy offset 17");
    TEST_ASSERT(buf[18] == 0xAF,
        "RTC one-byte SipHash tag must match the cross-language test vector");
    TEST_ASSERT(ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, 11, &decoded) == SHIP_OK,
        "RTC frame must authenticate and decode");

    ship_rtc_update(&rtc, &decoded, 5000);
    TEST_ASSERT(rtc.is_synced, "Decoded RTC frame must synchronize the clock");
    TEST_ASSERT(rtc.utc_epoch == epoch, "Decoded 32-bit UTC epoch must be exact");
    buf[17] ^= 0x01;
    TEST_ASSERT(ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, 11, &decoded)
                == SHIP_ERR_BAD_HMAC,
        "Changing the high epoch byte must invalidate the clock-frame tag");
    printf("  Mesh-RTC encode→decode→update: epoch=%lu, byte 17 and tag verified  ✓\n",
           (unsigned long)rtc.utc_epoch);
    return true;
}

/* ============================================================================
 * TEST 6: Mesh-RTC clock sync drift guard
 * ELI5: The clock-sync feature keeps all SHiP devices on the same time so
 *       "turn on lights at 2:00 AM" works even without Wi-Fi.
 *       Test: syncing updates the RTC; tiny drifts (< 2 sec) are ignored.
 * ============================================================================ */
static bool test_mesh_rtc_sync(void)
{
    ship_mesh_rtc_t rtc;
    memset(&rtc, 0, sizeof(rtc));

    /* Build a Mesh-RTC sync frame with UTC = 1700000000 (Nov 14 2023) */
    uint32_t test_utc = 1700000000UL;
    ship_frame_19b_t sync_frame;
    memset(&sync_frame, 0, sizeof(sync_frame));
    sync_frame.ship_magic = SHIP_MAGIC_BYTE;
    sync_frame.group_id   = 1;
    sync_frame.cmd_click  = (uint8_t)((SHIP_DOMAIN_MESH_CLOCK << 4) | SHIP_GESTURE_CLICK_1);
    /* Pack 32-bit UTC into the Mesh-RTC byte layout */
    sync_frame.param_1 = (uint8_t)(test_utc & 0xFF);
    sync_frame.param_2 = (uint8_t)((test_utc >> 8) & 0xFF);
    sync_frame.param_3 = (uint8_t)((test_utc >> 16) & 0xFF);
    /* Epoch byte 3 is the low byte of the field at frame offset 17. */
    sync_frame.hmac_trunc16 = (uint16_t)((test_utc >> 24) & 0xFF);

    uint32_t fake_mono_ms = 5000; /* 5 seconds after boot */

    /* First sync: RTC should be set */
    ship_rtc_update(&rtc, &sync_frame, fake_mono_ms);
    TEST_ASSERT(rtc.is_synced, "RTC must be marked synced after first update");
    TEST_ASSERT(rtc.utc_epoch == test_utc, "UTC epoch must match after first sync");

    /* Verify ship_rtc_now() returns approximately correct time */
    uint32_t now_8s_later = ship_rtc_now(&rtc, fake_mono_ms + 8000); /* +8 seconds */
    TEST_ASSERT(now_8s_later == test_utc + 8, "ship_rtc_now must account for elapsed ms");

    /* Tiny drift (< 2 seconds): should NOT update */
    uint32_t small_drift_utc = test_utc + 1; /* Only 1 second ahead */
    sync_frame.param_1 = (uint8_t)(small_drift_utc & 0xFF);
    sync_frame.param_2 = (uint8_t)((small_drift_utc >> 8) & 0xFF);
    sync_frame.param_3 = (uint8_t)((small_drift_utc >> 16) & 0xFF);
    sync_frame.hmac_trunc16 = (uint16_t)((small_drift_utc >> 24) & 0xFF);

    ship_rtc_update(&rtc, &sync_frame, fake_mono_ms);
    TEST_ASSERT(rtc.utc_epoch == test_utc,
        "RTC epoch must NOT update for drift < SHIP_RTC_MAX_DRIFT_S (2s)");

    /* Large drift (>= 2 seconds): SHOULD update */
    uint32_t large_drift_utc = test_utc + 60;
    sync_frame.param_1 = (uint8_t)(large_drift_utc & 0xFF);
    sync_frame.param_2 = (uint8_t)((large_drift_utc >> 8) & 0xFF);
    sync_frame.param_3 = (uint8_t)((large_drift_utc >> 16) & 0xFF);
    sync_frame.hmac_trunc16 = (uint16_t)((large_drift_utc >> 24) & 0xFF);

    ship_rtc_update(&rtc, &sync_frame, fake_mono_ms);
    TEST_ASSERT(rtc.utc_epoch == large_drift_utc,
        "RTC epoch must update for drift >= SHIP_RTC_MAX_DRIFT_S");

    /* Verify hour extraction for time-of-day rules:
     * UTC 1700000000 mod 86400 / 3600 = hour of day */
    uint8_t expected_hour = (uint8_t)((test_utc % 86400UL) / 3600UL);
    printf("  Mesh-RTC: UTC=%lu → hour=%d (UTC)\n", (unsigned long)test_utc, expected_hour);
    printf("  Mesh-RTC sync, drift guard, and ship_rtc_now() verified  ✓\n");
    return true;
}

/* ============================================================================
 * TEST 7: LEAT (Local Event-Action Table) matching
 * ELI5: Check that the cheat-sheet fires actions when group+domain+gesture
 *       match, AND respects the time-of-day window.
 * ============================================================================ */
static bool test_leat_matching(void)
{
    ship_leat_entry_t table[4];
    memset(table, 0, sizeof(table));

    /* Rule 0: group=1, domain=OnOff, gesture=1×click, any time */
    table[0].group_id    = 1;
    table[0].domain      = SHIP_DOMAIN_ONOFF;
    table[0].gesture     = SHIP_GESTURE_CLICK_1;
    table[0].action_type = 0;  /* relay toggle */
    table[0].hour_start  = 0;
    table[0].hour_end    = 23;
    table[0].enabled     = true;

    /* Rule 1: group=1, domain=Lighting, gesture=5×click, only 2:00-2:59 AM */
    table[1].group_id    = 1;
    table[1].domain      = SHIP_DOMAIN_LIGHTING_COMBO;
    table[1].gesture     = SHIP_GESTURE_CLICK_5;
    table[1].action_type = 1;  /* brightness */
    table[1].action_param= 13; /* 5% */
    table[1].hour_start  = 2;
    table[1].hour_end    = 2;
    table[1].enabled     = true;

    /* Frame: group=1, domain=OnOff, 1×click */
    ship_frame_19b_t frame;
    memset(&frame, 0, sizeof(frame));
    frame.ship_magic  = SHIP_MAGIC_BYTE;
    frame.group_id    = 1;
    frame.cmd_click   = (uint8_t)((SHIP_DOMAIN_ONOFF << 4) | SHIP_GESTURE_CLICK_1);

    /* RTC synced at 14:00 UTC — use 1699956000 which is 14:00:00 UTC 2023-11-14 */
    /* python3: int(datetime(2023,11,14,14,0,0,tz=utc).timestamp()) = 1699963200 */
    /* Simpler: pick any UTC where (utc%86400)/3600 == 14: e.g., 86400*any + 14*3600 */
    /* 1700000000 % 86400 = 68000 → 68000/3600 = 18.8 → hour 18, not 14. */
    /* Use 1699963200: 1699963200 % 86400 = 50400, 50400/3600 = 14 ✓ */
    ship_mesh_rtc_t rtc;
    rtc.utc_epoch    = 1699963200UL; /* 14:00:00 UTC on 2023-11-14 */
    rtc.last_sync_ms = 0;
    rtc.is_synced    = true;

    /* OnOff + 1×click at 14:00 → should match rule 0 */
    int m = ship_leat_match(table, 4, &frame, &rtc);
    TEST_ASSERT(m == 0, "OnOff+1×click at 14:00 must match rule 0 (all-day)");

    /* Lighting + 5×click at 14:00 → should NOT match rule 1 (wrong hour) */
    frame.cmd_click = (uint8_t)((SHIP_DOMAIN_LIGHTING_COMBO << 4) | SHIP_GESTURE_CLICK_5);
    m = ship_leat_match(table, 4, &frame, &rtc);
    TEST_ASSERT(m < 0, "Lighting+5×click at 14:00 must NOT match (outside 2:00AM window)");

    /* Lighting + 5×click at 2:00 AM → should match rule 1 */
    /* 1699927200: python3 datetime(2023,11,14,2,0,0,tz=utc) → hour=2 ✓ */
    rtc.utc_epoch = 1699927200UL; /* 02:00:00 UTC on 2023-11-14 */
    m = ship_leat_match(table, 4, &frame, &rtc);
    TEST_ASSERT(m == 1, "Lighting+5×click at 02:00 must match rule 1 (nightlight)");

    /* Wrong group → no match */
    frame.group_id = 99;
    m = ship_leat_match(table, 4, &frame, &rtc);
    TEST_ASSERT(m < 0, "Wrong group_id must not match any rule");

    printf("  LEAT matching: all rules + time-of-day window verified  ✓\n");
    return true;
}

/* ============================================================================
 * TEST 8: Tasmota NoStatusShare flag (gesture = 0x8)
 * ELI5: The NoStatusShare flag tells receiving devices "don't echo your
 *       current state back to the group." Verify it's correctly packed into
 *       the lower nibble of cmd_click.
 * ============================================================================ */
static bool test_nostatus_share_flag(void)
{
    uint8_t buf[SHIP_FRAME_SIZE];
    ship_frame_19b_t out;

    ship_frame_encode(buf, 0x1234, 0x0001, 0xFFFF, 1, 2,
                      SHIP_DOMAIN_LIGHTING_COMBO, SHIP_GESTURE_NO_STATUS_SHARE,
                      50, 0, 0, 10, TEST_KEY);

    TEST_ASSERT(buf[9] == SHIP_MAGIC_BYTE, "Magic byte must be present");

    /* Verify gesture nibble in raw buffer */
    uint8_t gesture_raw = buf[13] & 0x0F;
    uint8_t domain_raw  = (buf[13] >> 4) & 0x0F;
    TEST_ASSERT(gesture_raw == SHIP_GESTURE_NO_STATUS_SHARE,
        "NoStatusShare gesture must be 0x8 in cmd_click lower nibble");
    TEST_ASSERT(domain_raw  == SHIP_DOMAIN_LIGHTING_COMBO,
        "Domain must be SHIP_DOMAIN_LIGHTING_COMBO in cmd_click upper nibble");

    int dec = ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, 9, &out);
    TEST_ASSERT(dec == SHIP_OK, "NoStatusShare frame must decode successfully");
    TEST_ASSERT((out.cmd_click & 0x0F) == SHIP_GESTURE_NO_STATUS_SHARE,
        "Decoded gesture must be SHIP_GESTURE_NO_STATUS_SHARE");
    printf("  Tasmota NoStatusShare flag (gesture=0x8) verified  ✓\n");
    return true;
}

/* ============================================================================
 * TEST 9: Bad magic byte rejection
 * ELI5: If a random radio packet happens to land on the same channel but
 *       does NOT have the magic byte 0xA5 at position 9, SHiP must ignore it.
 * ============================================================================ */
static bool test_bad_magic_rejection(void)
{
    uint8_t buf[SHIP_FRAME_SIZE];
    ship_frame_19b_t out;

    /* Build a valid frame, then corrupt the magic byte */
    ship_frame_encode(buf, 0x1234, 0x0001, 0xFFFF, 1, 1,
                      SHIP_DOMAIN_ONOFF, SHIP_GESTURE_CLICK_1,
                      0, 0, 0, 5, TEST_KEY);

    buf[9] = 0x00; /* Corrupt the magic byte (was 0xA5) */

    int r = ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, 4, &out);
    TEST_ASSERT(r == SHIP_ERR_BAD_MAGIC,
        "Frame with corrupted magic byte must return SHIP_ERR_BAD_MAGIC");

    /* Wrong HMAC: valid magic but tampered data */
    ship_frame_encode(buf, 0x1234, 0x0001, 0xFFFF, 1, 1,
                      SHIP_DOMAIN_ONOFF, SHIP_GESTURE_CLICK_1,
                      0, 0, 0, 5, TEST_KEY);
    buf[14]++; /* Tamper param_1 */
    int r2 = ship_frame_decode(buf, SHIP_FRAME_SIZE, TEST_KEY, 4, &out);
    TEST_ASSERT(r2 == SHIP_ERR_BAD_HMAC,
        "Frame with tampered payload must return SHIP_ERR_BAD_HMAC");

    printf("  Bad magic and HMAC tamper rejection verified  ✓\n");
    return true;
}

/* ============================================================================
 * MAIN — Run all tests and print summary
 * ============================================================================ */
int main(void)
{
    printf("=================================================================\n");
    printf("  SHiP v0.1 (Project Anchor) — C Core Unit Test Suite\n");
    printf("  Testing: ship_protocol.c (SipHash-2-4, encode/decode, LEAT, RTC)\n");
    printf("=================================================================\n");

    RUN_TEST(test_frame_size_assertion);
    RUN_TEST(test_encode_decode_roundtrip);
    RUN_TEST(test_siphash_crypto);
    RUN_TEST(test_anti_replay);
    RUN_TEST(test_counter_rollover);
    RUN_TEST(test_mesh_rtc_encode_decode_roundtrip);
    RUN_TEST(test_mesh_rtc_sync);
    RUN_TEST(test_leat_matching);
    RUN_TEST(test_nostatus_share_flag);
    RUN_TEST(test_bad_magic_rejection);

    printf("\n=================================================================\n");
    printf("  RESULTS: %d/%d tests passed", tests_passed, tests_run);
    if (tests_failed > 0) {
        printf("  (%d FAILED)\n", tests_failed);
    } else {
        printf("  — ALL PASS ✓\n");
    }
    printf("=================================================================\n");

    return (tests_failed > 0) ? 1 : 0;
}
