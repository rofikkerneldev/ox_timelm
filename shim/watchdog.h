#pragma once
#include <stdint.h>

/* SM8250 APSS/KPSS layout, verified against qcom-wdt.c and watchdog_v2.c. */
#define TIMELM_WDT_BASE 0x17c10000UL
#define TIMELM_WDT_EN   (0x08 / 4)
#define TIMELM_WDT_STS  (0x0c / 4)
#define TIMELM_WDT_BARK (0x10 / 4)
#define TIMELM_WDT_BITE (0x14 / 4)

struct timelm_wdt_state {
	uint32_t before, after, status, bark, bite;
};

/* Exact token matching, bounded by the payload's 608-byte command-line field. */
static inline int
timelm_wdt_requested(const char *command, unsigned limit)
{
	static const char option[] = "timelm_wdt=stop";
	unsigned i = 0;
	while (i < limit && command[i]) {
		while (i < limit && (command[i] == ' ' || command[i] == '\t'))
			i++;
		unsigned start = i;
		while (i < limit && command[i] && command[i] != ' ' && command[i] != '\t')
			i++;
		if (i - start == sizeof(option) - 1) {
			unsigned j;
			for (j = 0; j < sizeof(option) - 1 && command[start + j] == option[j]; j++)
				;
			if (j == sizeof(option) - 1)
				return 1;
		}
	}
	return 0;
}

/* No reset, timeout, PMIC or secure-world register is modified. */
static inline int
timelm_wdt_stop(volatile uint32_t *regs, struct timelm_wdt_state *state)
{
	state->before = regs[TIMELM_WDT_EN];
	state->after = state->before;
	if (state->before == UINT32_MAX)
		return -1;
	state->status = regs[TIMELM_WDT_STS];
	state->bark = regs[TIMELM_WDT_BARK];
	state->bite = regs[TIMELM_WDT_BITE];
	if (state->before & 1U) {
		regs[TIMELM_WDT_EN] = 0;
#if defined(__aarch64__)
		__asm__ volatile("dsb sy" ::: "memory");
#else
		__sync_synchronize();
#endif
		state->after = regs[TIMELM_WDT_EN];
	}
	return (state->after & 1U) ? -2 : 0;
}
