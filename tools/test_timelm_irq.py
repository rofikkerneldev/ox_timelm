#!/usr/bin/env python3
"""Exercise the actual 0089 IRQ strip for bounds, gating, cadence and disk ABI."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


def additions(patch, target):
    section = patch.split('+++ b/' + target + '\n', 1)[1].split('\n--- ', 1)[0]
    return '\n'.join(line[1:] for line in section.splitlines() if line.startswith('+'))


class IRQTests(unittest.TestCase):
    def test_actual_irq_code(self):
        patch = (ROOT / 'xnu/patches/0089-timelm-independent-timer-irq-strip.patch').read_text()
        if os.environ.get('XNU_SOURCE'):
            source = Path(os.environ['XNU_SOURCE'])
            header = (source / 'pexpert/pexpert/arm64/SWEET.h').read_text()
            header = header[header.index('#ifndef ASSEMBLER\n/* Opt-in 0088'):]
            header = header[:header.index('#endif /* !ASSEMBLER */')]
            code = (source / 'iokit/Kernel/arm/SweetFramebuffer.cpp').read_text()
            code = code[code.index('/* 0089: only CPU0 timer IRQ'):]
            code = code[:code.index('class IOMobileFramebufferUserClient;')]
        else:
            old = (ROOT / 'xnu/patches/0088-timelm-independent-liveness-diagnostics.patch').read_text()
            header = additions(old, 'pexpert/pexpert/arm64/SWEET.h')
            header = header[header.index('#ifndef ASSEMBLER'):]
            header = header[:header.index('#endif /* !ASSEMBLER */')]
            header += '\nextern "C" {\n' + additions(patch, 'pexpert/pexpert/arm64/SWEET.h') + '\n}\n'
            code = additions(patch, 'iokit/Kernel/arm/SweetFramebuffer.cpp')
            code = code[code.index('/* 0089: only CPU0 timer IRQ'):code.index('    uint32_t irqdiagnostics = 0;')]
        if '/* 0090:' in code:
            code = code[:code.index('/* 0090:')]
        # Keep production draw/format/entry/done code, replacing only ARM barrier.
        self.assertEqual(code.count('__asm__ volatile("dsb sy" ::: "memory");'), 1)
        code = code.replace('__asm__ volatile("dsb sy" ::: "memory");',
                            '__atomic_thread_fence(__ATOMIC_SEQ_CST);')
        for forbidden in ('printf(', 'snprintf(', 'IOLock', 'IOLog(', 'IOMalloc', 'ufs_'):
            self.assertNotIn(forbidden, code)
        harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <assert.h>
#include <vector>
''' + header + r'''
#endif
unsigned int timelm_diag_enabled, timelm_irq_enabled;
unsigned int timelm_status_draw = 1, timelm90_claimed;
unsigned long long timelm_diag_values[TIMELM_D_COUNT];
unsigned long long timelm_irq_values[TIMELM_I_COUNT], timelm_irq_period;
char timelm_diag_trial[64];
static uintptr_t timelm_diag_pixels;
static uint32_t timelm_diag_width, timelm_diag_height, timelm_diag_stride;
unsigned char iso_font[256 * 16];
static unsigned long long fake_now;
static unsigned long long mach_absolute_time() { return fake_now; }
''' + code + r'''
static_assert(sizeof(timelm_irq_disk) == 80, "disk ABI");
static_assert(offsetof(timelm_irq_disk, values) == 16, "values offset");
static_assert(TIMELM_IRQ_DISK_OFFSET >= TIMELM_DIAG_DISK_OFFSET + sizeof(timelm_diag_disk), "no header overlap");
static_assert(TIMELM_IRQ_DISK_OFFSET + sizeof(timelm_irq_disk) <= 4096, "fits header");
void check(unsigned int w, unsigned int h, unsigned int pad) {
    const uint32_t initial = 0x12345678;
    std::vector<uint32_t> mem(32 + (w + pad) * h, initial);
    timelm_diag_pixels = (uintptr_t)&mem[16];
    timelm_diag_width = w; timelm_diag_height = h; timelm_diag_stride = (w + pad) * 4;
    timelm_irq_strip = 1; timelm_irq_period = 100;
    for (auto &v : timelm_irq_values) v = 0;
    for (auto &v : iso_font) v = 0xff;
    timelm_irq_enabled = 0; fake_now = 100;
    assert(timelm_irq_enter(0xabcdef, 5) == 0); timelm_irq_done(0);
    assert(timelm_irq_values[TIMELM_I_ENTER] == 0);
    timelm_irq_enabled = 1; fake_now = 99;
    assert(timelm_irq_enter(0xabcdef, 5) == 0); timelm_irq_done(0);
    assert(timelm_irq_values[TIMELM_I_ENTER] == 1);
    for (auto v : mem) assert(v == initial);
    fake_now = 100;
    auto redraw = timelm_irq_enter(0xfffffe0007aabbccULL, 5);
    assert(redraw == 1 && timelm_irq_values[TIMELM_I_PHASE] == 1);
    assert(timelm_irq_values[TIMELM_I_PC] == 0xfffffe0007aabbccULL);
    timelm_irq_done(redraw);
    assert(timelm_irq_values[TIMELM_I_DONE] == 2 && timelm_irq_values[TIMELM_I_PHASE] == 2);
    auto saved = mem; fake_now = 101;
    assert(timelm_irq_enter(0x100000004, 0) == 0); timelm_irq_done(0);
    assert(saved == mem);  // IRQ counters progress without another framebuffer draw.
    timelm_irq_values[TIMELM_I_ENTER] = ~0ULL - 1;
    timelm_irq_values[TIMELM_I_DONE] = ~0ULL - 1;
    timelm_diag_values[TIMELM_D_HEARTBEAT] = ~0ULL;
    fake_now = 200; redraw = timelm_irq_enter(~0ULL, 0); assert(redraw == 1);
    timelm_irq_done(redraw);  // Longest decimal/hex strings exercise the stack buffer.
    unsigned int top = h - TIMELM_DIAG_ROWS - TIMELM_IRQ_ROWS;
    for (unsigned int y = 0; y < h; y++) {
        for (unsigned int x = 0; x < w + pad; x++) {
            bool in_strip = y >= top && y < top + TIMELM_IRQ_ROWS && x < w;
            assert((mem[16 + y * (w + pad) + x] != initial) == in_strip);
        }
    }
    for (unsigned int i = 0; i < 16; i++) {
        assert(mem[i] == initial); assert(mem[mem.size() - i - 1] == initial);
    }
    saved = mem; timelm_irq_strip = 0; fake_now = 300;
    assert(timelm_irq_enter(8, 0) == 0); timelm_irq_done(0); assert(mem == saved);
    timelm_diag_pixels = 0; timelm_irq_strip = 1;
    assert(timelm_irq_enter(8, 0) == 0); timelm_irq_done(0);
}
int main() {
    check(16, 97, 4); check(1080, 2460, 8); check(1125, 2436, 0);
    char s[21]; char *end = timelm_irq_number(s, ~0ULL, 10); *end = 0;
    assert(end - s == 20);
}
'''
        if 'timelm_status_draw &&' in code:
            harness = harness.replace('saved = mem; timelm_irq_strip = 0;', '''
    saved = mem; timelm_status_draw = 0; fake_now = 300;
    auto before = timelm_irq_values[TIMELM_I_ENTER];
    assert(timelm_irq_enter(8, 0) == 0); timelm_irq_done(0);
    assert(mem == saved && timelm_irq_values[TIMELM_I_ENTER] == before + 1);
    timelm_status_draw = 1;
    saved = mem; timelm_irq_strip = 0;''')
        with tempfile.TemporaryDirectory() as temp:
            f = Path(temp) / 'irq.cpp'; f.write_text(harness)
            exe = Path(temp) / 'irq-test'
            run = subprocess.run(['c++', '-std=c++17', '-O1', '-fsanitize=address,undefined',
                                  str(f), '-o', str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_irq_decoder_and_unknown_version(self):
        for version in (1, 2):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                data = bytearray(8192)
                struct.pack_into('<8sIIQQ', data, 0, b'SWEETLOG', 1, 4096, 5, 7)
                data[4096:4101] = b'boot\n'
                struct.pack_into('<8sII8Q', data, 320, b'TIMELMIQ', version, 80,
                                 100, 99, 999, 998, 0xfffffe0007000000, 5, 1, 997)
                f = Path(temp) / 'ring.bin'; f.write_bytes(data)
                run = subprocess.run([sys.executable, str(ROOT / 'tools/sweet-log.py'), str(f)],
                                     capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stdout, 'boot\n')
                if version == 1:
                    line = next(x for x in run.stderr.splitlines() if x.startswith('# TIMELM_IRQ '))
                    info = json.loads(line.removeprefix('# TIMELM_IRQ '))
                    self.assertEqual(info['entered'], 100)
                    self.assertEqual(info['handler_done'], 99)
                    self.assertEqual(info['interrupted_pc'], 0xfffffe0007000000)
                else:
                    self.assertIn('unsupported TIMELM_IRQ', run.stderr)


if __name__ == '__main__':
    unittest.main()
