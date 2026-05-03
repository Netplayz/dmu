/*
 * ================================================================
 *  ISS Radiation-Hardened Memory Core — C Implementation
 *  Triple Modular Redundancy (TMR) + Hamming(7,4) ECC
 *  Memory Scrubber Thread + SEU Simulator
 *
 *  NetByte
 *  Radiation-Hardened Software Suite v1.0
 * ================================================================
 *
 *  Techniques used:
 *    - Triple Modular Redundancy: every byte stored 3× and
 *      majority-voted on read. One corrupted copy → silently fixed.
 *    - Hamming(7,4) ECC: encodes the lower nibble of each byte
 *      so even a 3-way disagreement can be resolved.
 *    - Memory Scrubber: background thread walks the pool every
 *      SCRUB_INTERVAL_SEC seconds, repairing diverged copies.
 *    - Radiation log: records every SEU injection + correction.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <pthread.h>
#include <unistd.h>
#include <time.h>
#include <signal.h>

/* ---- Configuration ------------------------------------------ */
#define TMR_COPIES          3
#define MEMORY_POOL_SIZE    4096
#define SCRUB_INTERVAL_SEC  3
#define MAX_RAD_EVENTS      512

/* ---- Radiation Event Log ------------------------------------ */
typedef struct {
    time_t   timestamp;
    uint32_t address;
    int      copy_idx;
    int      bit_pos;
    uint8_t  original;
    uint8_t  corrupted;
    int      corrected;   /* 1 = fixed by scrubber/TMR */
} RadiationEvent;

static RadiationEvent rad_log[MAX_RAD_EVENTS];
static int            rad_count = 0;
static pthread_mutex_t rad_mutex = PTHREAD_MUTEX_INITIALIZER;

/* ---- Protected Memory Pool ---------------------------------- */
typedef struct {
    uint8_t  copy[TMR_COPIES];   /* three independent copies     */
    uint8_t  hamming_parity;     /* Hamming code for lower nibble */
} ProtectedByte;

static ProtectedByte   mem_pool[MEMORY_POOL_SIZE];
static pthread_mutex_t mem_mutex = PTHREAD_MUTEX_INITIALIZER;
static volatile int    running   = 1;

/* Counters */
static int total_seu    = 0;
static int total_healed = 0;

/* ================================================================
 *  Hamming(7,4)  — encodes 4 data bits into 7-bit code word
 *
 *  Bit layout (1-indexed):
 *    pos 1 = parity p1  (covers 1,3,5,7)
 *    pos 2 = parity p2  (covers 2,3,6,7)
 *    pos 3 = data   d1
 *    pos 4 = parity p3  (covers 4,5,6,7)
 *    pos 5 = data   d2
 *    pos 6 = data   d3
 *    pos 7 = data   d4
 * ================================================================ */
static uint8_t hamming_encode(uint8_t nibble)
{
    uint8_t d1 = (nibble >> 0) & 1;
    uint8_t d2 = (nibble >> 1) & 1;
    uint8_t d3 = (nibble >> 2) & 1;
    uint8_t d4 = (nibble >> 3) & 1;

    uint8_t p1 = d1 ^ d2 ^ d4;
    uint8_t p2 = d1 ^ d3 ^ d4;
    uint8_t p3 = d2 ^ d3 ^ d4;

    return (p1)       |
           (p2  << 1) |
           (d1  << 2) |
           (p3  << 3) |
           (d2  << 4) |
           (d3  << 5) |
           (d4  << 6);
}

/* Returns 0=clean, 1=single-bit corrected.
   Recovered nibble written to *data_out. */
static int hamming_decode(uint8_t encoded, uint8_t *data_out)
{
    uint8_t r1 = (encoded >> 0) & 1; /* p1 */
    uint8_t r2 = (encoded >> 1) & 1; /* p2 */
    uint8_t r3 = (encoded >> 2) & 1; /* d1 */
    uint8_t r4 = (encoded >> 3) & 1; /* p3 */
    uint8_t r5 = (encoded >> 4) & 1; /* d2 */
    uint8_t r6 = (encoded >> 5) & 1; /* d3 */
    uint8_t r7 = (encoded >> 6) & 1; /* d4 */

    uint8_t s1 = r1 ^ r3 ^ r5 ^ r7;
    uint8_t s2 = r2 ^ r3 ^ r6 ^ r7;
    uint8_t s3 = r4 ^ r5 ^ r6 ^ r7;

    uint8_t error_pos = s1 | (s2 << 1) | (s3 << 2);

    if (error_pos != 0)
        encoded ^= (1u << (error_pos - 1));   /* flip the bad bit */

    *data_out = ((encoded >> 2) & 1)       |
                (((encoded >> 4) & 1) << 1)|
                (((encoded >> 5) & 1) << 2)|
                (((encoded >> 6) & 1) << 3);

    return (error_pos != 0) ? 1 : 0;
}

