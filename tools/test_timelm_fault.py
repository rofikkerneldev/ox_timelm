#!/usr/bin/env python3
"""Run actual 0090 raw-event code with invalid format pointers and guarded buffers."""
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('irqtests', ROOT / 'tools/test_timelm_irq.py')
irqtests = importlib.util.module_from_spec(spec)
spec.loader.exec_module(irqtests)


class RawFaultTests(unittest.TestCase):
    def test_actual_raw_marker_and_progress(self):
        patches = [(ROOT / 'xnu/patches' / p).read_text() for p in (
            '0088-timelm-independent-liveness-diagnostics.patch',
            '0089-timelm-independent-timer-irq-strip.patch',
            '0090-timelm-raw-panic-and-irq-tail-progress.patch')]
        if os.environ.get('XNU_SOURCE'):
            source = Path(os.environ['XNU_SOURCE'])
            h = (source / 'pexpert/pexpert/arm64/SWEET.h').read_text()
            h = h[h.index('#ifndef ASSEMBLER\n/* Opt-in 0088'):h.index('#endif /* !ASSEMBLER */')]
            fb = (source / 'iokit/Kernel/arm/SweetFramebuffer.cpp').read_text()
        else:
            h = irqtests.additions(patches[0], 'pexpert/pexpert/arm64/SWEET.h')
            h = h[h.index('#ifndef ASSEMBLER'):h.index('#endif /* !ASSEMBLER */')]
            for p in patches[1:]:
                h += '\nextern "C" {\n' + irqtests.additions(p, 'pexpert/pexpert/arm64/SWEET.h') + '\n}\n'
            section = patches[2].split('+++ b/iokit/Kernel/arm/SweetFramebuffer.cpp\n', 1)[1].split('\n--- ', 1)[0]
            new_context = '\n'.join(line[1:] for line in section.splitlines()
                                    if line.startswith(('+', ' ')))
            fb = irqtests.additions(patches[1], 'iokit/Kernel/arm/SweetFramebuffer.cpp') + '\n' + new_context
        start = fb.index('static char *\n timelm_irq_number(')
        helper = fb[start:fb.index('static void\n timelm_irq_draw(', start)]
        code = fb[fb.index('/* 0090:'):]
        if 'class IOMobileFramebufferUserClient;' in code:
            code = code[:code.index('class IOMobileFramebufferUserClient;')]
        for forbidden in ('printf(', 'snprintf(', 'IOLock', 'IOMalloc', 'IOLog(', 'ufs_', 'va_list', 'current_thread'):
            self.assertNotIn(forbidden, code)
        self.assertEqual(code.count('__asm__ volatile("dsb sy" ::: "memory");'), 2)
        code = code.replace('__asm__ volatile("dsb sy" ::: "memory");', '__atomic_thread_fence(__ATOMIC_SEQ_CST);')
        harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <vector>
