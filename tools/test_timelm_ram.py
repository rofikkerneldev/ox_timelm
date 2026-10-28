#!/usr/bin/env python3
"""Reject the actual old RAM hole and audit boundary/reservation mistakes."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ramcheck', ROOT / 'tools/check-ram-window.py')
ram = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ram)


class RAMTests(unittest.TestCase):
    def setUp(self):
        self.audit = json.loads((ROOT / 'docs/evidence/0090-recovery-ram-audit.json').read_text())

    def test_live_control_window_and_old_hole(self):
        self.assertTrue(ram.validate(0x100000000, 0x80000000, self.audit)['live_bank_coverage'])
        with self.assertRaisesRegex(ValueError, 'hole'):
            ram.validate(0x100000000, 0x170000000, self.audit)

    def test_reserved_and_memreserve_and_touching_end(self):
        for key in ('reserved', 'memreserve'):
            audit = copy.deepcopy(self.audit)
            audit[key].append(dict(base='0x140000000', end_exclusive='0x140004000'))
            with self.assertRaisesRegex(ValueError, 'reserved'):
                ram.validate(0x100000000, 0x80000000, audit)
            audit[key][-1] = dict(base='0x180000000', end_exclusive='0x180004000')
            ram.validate(0x100000000, 0x80000000, audit)

    def test_adjacent_banks_and_linux_coverage(self):
        audit = copy.deepcopy(self.audit)
        audit['banks'] = [dict(base='0x100000000', end_exclusive='0x140000000'),
                          dict(base='0x140000000', end_exclusive='0x180000000')]
        ram.validate(0x100000000, 0x80000000, audit)
        audit['system_ram'] = [dict(base='0x100000000', end_exclusive='0x140000000')]
        with self.assertRaisesRegex(ValueError, 'System RAM'):
            ram.validate(0x100000000, 0x80000000, audit)

    def test_invalid_and_missing_ranges_fail_closed(self):
        for base, size in [(0, 0), (-1, 16384), (1 << 64, 16384),
                           (0x100004000, 0x80000000), (0x100000000, 1)]:
            with self.assertRaises(ValueError):
                ram.validate(base, size, self.audit)
        audit = copy.deepcopy(self.audit)
        audit['banks'] = []
        with self.assertRaises(ValueError):
            ram.validate(0x100000000, 0x80000000, audit)
        del audit['reserved']
        with self.assertRaises(KeyError):
            ram.validate(0x100000000, 0x80000000, audit)


if __name__ == '__main__':
    unittest.main()