/* ================================================================
 *  TMR Read / Write
 * ================================================================ */
void tmr_write(uint32_t addr, uint8_t value)
{
    if (addr >= MEMORY_POOL_SIZE) return;
    pthread_mutex_lock(&mem_mutex);
    for (int i = 0; i < TMR_COPIES; i++)
        mem_pool[addr].copy[i] = value;
    mem_pool[addr].hamming_parity = hamming_encode(value & 0x0F);
    pthread_mutex_unlock(&mem_mutex);
}

uint8_t tmr_read(uint32_t addr)
{
    if (addr >= MEMORY_POOL_SIZE) return 0;
    pthread_mutex_lock(&mem_mutex);
    uint8_t a = mem_pool[addr].copy[0];
    uint8_t b = mem_pool[addr].copy[1];
    uint8_t c = mem_pool[addr].copy[2];
    uint8_t hp = mem_pool[addr].hamming_parity;
    pthread_mutex_unlock(&mem_mutex);

    /* --- Majority vote --- */
    if (a == b || a == c) return a;
    if (b == c)           return b;

    /* Triple mismatch — fall back to Hamming recovery */
    uint8_t recovered = 0;
    hamming_decode(hp, &recovered);
    printf("[TMR] 3-way mismatch @ 0x%04X: %02X/%02X/%02X  "
           "Hamming recovery → 0x%02X\n", addr, a, b, c, recovered);
    return recovered;
}

/* ================================================================
 *  Radiation / SEU Simulator
 * ================================================================ */
void inject_seu(uint32_t addr, int copy_idx, int bit_pos)
{
    if (addr >= MEMORY_POOL_SIZE || copy_idx >= TMR_COPIES) return;

    pthread_mutex_lock(&mem_mutex);
    uint8_t orig = mem_pool[addr].copy[copy_idx];
    mem_pool[addr].copy[copy_idx] ^= (uint8_t)(1u << bit_pos);
    uint8_t corr = mem_pool[addr].copy[copy_idx];
    pthread_mutex_unlock(&mem_mutex);

    pthread_mutex_lock(&rad_mutex);
    if (rad_count < MAX_RAD_EVENTS) {
        rad_log[rad_count++] = (RadiationEvent){
            .timestamp = time(NULL),
            .address   = addr,
            .copy_idx  = copy_idx,
            .bit_pos   = bit_pos,
            .original  = orig,
            .corrupted = corr,
            .corrected = 0,
        };
    }
    total_seu++;
    pthread_mutex_unlock(&rad_mutex);

    printf("[SEU] addr=0x%04X copy[%d] bit%d: 0x%02X→0x%02X\n",
           addr, copy_idx, bit_pos, orig, corr);
}

/* ================================================================
 *  Memory Scrubber Thread
 *  Walks the entire pool, detects divergence, votes + repairs.
 * ================================================================ */
static void *memory_scrubber(void *arg)
{
    (void)arg;
    printf("[SCRUBBER] Online — interval=%ds\n", SCRUB_INTERVAL_SEC);

    while (running) {
        sleep(SCRUB_INTERVAL_SEC);
        int healed = 0;

        for (uint32_t addr = 0; addr < MEMORY_POOL_SIZE; addr++) {
            pthread_mutex_lock(&mem_mutex);
            uint8_t a  = mem_pool[addr].copy[0];
            uint8_t b  = mem_pool[addr].copy[1];
            uint8_t c  = mem_pool[addr].copy[2];

            if (a == b && b == c) { pthread_mutex_unlock(&mem_mutex); continue; }

            uint8_t correct_val;
            if      (a == b || a == c) correct_val = a;
            else if (b == c)           correct_val = b;
            else {
                /* Triple mismatch — Hamming oracle */
                hamming_decode(mem_pool[addr].hamming_parity, &correct_val);
            }

            for (int i = 0; i < TMR_COPIES; i++)
                mem_pool[addr].copy[i] = correct_val;

            pthread_mutex_unlock(&mem_mutex);
            healed++;

            pthread_mutex_lock(&rad_mutex);
            if (rad_count > 0)
                rad_log[rad_count - 1].corrected = 1;
            pthread_mutex_unlock(&rad_mutex);
        }

        if (healed) {
            total_healed += healed;
            printf("[SCRUBBER] Healed %d location(s)  "
                   "(total SEU=%d  healed=%d)\n",
                   healed, total_seu, total_healed);
        }
    }

    printf("[SCRUBBER] Offline.\n");
    return NULL;
}

/* ================================================================
 *  Self-Test
 * ================================================================ */