''' + h + r'''
#endif
unsigned int timelm_diag_enabled, timelm_irq_enabled, timelm_status_draw, timelm90_claimed;
unsigned long long timelm_diag_values[TIMELM_D_COUNT], timelm_irq_values[TIMELM_I_COUNT];
unsigned long long timelm90_values[TIMELM90_COUNT], timelm_irq_period;
char timelm_diag_trial[64];
unsigned char iso_font[256 * 16];
static uintptr_t timelm_diag_pixels;
static uint32_t timelm_diag_width, timelm_diag_height, timelm_diag_stride;
static unsigned int timelm_irq_strip;
''' + helper + code + r'''
static_assert(sizeof(timelm90_disk) == 128, "disk ABI");
static_assert(offsetof(timelm90_disk, values) == 16, "values offset");
static_assert(TIMELM90_DISK_OFFSET >= TIMELM_IRQ_DISK_OFFSET + sizeof(timelm_irq_disk), "no overlap");
static_assert(TIMELM90_DISK_OFFSET + sizeof(timelm90_disk) <= 4096, "fits header");
void reset() {
    timelm90_claimed = 0;
    for (auto &v : timelm90_values) v = 0;
}
void check(unsigned int width, unsigned int height, unsigned int pad) {
    const uint32_t initial = 0x12345678;
    std::vector<uint32_t> memory(32 + (width + pad) * height, initial);
    timelm_diag_width = width; timelm_diag_height = height;
    timelm_diag_stride = (width + pad) * 4;
    timelm_diag_pixels = (uintptr_t)&memory[16]; timelm_irq_strip = 1;
    for (auto &v : iso_font) v = 0xff;
    timelm_diag_enabled = 0; reset(); timelm_status_draw = 1;
    timelm90_panic(1, 1, 2, 3, 4, 5, ~0ULL);
    assert(timelm90_values[TIMELM90_EVENTS] == 0);
    timelm_diag_enabled = 1; timelm_irq_enabled = 1;
    timelm_status_draw = 0;
    timelm90_panic(1, 1, 2, 3, 4, 5, ~0ULL);  // Invalid pointer is recorded, never read.
    assert(timelm90_values[TIMELM90_FORMAT] == ~0ULL);
    assert(timelm90_values[TIMELM90_PANIC_DRAW] == 0);
    for (auto v : memory) assert(v == initial);
    reset(); timelm_status_draw = 1;
    timelm90_panic(2, ~0ULL, ~0ULL, ~0ULL, ~0ULL, ~0ULL, ~0ULL);
    assert(timelm90_values[TIMELM90_EVENT] == 2);
    assert(timelm90_values[TIMELM90_PANIC_DRAW] == 2);
    auto drawn = memory;
    timelm90_panic(1, 9, 9, 9, 9, 9, 9);
    assert(timelm90_values[TIMELM90_EVENTS] == 2);
    assert(timelm90_values[TIMELM90_PC] == ~0ULL && memory == drawn);
    unsigned int top = height - 96;
    for (unsigned int y = 0; y < height; y++) {
        for (unsigned int x = 0; x < width + pad; x++) {
            bool expected = y >= top && x < width;
            assert((memory[16 + y * (width + pad) + x] != initial) == expected);
        }
    }
    for (unsigned int i = 0; i < 16; i++) {
        assert(memory[i] == initial); assert(memory[memory.size() - 1 - i] == initial);
    }
    reset(); timelm90_timer(5, 12345); timelm90_stage(6, 0);
    assert(timelm90_values[TIMELM90_IRQ_FULL] == 1 && memory == drawn);
    assert(timelm90_values[TIMELM90_TIMER_CTL] == 5 && timelm90_values[TIMELM90_TIMER_CVAL] == 12345);
    timelm90_stage(5, 1);
    // Only the Q glyph's 32x32 region may change on a progress draw.
    for (unsigned int y = 0; y < height; y++) {
        for (unsigned int x = 0; x < width + pad; x++) {
            bool q = y >= top && y < top + 32 && x >= width - 32 && x < width;
            if (!q) assert(memory[16 + y * (width + pad) + x] == drawn[16 + y * (width + pad) + x]);
        }
    }
    reset(); timelm_diag_pixels = 0;
    timelm90_panic(1, 1, 2, 3, 4, 5, ~0ULL);
    assert(timelm90_values[TIMELM90_PANIC_DRAW] == 0);
}
int main() { check(32, 97, 4); check(1080, 2460, 8); check(1125, 2436, 0); }
'''
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'fault.cpp'; path.write_text(harness)
            exe = Path(temp) / 'fault-test'
            p = subprocess.run(['c++', '-std=c++17', '-O1', '-fsanitize=address,undefined', str(path), '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            p = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)

    def test_decoder_event_and_unknown_version(self):
        for version in (1, 2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                b = bytearray(8192)
                struct.pack_into('<8sIIQQ', b, 0, b'SWEETLOG', 1, 4096, 5, 8)
                b[4096:4101] = b'boot\n'
                struct.pack_into('<8sII14Q', b, 448, b'TIMELM90', version, 128,
                                 2, 1, 0xfffffe0007000010, 123, 456, 789, 10, 11, 6, 1000, 5, 12345, 1, 2)
                f = Path(temp) / 'ring.bin'; f.write_bytes(b)
                p = subprocess.run([sys.executable, str(ROOT / 'tools/sweet-log.py'), str(f)], capture_output=True, text=True)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(p.stdout, 'boot\n')
                if version == 1:
                    line = next(x for x in p.stderr.splitlines() if x.startswith('# TIMELM90 '))
                    info = json.loads(line.removeprefix('# TIMELM90 '))
                    self.assertEqual(info['event_kind'], 2)
                    self.assertEqual(info['irq_stage'], 6)
                    self.assertEqual(info['timer_cval'], 12345)
                else:
                    self.assertIn('unsupported TIMELM90', p.stderr)


if __name__ == '__main__':
    unittest.main()
