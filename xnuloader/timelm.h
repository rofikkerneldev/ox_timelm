#ifndef XNULOADER_TIMELM_H
#define XNULOADER_TIMELM_H
#define TIMELM_RAM_BASE 0x100000000ULL
#define TIMELM_RAM_END  0x180000000ULL
/* Intersect only conventional-memory descriptors with the validated4..6GiB bank. */
static int
timelm_ram_window(uint64_t lo, uint64_t hi, uint64_t *base, uint64_t *size)
{
    if (hi <= lo || lo >= TIMELM_RAM_END || hi <= TIMELM_RAM_BASE) return 0;
    if (lo < TIMELM_RAM_BASE) lo = TIMELM_RAM_BASE;
    if (hi > TIMELM_RAM_END) hi = TIMELM_RAM_END;
    lo = (lo + 0x1ffffffULL) & ~0x1ffffffULL;
    hi &= ~0x1ffffffULL;
    if (hi <= lo) return 0;
    *base = lo; *size = hi - lo;
    return 1;
}
#endif