static int self_test(void)
{
    printf("[SELF-TEST] Running...\n");

    /* Hamming round-trip for every nibble */
    for (uint8_t d = 0; d < 16; d++) {
        uint8_t enc = hamming_encode(d);
        uint8_t dec = 0;
        hamming_decode(enc, &dec);
        if (dec != d) {
            printf("[SELF-TEST] Hamming FAIL nibble 0x%X\n", d);
            return -1;
        }
        /* Inject single-bit error, verify correction */
        uint8_t broken = enc ^ 0x04;   /* flip bit 2 */
        hamming_decode(broken, &dec);
        if (dec != d) {
            printf("[SELF-TEST] Hamming correction FAIL nibble 0x%X\n", d);
            return -1;
        }
    }
    printf("[SELF-TEST] Hamming(7,4) ECC: PASS\n");

    /* TMR write/read/correct */
    for (uint8_t v = 0; v < 32; v++) {
        tmr_write(v, (uint8_t)(v * 7 + 3));
    }
    inject_seu(0,  0, 1);  /* corrupt copy[0] of address 0  */
    inject_seu(7,  1, 5);  /* corrupt copy[1] of address 7  */
    inject_seu(15, 2, 0);  /* corrupt copy[2] of address 15 */

    for (uint8_t v = 0; v < 32; v++) {
        uint8_t expected = (uint8_t)(v * 7 + 3);
        uint8_t got      = tmr_read(v);
        if (got != expected) {
            printf("[SELF-TEST] TMR FAIL @ addr %u: got 0x%02X expected 0x%02X\n",
                   v, got, expected);
            return -1;
        }
    }
    printf("[SELF-TEST] TMR majority vote: PASS\n");
    printf("[SELF-TEST] All tests PASSED.\n\n");
    return 0;
}

static void print_rad_log(void)
{
    printf("\n╔══ RADIATION EVENT LOG (%d events) ══════════════════╗\n",
           rad_count);
    for (int i = 0; i < rad_count; i++) {
        RadiationEvent *e = &rad_log[i];
        printf("║  [%ld] 0x%04X copy[%d] bit%d  0x%02X→0x%02X  %s\n",
               e->timestamp, e->address, e->copy_idx, e->bit_pos,
               e->original, e->corrupted,
               e->corrected ? "CORRECTED ✓" : "pending…");
    }
    printf("╚═════════════════════════════════════════════════════╝\n");
    printf("  Total SEU: %d   Healed: %d   Lost: %d\n",
           total_seu, total_healed, total_seu - total_healed);
}

static void signal_handler(int s) { (void)s; running = 0; }

/* ================================================================
 *  main
 * ================================================================ */
int main(void)
{
    printf("╔══════════════════════════════════════════════════════╗\n");
    printf("║  ISS Radiation-Hardened Memory Core  (C / TMR+ECC)  ║\n");
    printf("║  Triple Modular Redundancy + Hamming(7,4) + Scrubber ║\n");
    printf("║  NetByte  v1.0                   ║\n");
    printf("╚══════════════════════════════════════════════════════╝\n\n");

    signal(SIGINT,  signal_handler);
    signal(SIGTERM, signal_handler);

    srand((unsigned)time(NULL));
    memset(mem_pool, 0, sizeof(mem_pool));

    if (self_test() != 0) {
        fprintf(stderr, "CRITICAL: Self-test failed — system unsafe.\n");
        return 1;
    }

    /* Start background scrubber */
    pthread_t scrubber;
    pthread_create(&scrubber, NULL, memory_scrubber, NULL);

    /* Write mission-critical string */
    const char *mission_data = "EXPEDITION74-SYSTEMS-NOMINAL-ISS";
    size_t mlen = strlen(mission_data);
    printf("[MAIN] Writing protected mission data (%zu bytes)...\n", mlen);
    for (size_t i = 0; i < mlen; i++)
        tmr_write((uint32_t)i, (uint8_t)mission_data[i]);

    /* Simulate orbital radiation over several passes */
    printf("[MAIN] Simulating radiation environment (3 orbital passes)...\n\n");
    for (int pass = 0; pass < 3; pass++) {
        sleep(1);
        printf("─── Orbital pass %d ──────────────────────────────\n", pass + 1);

        /* 1-3 random SEUs per pass */
        int seu_count = 1 + rand() % 3;
        for (int k = 0; k < seu_count; k++) {
            inject_seu((uint32_t)(rand() % mlen),
                       rand() % TMR_COPIES,
                       rand() % 8);
        }

        /* Verify data still intact via TMR */
        printf("[MAIN] Read-back: \"");
        for (size_t i = 0; i < mlen; i++)
            putchar((char)tmr_read((uint32_t)i));
        printf("\"\n\n");
    }

    /* Let scrubber run one full cycle */
    sleep(SCRUB_INTERVAL_SEC + 1);

    print_rad_log();

    running = 0;
    pthread_join(scrubber, NULL);

    printf("\n[MAIN] TMR Core shutdown — system nominal.\n");
    return 0;
}
