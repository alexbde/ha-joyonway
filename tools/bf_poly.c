/*
 * Brute-force CRC-32 polynomial search — comprehensive version.
 *
 * Searches ALL 2^32 polynomials with ALL plausible shift distances
 * between byte[14] bit1 and byte[10] bit2 (distance 1 to 64).
 *
 * Also tries REVERSE direction (byte[10] → byte[14] instead of byte[14] → byte[10]).
 *
 * For constraint 2 (byte[10] ↔ byte[9], distance 8), tries both directions.
 *
 * Build: cc -O3 -o bf_poly bf_poly.c
 * Run:   ./bf_poly
 */
#include <stdio.h>
#include <stdint.h>
#include <time.h>

static inline uint32_t shift1(uint32_t val, uint32_t poly) {
    if (val & 0x80000000u)
        return (val << 1) ^ poly;
    else
        return val << 1;
}

int main(void) {
    const uint32_t C14b1 = 0x03B1590Eu;
    const uint32_t C10b2 = 0x399CBDF3u;
    const uint32_t C10b3 = 0x73397BE6u;
    const uint32_t C10xor = C10b2 ^ C10b3;  /* 0x4AA5C615 */
    const uint32_t C9xor  = 0xB5E05E20u;

    const int MAX_DIST = 64;
    const uint64_t TOTAL = 0x100000000ULL;
    const uint64_t REPORT_INTERVAL = TOTAL / 1000;  /* report every 0.1% */

    printf("Searching 2^32 polynomials, distances 1-%d, both directions...\n",
           MAX_DIST);
    printf("Will report progress every 0.1%%\n\n");
    fflush(stdout);

    time_t start_time = time(NULL);
    uint64_t hits = 0;

    for (uint64_t p64 = 0; p64 < TOTAL; p64++) {
        uint32_t poly = (uint32_t)p64;

        /* Progress reporting */
        if ((p64 % REPORT_INTERVAL) == 0 && p64 > 0) {
            double pct = (double)p64 / (double)TOTAL * 100.0;
            time_t now = time(NULL);
            double elapsed = difftime(now, start_time);
            double rate = (double)p64 / elapsed;
            double remaining = (double)(TOTAL - p64) / rate;
            int eta_min = (int)(remaining / 60.0);
            int eta_sec = (int)remaining % 60;
            printf("\r  [%5.1f%%] %llu / %llu | %d hits | elapsed %.0fs | ETA %dm%02ds   ",
                   pct, (unsigned long long)p64, (unsigned long long)TOTAL,
                   (int)hits, elapsed, eta_min, eta_sec);
            fflush(stdout);
        }

        /* === FORWARD: shift^D(C14b1, P) should give C10b2 at some D === */
        uint32_t fwd = C14b1;
        for (int d = 1; d <= MAX_DIST; d++) {
            fwd = shift1(fwd, poly);
            if (fwd == C10b2) {
                /* Check constraint 2 in BOTH directions */
                uint32_t c2f = C10xor;
                for (int i = 0; i < 8; i++) c2f = shift1(c2f, poly);
                if (c2f == C9xor) {
                    hits++;
                    printf("\n  ★ FWD D=%d, C2=fwd(10->9): P = 0x%08X\n", d, poly);
                    fflush(stdout);
                }
                uint32_t c2r = C9xor;
                for (int i = 0; i < 8; i++) c2r = shift1(c2r, poly);
                if (c2r == C10xor) {
                    hits++;
                    printf("\n  ★ FWD D=%d, C2=rev(9->10): P = 0x%08X\n", d, poly);
                    fflush(stdout);
                }
            }
        }

        /* === REVERSE: shift^D(C10b2, P) should give C14b1 at some D === */
        uint32_t rev = C10b2;
        for (int d = 1; d <= MAX_DIST; d++) {
            rev = shift1(rev, poly);
            if (rev == C14b1) {
                /* Check constraint 2 in BOTH directions */
                uint32_t c2f = C10xor;
                for (int i = 0; i < 8; i++) c2f = shift1(c2f, poly);
                if (c2f == C9xor) {
                    hits++;
                    printf("\n  ★ REV D=%d, C2=fwd(10->9): P = 0x%08X\n", d, poly);
                    fflush(stdout);
                }
                uint32_t c2r = C9xor;
                for (int i = 0; i < 8; i++) c2r = shift1(c2r, poly);
                if (c2r == C10xor) {
                    hits++;
                    printf("\n  ★ REV D=%d, C2=rev(9->10): P = 0x%08X\n", d, poly);
                    fflush(stdout);
                }
            }
        }
    }

    time_t end_time = time(NULL);
    double total_elapsed = difftime(end_time, start_time);

    printf("\n\n");
    printf("Search complete. Total time: %.0f seconds (%.1f minutes)\n",
           total_elapsed, total_elapsed / 60.0);
    printf("Hits: %llu\n", (unsigned long long)hits);

    if (hits == 0) {
        printf("\nNo polynomial found satisfying both constraints.\n");
        printf("The checksum is likely NOT a standard CRC-32 with MSB-first LFSR.\n");
    }

    return 0;
}

