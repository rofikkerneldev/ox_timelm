#!/usr/bin/env python3
"""Validate an ADT RAM window against a captured, device-specific live RAM audit.

The static timelm DTB has a zero-size memory placeholder. Its MMIO check cannot
validate DRAM. Default evidence is this phone's recovery audit, not a universal
LG V60 memory map. Supply a fresh audited JSON if hardware/topology changes.
"""
import argparse
import hashlib
import json
from pathlib import Path

import adt
import mkpayload

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT = ROOT / 'docs/evidence/0090-recovery-ram-audit.json'


def interval(base, size):
    if not isinstance(base, int) or not isinstance(size, int):
        raise ValueError('RAM range must use integer base/size')
    if base < 0 or size <= 0 or base + size > 1 << 64:
        raise ValueError('empty, negative or overflowing RAM range')
    return base, base + size


def regions(items):
    result = []
    for item in items:
        base = int(item['base'], 0)
        end = int(item['end_exclusive'], 0)
        result.append(interval(base, end - base))
    return result


def covered(window, spans):
    cursor, end = window
    for a, z in sorted(spans):
        if z <= cursor:
            continue
        if a > cursor:
            return False
        cursor = max(cursor, z)
        if cursor >= end:
            return True
    return False


def validate(base, size, audit):
    window = interval(base, size)
    if base % 0x2000000 or size % 0x4000:
        raise ValueError('RAM base must be32MiB aligned and size16KiB aligned')
    banks = regions(audit['banks'])
    system_ram = regions(audit['system_ram'])
    reserved = regions(audit['reserved']) + regions(audit['memreserve'])
    if not covered(window, banks):
        raise ValueError('RAM window crosses a hole or extends outside audited live banks')
    if not covered(window, system_ram):
        raise ValueError('RAM window is not fully covered by audited Linux System RAM')
    if any(a < window[1] and window[0] < z for a, z in reserved):
        raise ValueError('RAM window overlaps an audited reserved region')
    return dict(phys_base=hex(base), mem_size=hex(size), end_exclusive=hex(window[1]),
                live_bank_coverage=True, system_ram_coverage=True,
                reserved_overlap=False, evidence_scope='captured recovery topology for this phone')


def check(base, size, audit_path):
    raw = Path(audit_path).read_bytes()
    result = validate(base, size, json.loads(raw))
    result.update(ram_audit=str(audit_path), ram_audit_sha256=hashlib.sha256(raw).hexdigest())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('adt', type=Path)
    parser.add_argument('--audit', type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    try:
        base, size = mkpayload.adt_dram(adt.load(args.adt))
        report = check(base, size, args.audit)
    except (ValueError, KeyError, TypeError, OSError) as error:
        raise SystemExit('RAM validation failed: ' + str(error)) from error
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
