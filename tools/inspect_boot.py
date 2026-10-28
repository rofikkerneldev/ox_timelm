#!/usr/bin/env python3
"""Validate the XNU payload inside a timelm Android boot v2 image and write a manifest."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess

import adt
import mkpayload

spec = importlib.util.spec_from_file_location('dtcheck', Path(__file__).with_name('check-adt-vs-dtb.py'))
dtcheck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dtcheck)
ram_spec = importlib.util.spec_from_file_location('ramcheck', Path(__file__).with_name('check-ram-window.py'))
ramcheck = importlib.util.module_from_spec(ram_spec)
ram_spec.loader.exec_module(ramcheck)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def boot_payload(data):
    if len(data) < 1660 or data[:8] != b'ANDROID!':
        raise ValueError('not an Android boot image')
    ks, _, rs, _, ss, _, _, page, version = struct.unpack_from('<9I', data, 8)
    if version != 2 or page < 2048 or page & (page - 1):
        raise ValueError('expected boot header v2 and a power-of-two page size')
    align = lambda n: (n + page - 1) // page * page
    offset = page + align(ks)
    dtb_offset = offset + align(rs) + align(ss)
    recovery_size = struct.unpack_from('<I', data, 1632)[0]
    dtb_offset += align(recovery_size)
    dtb_size = struct.unpack_from('<I', data, 1648)[0]
    if not ks or not rs or not dtb_size or dtb_offset + dtb_size > len(data):
        raise ValueError('missing or truncated boot image sections')
    return data[offset:offset + rs], data[dtb_offset:dtb_offset + dtb_size], data[page:page + ks]


def payload_info(payload, kernel=None):
    if len(payload) < mkpayload.SEG_TABLE or payload[:8] != b'SWEETXNU':
        raise ValueError('missing SWEETXNU payload')
    version, count = struct.unpack_from('<2I', payload, 8)
    if version != 1 or not 1 <= count <= 64:
        raise ValueError('unsupported payload version or segment count')
    table_end = mkpayload.SEG_TABLE + count * 32
    if table_end > len(payload):
        raise ValueError('truncated segment table')
    virt, entry, adt_off, adt_size, kernel_end, phys, mem = struct.unpack_from('<7Q', payload, 16)
    if adt_off < table_end or adt_size == 0 or adt_off + adt_size > len(payload):
        raise ValueError('invalid ADT extent')
    if phys % 0x2000000 or not mem or phys + mem > 1 << 64:
        raise ValueError('kernel RAM must have a 32-MiB-aligned base and valid size')
    if not virt <= entry < kernel_end or kernel_end - virt >= mem:
        raise ValueError('entry or kernel extent outside managed RAM')
    command = payload[mkpayload.CMDLINE:mkpayload.SEG_TABLE]
    if b'\0' not in command:
        raise ValueError('unterminated boot command line')
    command = command.split(b'\0')[0].decode()
    segments = []
    for i in range(count):
        va, vs, off, size = struct.unpack_from('<4Q', payload, mkpayload.SEG_TABLE + i * 32)
        if size > vs or off < table_end or off + size > adt_off:
            raise ValueError('invalid payload segment file extent')
        if va < virt or va + vs > kernel_end:
            raise ValueError('segment outside reserved kernel RAM')
        segments.append((va, vs, off, size))
    verified = 0
    if kernel is not None:
        fixed = bytearray(kernel)
        macho = mkpayload.parse_macho(fixed)
        mkpayload.apply_chained_fixups(fixed, macho)
        expected = [s for s in macho['segs'] if s['vmsize']]
        if count not in (len(expected), len(expected) + 1):
            raise ValueError('kernel segment count differs')
        for actual, segment in zip(segments, expected):
            va, vs, off, size = actual
            if (va, vs, size) != (segment['vmaddr'], segment['vmsize'], segment['filesize']):
                raise ValueError('kernel segment metadata differs: ' + segment['name'])
            if payload[off:off + size] != fixed[segment['fileoff']:segment['fileoff'] + size]:
                raise ValueError('kernel segment bytes differ: ' + segment['name'])
            verified += 1
    blob = payload[adt_off:adt_off + adt_size]
    root, _ = adt.parse(blob)
    nodes = dtcheck.adt_nodes(root)
    if mkpayload.adt_dram(blob) != (phys, mem):
        raise ValueError('payload and embedded ADT RAM windows differ')
    raw_timer = nodes.get('/arm-io/gic', {}).get('virtual-timer-intid', b'')
    if len(raw_timer) != 4:
        raise ValueError('missing embedded timer INTID')
    return dict(payload_sha256=sha256(payload), adt_sha256=sha256(blob),
                virtual_timer_intid=struct.unpack('<I', raw_timer)[0], cmdline=command,
                phys_base=hex(phys), mem_size=hex(mem), kernel_segments_verified=verified), nodes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--kernel', type=Path, required=True)
    parser.add_argument('--dtb', type=Path, required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--trial')
    parser.add_argument('--ram-audit', type=Path, default=ramcheck.DEFAULT_AUDIT)
    args = parser.parse_args()
    data, kernel = args.image.read_bytes(), args.kernel.read_bytes()
    if b'TIMELM IRQ FIX 0085:' not in kernel:
        raise SystemExit('kernel does not contain the timelm interrupt correction marker')
    try:
        payload, dtb, shim = boot_payload(data)
        if dtb != args.dtb.read_bytes():
            raise ValueError('embedded Android DTB differs from expected DTB')
        info, nodes = payload_info(payload, kernel)
        info['ram_validation'] = ramcheck.check(int(info['phys_base'], 16), int(info['mem_size'], 16), args.ram_audit)
        if 'timelm_wdt=stop' in info['cmdline'].split():
            hardware = dtcheck.parse_dtb(args.dtb)
            _, watchdog = dtcheck.find(hardware, lambda p, props:
                b'qcom,kpss-wdt' in props.get('compatible', b'').split(b'\0'))
            if watchdog is None or dtcheck.cells64(watchdog.get('reg', b'')) != [0x17c10000, 0x1000]:
                raise ValueError('watchdog-stop profile requires the SM8250 APSS watchdog DTB region')
            info['watchdog_action'] = 'shim stops APSS watchdog if enabled; runtime result unverified'
        if args.trial and 'timelm_trial=' + args.trial not in info['cmdline'].split():
            raise ValueError('embedded trial ID differs')
        if dtcheck.check_nodes(dtcheck.parse_dtb(args.dtb), nodes):
            raise ValueError('embedded ADT does not match hardware DTB')
    except (ValueError, struct.error, AssertionError, KeyError, TypeError, OSError) as error:
        raise SystemExit(str(error)) from error
    info.update(image=args.image.name, image_size=len(data), image_sha256=sha256(data),
                kernel_sha256=sha256(kernel), dtb_sha256=sha256(dtb), shim_sha256=sha256(shim))
    repo = Path(__file__).resolve().parent.parent
    info['source_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    info['source_dirty'] = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=repo, text=True))
    info['patches_sha256'] = sha256(b''.join(p.name.encode() + b'\0' + p.read_bytes()
                                           for p in sorted((repo / 'xnu/patches').glob('*.patch'))))
    report = json.dumps(info, indent=2) + '\n'
    if args.manifest:
        args.manifest.write_text(report)
    print(report, end='')


if __name__ == '__main__':
    main()
