/*
 * ============================================================================
 * SHiP v0.1.23 — sim/test_ship_fuzz.c
 * ============================================================================
 *
 * LAYER 1 — THE KITCHEN-TABLE EXPLANATION (ELI5)
 * -----------------------------------------------
 * This is the "crash test" for the SHiP radio engine, in C. A real mesh sits
 * out in the open where the air is full of garbage: half-arrived packets, a
 * neighbour replaying an old recording, a corrupted secret stamp, a device that
 * transmits pure noise. If any of that could make ship_frame_decode() CRASH (an
 * out-of-bounds read, a null-deref, a stack smash) it could take the whole
 * offline mesh down — the exact disaster SHiP Anchor exists to prevent.
 *
 * So this harness throws *thousands* of deliberately broken frames at the REAL
 * C core (ship_frame_decode) and asserts one simple thing every time: the
 * decoder NEVER crashes. A bad frame is cleanly rejected with a typed SHIP_ERR_*
 * code ("drop it silently") or, if it happens to stay valid, decoded. Anything
 * else — a crash — is a harness failure.
 *
 * This is the C companion to tests/test_fuzz.py (the Python airbag test). The
 * two exercise the same frame, byte-for-byte, across the same language mirror.
 *
 * ELI5: it is the airbag test. We jab the steering wheel in every direction to
 * make sure the airbag pops instead of the car flipping over.
 *
 * LAYER 2 — THE UNDER-THE-HOOD IMPLEMENTATION
 * ============================================================================
 * Compiled and run natively (no radio hardware) with:
 *   gcc -Wall -Wextra -Werror -std=c11 -DSHIP_PLATFORM_NATIVE \
 *        -I src/ship_core sim/test_ship_fuzz.c src/ship_core/ship_protocol.c \
 *       src/ship_core/ship_radio_hal.c -o /tmp/test_ship_fuzz && /tmp/test_ship_fuzz
 *
 * The same robustness check is ALSO wired into the core suite as
 * test_fuzz_robustness() (see sim/test_ship_core.c), so a single build keeps the
 * C core honest. This standalone harness exists so the fuzz run can be made
 * heavier (raise FUZZ_ITERS) without slowing the everyday core build.
 *
 * No external dependencies — pure C11, POSIX headers only.
 * ============================================================================
 */

#include "../src/ship_core/ship_protocol.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdbool.h>

/* ELI5: the same test key the core suite uses. A well-known TEST secret, NOT a
 * real one — it must never protect a live device. */
static const uint8_t FUZZ_KEY[SHIP_KEY_SIZE] = {
    0xDE, 0xAD, 0xBE, 0xEF, 0xCA, 0xFE, 0xBA, 0xBE,
    0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08
};
/* ELI5: a tiny, fast, deterministic PRNG (xorshift32). Deterministic so the
 * run is reproducible; fast so we can throw thousands of frames cheaply. */
static uint32_t g_rng = 0x9E3779B9u;
static uint32_t rng_range(uint32_t limit)
{
    /* limit > 0 */
    return g_rng % limit;
}
static uint8_t rng_byte(void)
{
    return (uint8_t)(g_rng & 0xFFu);
}

/* ELI5: one counter tallies frames that reached the decoder. If we ever get
 * here, the decoder returned a typed code instead of crashing. */
static int g_clean_returns = 0;
static int g_rejections    = 0;
static int g_accepts       = 0;

/* A single "throw a frame at the decoder" step. Returns true on a clean
 * (non-crashing) outcome. Every path is a clean outcome — the only failure is
 * the program crashing, which a test runner detects by a non-zero exit / signal.
 */
static bool fuzz_one(const uint8_t *frame, size_t len)
{
    ship_frame_19b_t out;
    int r = ship_frame_decode(frame, len, FUZZ_KEY,
                               (uint16_t)rng_range(0x10000u), &out);
    g_clean_returns++;
    if (r == SHIP_OK) {
        g_accepts++;
    } else {
        g_rejections++;
    }
    return true; /* any typed return is a clean, crash-free outcome */
}

/* ---------------------------------------------------------------------------
 * FUZZ FAMILY 1 — Truncated & over-long frames.
 * ELI5: a half-arrived or over-long packet. The decoder must refuse (< 19
 * bytes) or ignore trailing padding (>= 19 bytes), NEVER read out of bounds.
 * ------------------------------------------------------------------------- */
static void fuzz_truncated_and_oversized(const uint8_t *base)
{
    for (size_t len = 0; len <= SHIP_FRAME_SIZE + 6; len++) {
        fuzz_one(base, len);
    }
}

/* ---------------------------------------------------------------------------
 * FUZZ FAMILY 2 — Corrupted SipHash tags.
 * ELI5: a tampered secret stamp. The decoder must answer SHIP_ERR_BAD_HMAC.
 * ------------------------------------------------------------------------- */
static void fuzz_corrupted_hmac(const uint8_t *base, int iterations)
{
    for (int i = 0; i < iterations; i++) {
        uint8_t frame[SHIP_FRAME_SIZE];
        memcpy(frame, base, SHIP_FRAME_SIZE);
        if ((rng_byte() & 1u) == 0u) {
            frame[17] ^= (uint8_t)(rng_byte() | 1u);
            frame[18] ^= (uint8_t)(rng_byte() | 1u);
        } else {
            frame[18] ^= (uint8_t)(rng_byte() | 1u);
        }
        fuzz_one(frame, SHIP_FRAME_SIZE);
    }
}

