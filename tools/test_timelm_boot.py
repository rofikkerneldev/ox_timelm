#!/usr/bin/env python3
"""Regression checks for timelm timer routing, SGI encoding and payload integrity.

XNU_SOURCE=/path/to/patched/xnu enables compilation of the actual SGI expression.
"""
import contextlib
import io
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

import adt
import inspect_boot
import mkadt
import mkpayload

dtcheck = inspect_boot.dtcheck


class TimerTests(unittest.TestCase):
    def setUp(self):
        self.timer = {'/timer': {'compatible': b'arm,armv8-timer\0',
            'interrupts': struct.pack('>12I', 1, 13, 8, 1, 14, 8, 1, 11, 8, 1, 10, 8)}}

    def test_virtual_ppi_is_relative_to_16(self):
        self.assertEqual(dtcheck.virtual_timer_intid(self.timer), 27)

    def test_named_virtual_irq(self):
        self.timer['/timer']['interrupt-names'] = b'phys\0virt\0'
        self.timer['/timer']['interrupts'] = struct.pack('>6I', 1, 14, 8, 1, 11, 8)
        self.assertEqual(dtcheck.virtual_timer_intid(self.timer), 27)

    def test_missing_timer_is_an_error(self):
        with self.assertRaises(ValueError):
            dtcheck.virtual_timer_intid({})

    def test_spi_cannot_be_a_timer_ppi(self):
        self.timer['/timer']['interrupts'] = struct.pack('>9I', 1, 13, 8, 1, 14, 8, 0, 11, 8)
        with self.assertRaises(ValueError):
            dtcheck.virtual_timer_intid(self.timer)

    def test_missing_required_nodes_fail_checker(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(dtcheck.check_nodes(self.timer, {}), 1)

    def test_generated_timelm_adt_uses_27(self):
        nodes = dtcheck.adt_nodes(mkadt.build(seed=bytes(64), highmem=True))
        self.assertEqual(struct.unpack('<I', nodes['/arm-io/gic']['virtual-timer-intid'])[0], 27)


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.tree = self.directory / 'test.adt'
        self.tree.write_bytes(adt.serialize(mkadt.build(seed=bytes(64), highmem=True)))
        # A small independent Mach-O fixture: one file-backed __TEXT and a thread PC.
        segment = struct.pack('<II16sQQQQIIII', 0x19, 72, b'__TEXT',
                              0x100000000, 0x4000, 0, 512, 5, 5, 0, 0)
        state = [0] * 33
        state[32] = 0x100000100
        thread = struct.pack('<4I33Q2I', 5, 288, 6, 68, *state, 0, 0)
        header = struct.pack('<8I', 0xfeedfacf, 0x100000c, 0, 2, 2, len(segment + thread), 1, 0)
        self.kernel = (header + segment + thread).ljust(512, b'\0')
        self.path = self.directory / 'kernel'
        self.path.write_bytes(self.kernel)
        self.payload, _ = mkpayload.build(self.path, self.tree, 'cpus=1 rd=md0')

    def test_payload_matches_expected_kernel(self):
        info, _ = inspect_boot.payload_info(self.payload, self.kernel)
        self.assertEqual(info['kernel_segments_verified'], 1)
        self.assertEqual(info['virtual_timer_intid'], 27)

    def test_modified_kernel_segment_is_rejected(self):
        data = bytearray(self.payload)
        offset = struct.unpack_from('<Q', data, mkpayload.SEG_TABLE + 16)[0]
        data[offset + 500] ^= 1
        with self.assertRaisesRegex(ValueError, 'segment bytes differ'):
            inspect_boot.payload_info(data, self.kernel)

    def test_truncated_payload_is_rejected(self):
        with self.assertRaises(ValueError):
            inspect_boot.payload_info(self.payload[:700])

    def test_command_line_cannot_be_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, 'too long'):
            mkpayload.build(self.path, self.tree, 'x' * 608)

    def test_unaligned_ram_is_rejected(self):
        self.tree.write_bytes(adt.serialize(mkadt.build(seed=bytes(64), phys_base=0xb0280000,
                                                     mem_size=0x3fd80000)))
        with self.assertRaisesRegex(ValueError, 'aligned'):
            mkpayload.build(self.path, self.tree, 'cpus=1')


