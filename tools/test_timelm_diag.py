#!/usr/bin/env python3
"""Check diagnostic disk compatibility and bounds of the production footer code."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent


class DecoderTests(unittest.TestCase):
    def decode(self, content):
        with tempfile.TemporaryDirectory() as name:
            area = Path(name) / 'area.bin'
            area.write_bytes(content)
            return subprocess.run([sys.executable, str(ROOT / 'tools/sweet-log.py'),
                                   str(area)], capture_output=True, text=True)

    def area(self, total=5):
        data = bytearray(4096 + 4096)
        struct.pack_into('<8sIIQQ', data, 0, b'SWEETLOG', 1, 4096, total, 60)
        data[4096:4101] = b'boot\n'
        return data

    def test_legacy_console_is_unchanged(self):
        result = self.decode(self.area())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, 'boot\n')
        self.assertNotIn('TIMELM_DIAG', result.stderr)

    def test_liveness_without_console_bytes(self):
        data = self.area(total=0)
        values = [600, 123456, 12000, 10, 8, 7, 1, 59, 123400,
                  3, 2, 70, 69, 0x35, 5, 2]
        struct.pack_into('<8sII16Q64s', data, 64, b'TIMELMDG', 1, 208,
                         *values, b'0088-fixture')
        result = self.decode(data)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, '')
        line = next(line for line in result.stderr.splitlines()
                    if line.startswith('# TIMELM_DIAG '))
        info = json.loads(line.removeprefix('# TIMELM_DIAG '))
        self.assertEqual(info['heartbeat'], 600)
        self.assertEqual(info['trial'], '0088-fixture')
        self.assertEqual(info['copied'], 7)
        self.assertEqual(info['log_success_seq'], 59)
        self.assertEqual(info['ufs_opcode'], 0x35)
        self.assertEqual(info['ufs_phase'], 2)

    def test_truncated_area_is_rejected(self):
        result = self.decode(self.area()[:-1])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('truncated', result.stderr)

    def test_unknown_extension_preserves_console(self):
        data = self.area()
        struct.pack_into('<8sII', data, 64, b'TIMELMDG', 2, 208)
        result = self.decode(data)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'boot\n')
        self.assertIn('unsupported TIMELM_DIAG', result.stderr)


class FooterTests(unittest.TestCase):
    def test_production_footer_bounds_and_disk_abi(self):
        source = os.environ.get('XNU_SOURCE')
        if source:
            source = Path(source)
            header = (source / 'pexpert/pexpert/arm64/SWEET.h').read_text()
            fb = (source / 'iokit/Kernel/arm/SweetFramebuffer.cpp').read_text()
            ufs = (source / 'bsd/dev/sweet_ufs.c').read_text()
        else:
            patch = (ROOT / 'xnu/patches/0088-timelm-independent-liveness-diagnostics.patch').read_text()
            added = '\n'.join(line[1:] for line in patch.splitlines()
                              if line.startswith('+') and not line.startswith('+++'))
            start = added.index('#ifndef ASSEMBLER\n/* Opt-in 0088')
            end = added.index('#endif /* !ASSEMBLER */', start)
            header = added[start:end + len('#endif /* !ASSEMBLER */')]
            fb = added
            ufs = added
        start = fb.index('extern "C" unsigned char iso_font[];')
        brace = fb.index('{', fb.index('timelm_diag_draw(void)', start))
        depth, end = 1, brace + 1
        while depth:
            depth += (fb[end] == '{') - (fb[end] == '}')
            end += 1
        footer = fb[start:end]
        # Host tests replace only the ARM completion barrier, keeping draw code.
        barrier = '__asm__ volatile("dsb sy" ::: "memory");'
        self.assertEqual(footer.count(barrier), 1)
        footer = footer.replace(barrier, '__atomic_thread_fence(__ATOMIC_SEQ_CST);')
        start = ufs.index('static void\ntimelm_ufs_lock(void)')
        brace = ufs.index('{', start)
        depth, end = 1, brace + 1
        while depth:
            depth += (ufs[end] == '{') - (ufs[end] == '}')
            end += 1
        lock = ufs[start:end]
        harness = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <assert.h>
#include <string.h>
#include <vector>
''' + header + '\n' + footer + r'''
static int sweet_ufs_lock;
static unsigned int mock_enable;
static void lck_mtx_lock(int *) { timelm_diag_enabled = mock_enable; }
''' + lock + r'''
unsigned int timelm_diag_enabled;
unsigned int timelm_status_draw = 1, timelm90_claimed;
unsigned long long timelm_diag_values[TIMELM_D_COUNT];
char timelm_diag_trial[64];
unsigned char iso_font[256 * 16];
static_assert(TIMELM_D_COUNT == 16, "disk metric order/count");
static_assert(sizeof(timelm_diag_disk) == 208, "disk extension size");
static_assert(offsetof(timelm_diag_disk, values) == 16, "disk values offset");
static_assert(offsetof(timelm_diag_disk, trial) == 144, "disk trial offset");
void check(unsigned int width, unsigned int height, unsigned int pad) {
    const unsigned int guard = 16, stride = width + pad;
    std::vector<uint32_t> memory(guard * 2 + stride * height, 0x12345678);
    timelm_diag_width = width;
    timelm_diag_height = height;
    timelm_diag_stride = stride * 4;
    timelm_diag_pixels = (uintptr_t)(memory.data() + guard);
    timelm_diag_enabled = 1;
    timelm_diag_values[TIMELM_D_HEARTBEAT] = 600;
    memset(iso_font, 255, sizeof(iso_font));
    timelm_diag_draw();
    unsigned int white = 0;
    for (unsigned int i = 0; i < memory.size(); i++) {
        if (i < guard || i >= guard + stride * height) {
            assert(memory[i] == 0x12345678);
            continue;
        }
        unsigned int y = (i - guard) / stride, x = (i - guard) % stride;
        if (y < height - TIMELM_DIAG_ROWS || x >= width) {
            assert(memory[i] == 0x12345678);
        } else {
            assert(memory[i] == 0xff000000 || memory[i] == 0xffffffff);
            white += memory[i] == 0xffffffff;
        }
    }
    assert(white > 0);
    std::vector<uint32_t> before = memory;
    timelm_diag_enabled = 0;
    timelm_diag_draw();
    assert(memory == before);
    timelm_diag_enabled = 1;
    timelm_diag_pixels = 0;
    timelm_diag_draw();
    assert(memory == before);
}
int main() {
    check(1080, 2460, 0);
    check(1080, 2460, 8);
    check(16, 65, 4);
    timelm_diag_values[TIMELM_D_UFS_WAITERS] = 0;
    timelm_diag_enabled = 0;
    mock_enable = 1;
    timelm_ufs_lock();
    assert(timelm_diag_values[TIMELM_D_UFS_WAITERS] == 0);
    timelm_diag_enabled = 1;
    mock_enable = 0;
    timelm_ufs_lock();
    assert(timelm_diag_values[TIMELM_D_UFS_WAITERS] == 0);
}
'''
        with tempfile.TemporaryDirectory() as name:
            path = Path(name)
            cpp, binary = path / 'footer.cpp', path / 'footer'
            cpp.write_text(harness)
            subprocess.run(['c++', '-std=c++11', '-O1', str(cpp), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