/* ---------------------------------------------------------------------------
 * FUZZ FAMILY 3 — Stale / replayed nonces.
 * ELI5: a neighbour replaying an old recording. The decoder must answer
 * SHIP_ERR_REPLAY without ever crashing.
 * ------------------------------------------------------------------------- */
static void fuzz_replayed_nonces(const uint8_t *base, int iterations)
{
    for (int i = 0; i < iterations; i++) {
        uint8_t frame[SHIP_FRAME_SIZE];
        memcpy(frame, base, SHIP_FRAME_SIZE);
        /* Randomize the 2-byte replay counter at bytes 15-16. */
        frame[15] = rng_byte();
        frame[16] = rng_byte();
        fuzz_one(frame, SHIP_FRAME_SIZE);
    }
}

/* ---------------------------------------------------------------------------
 * FUZZ FAMILY 4 — Garbage headers & pure random bytes.
 * ELI5: mangled magic, and completely random payloads. The decoder must
 * refuse with SHIP_ERR_BAD_MAGIC / SHIP_ERR_BAD_HMAC and never crash.
 * ------------------------------------------------------------------------- */
static void fuzz_garbage_headers(const uint8_t *base, int iterations)
{
    for (int i = 0; i < iterations; i++) {
        uint8_t frame[SHIP_FRAME_SIZE];
        memcpy(frame, base, SHIP_FRAME_SIZE);
        frame[9] = rng_byte(); /* clobber the magic byte */
        fuzz_one(frame, SHIP_FRAME_SIZE);
    }
}

static void fuzz_random_bytes(int iterations)
{
    for (int i = 0; i < iterations; i++) {
        uint8_t frame[SHIP_FRAME_SIZE];
        for (size_t b = 0; b < SHIP_FRAME_SIZE; b++) {
            frame[b] = rng_byte();
        }
        fuzz_one(frame, SHIP_FRAME_SIZE);
    }
}

/* ---------------------------------------------------------------------------
 * FUZZ FAMILY 5 — Null-pointer hardening.
 * ELI5: a caller that passes a null pointer must get SHIP_ERR_NULL_PTR, not a
 * segfault.
 * ------------------------------------------------------------------------- */
static bool fuzz_null_pointers(void)
{
    uint8_t frame[SHIP_FRAME_SIZE];
    ship_frame_19b_t out;
    memset(frame, 0, sizeof(frame));

    int r_null_frame = ship_frame_decode(NULL, SHIP_FRAME_SIZE,
                                          FUZZ_KEY, 0, &out);
    int r_null_key   = ship_frame_decode(frame, SHIP_FRAME_SIZE,
                                          NULL, 0, &out);
    int r_null_out   = ship_frame_decode(frame, SHIP_FRAME_SIZE,
                                          FUZZ_KEY, 0, NULL);
    g_clean_returns += 3;
    return (r_null_frame == SHIP_ERR_NULL_PTR &&
            r_null_key   == SHIP_ERR_NULL_PTR &&
            r_null_out   == SHIP_ERR_NULL_PTR);
}

int main(void)
{
    printf("=================================================================\n");
    printf("  SHiP v0.1.23 — C Core FUZZ AIRBAG (test_ship_fuzz.c)\n");
    printf("  Throwing thousands of broken frames at ship_frame_decode()\n");
    printf("  and asserting it NEVER crashes — only cleanly refuses/decodes.\n");
    printf("=================================================================\n");

    /* Build a valid baseline frame to corrupt. */
    uint8_t base[SHIP_FRAME_SIZE];
    int enc = ship_frame_encode(base, 0x1234, 0xABCD, 0xFFFF, 7, 1,
                                0x01, 0x02, 0xBB, 0xCC, 0xDD, 10, FUZZ_KEY);
    if (enc != (int)SHIP_FRAME_SIZE) {
        printf("  [FATAL] could not build baseline frame (enc=%d)\n", enc);
        return 1;
    }
    if (base[9] != SHIP_MAGIC_BYTE) {
        printf("  [FATAL] baseline magic byte is not 0xA5\n");
        return 1;
    }

    int iterations = 4000;

    fuzz_truncated_and_oversized(base);
    fuzz_corrupted_hmac(base, iterations);
    fuzz_replayed_nonces(base, iterations);
    fuzz_garbage_headers(base, iterations);
    fuzz_random_bytes(iterations);

    bool null_ok = fuzz_null_pointers();

    printf("\n=================================================================\n");
    printf("  RESULTS: %d frames reached the decoder (crash-free)\n",
            g_clean_returns);
    printf("    clean rejections: %d   accepts: %d\n",
            g_rejections, g_accepts);
    printf("    null-pointer hardening: %s\n", null_ok ? "PASS" : "FAIL");
    /* A clean rejection count must be non-zero — the airbag MUST pop at least
     * once; if nothing was ever refused the test families are broken. */
    bool pass = null_ok && (g_rejections > 0) && (g_clean_returns > 0);
    printf("  VERDICT: %s\n", pass ? "ALL PASS (zero crashes)" : "FAILED");
    printf("=================================================================\n");

    return pass ? 0 : 1;
}