class SGITests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('XNU_SOURCE'), 'set XNU_SOURCE for patched kernel encoder test')
    def test_actual_kernel_encoder_against_architecture_vectors(self):
        source = (Path(os.environ['XNU_SOURCE']) / 'iokit/Kernel/arm/SweetCPU.cpp').read_text()
        expression = re.search(r'uint64_t sgi = (.*?);', source, re.S).group(1)
        program = '#include <stdint.h>\n#include <assert.h>\n#define SWEET_IPI_SGI 1\n'
        program += 'static uint64_t encode(uint64_t mpidr) { unsigned aff0 = mpidr & 255; return ' + expression + '; }\n'
        program += '''int main(void) {
            assert(encode(0) == UINT64_C(0x01000001));
            assert(encode(0x100) == UINT64_C(0x01010001));
            assert(encode(0x700) == UINT64_C(0x01070001));
            assert(encode(UINT64_C(0x1200345603)) == UINT64_C(0x12003401560008));
            return 0;
        }\n'''
        with tempfile.TemporaryDirectory() as directory:
            src, binary = Path(directory) / 'sgi.c', Path(directory) / 'sgi'
            src.write_text(program)
            subprocess.run([os.environ.get('CC', 'cc'), '-Wall', '-Wextra', '-Werror', str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


class WatchdogTests(unittest.TestCase):
    def test_opt_in_gate_and_exact_register_write(self):
        program = r'''
        #include <assert.h>
        #include <string.h>
        #include "watchdog.h"
        int main(void) {
            assert(!timelm_wdt_requested("cpus=1", 7));
            assert(!timelm_wdt_requested("x_timelm_wdt=stop", 17));
            assert(!timelm_wdt_requested("timelm_wdt=stopmore", 19));
            assert(!timelm_wdt_requested("timelm_wdt=stop", 5));
            assert(timelm_wdt_requested("timelm_wdt=stop", 15));
            assert(timelm_wdt_requested("cpus=1\ttimelm_wdt=stop rd=md0", 29));
            uint32_t regs[8] = {0x1234, 0x5678, 3, 0x456, 0x789, 0xabc, 0xdef, 0xabc};
            uint32_t saved[8];
            memcpy(saved, regs, sizeof(regs));
            struct timelm_wdt_state s = {0};
            assert(timelm_wdt_stop(regs, &s) == 0);
            assert(s.before == 3 && s.after == 0);
            assert(s.status == 0x456 && s.bark == 0x789 && s.bite == 0xabc);
            for (unsigned i = 0; i < 8; ++i)
                assert(regs[i] == (i == 2 ? 0 : saved[i]));
            assert(timelm_wdt_stop(regs, &s) == 0 && s.before == 0 && s.after == 0);
            regs[2] = UINT32_MAX;
            assert(timelm_wdt_stop(regs, &s) == -1 && regs[2] == UINT32_MAX);
            return 0;
        }
        '''
        with tempfile.TemporaryDirectory() as directory:
            source, binary = Path(directory) / 'wdt.c', Path(directory) / 'wdt'
            source.write_text(program)
            shim = Path(__file__).resolve().parent.parent / 'shim'
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I', str(shim), str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


class DisplayTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('XNU_SOURCE'), 'set XNU_SOURCE for actual framebuffer geometry')
    def test_actual_geometry_matches_iphone_canvas_on_taller_v60(self):
        source = (Path(os.environ['XNU_SOURCE']) / 'iokit/Kernel/arm/SweetFramebuffer.cpp').read_text()
        start = source.index('\tfLogicalW = fWidth;')
        end = source.index('\n\tfLock =', start)
        expression = source[start:end]
        program = '#include <stdint.h>\n#include <assert.h>\n'
        program += '#define SWEET_FB_LOGICAL_W 1125\n#define SWEET_FB_LOGICAL_H 2436\n'
        program += '''static void check(uint32_t fWidth, uint32_t fHeight, int native,
                uint32_t wantW, uint32_t wantH, int32_t wantX, int32_t wantY) {
            uint32_t fLogicalW, fLogicalH;
            int32_t fOffX, fOffY;
        ''' + expression + '''
            assert(fLogicalW == wantW && fLogicalH == wantH);
            assert(fOffX == wantX && fOffY == wantY);
        }
        int main(void) {
            check(1080, 2460, 0, 1125, 2436, 22, -12);
            check(1080, 2400, 0, 1125, 2436, 22, 18);
            check(1080, 2460, 1, 1080, 2460, 0, 0);
            return 0;
        }
        '''
        with tempfile.TemporaryDirectory() as directory:
            src, binary = Path(directory) / 'geometry.c', Path(directory) / 'geometry'
            src.write_text(program)
            subprocess.run([os.environ.get('CC', 'cc'), '-Wall', '-Wextra', '-Werror', str(src), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
