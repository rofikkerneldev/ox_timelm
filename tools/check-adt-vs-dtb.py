#!/usr/bin/env python3
"""Cross-check the Apple Device Tree we build against the phone's own DTB.

The ADT (tools/mkadt.py) is hand-written: every MMIO address in it is a
constant that was copied from another SoC at some point.  This script compares
those constants with the device tree the LG V60 bootloader actually ships
(dtb/boot_unpacked/dtb) so a wrong address cannot survive a port.

  usage: tools/check-adt-vs-dtb.py DTB ADT [--soc-base 0x1000000]

The DTB is parsed here (no dtc needed).  ADT parsing reuses tools/adt.py.
Exit status is non-zero when a checked value differs.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

FDT_MAGIC = 0xd00dfeed
FDT_BEGIN_NODE, FDT_END_NODE, FDT_PROP, FDT_NOP, FDT_END = 1, 2, 3, 4, 9


def parse_dtb(path):
    """Flattened device tree -> {node path: {property: raw bytes}}."""
    buf = open(path, 'rb').read()
    magic, totalsize, off_struct, off_strings, _rsv, _ver, _last, _boot, \
        size_strings, size_struct = struct.unpack('>10I', buf[:40])
    if magic != FDT_MAGIC:
        raise SystemExit('%s: not a device tree blob' % path)
    strings = buf[off_strings:off_strings + size_strings]
    pos, path, out = off_struct, '', {}
    depth = 0
    while pos < off_struct + size_struct:
        (tag,) = struct.unpack('>I', buf[pos:pos + 4])
        pos += 4
        if tag == FDT_BEGIN_NODE:
            end = buf.index(b'\0', pos)
            name = buf[pos:end].decode()
            pos = (end + 4) & ~3
            path = path + '/' + name if name else path
            out.setdefault(path, {})
            depth += 1
        elif tag == FDT_END_NODE:
            depth -= 1
            path = path.rsplit('/', 1)[0]
        elif tag == FDT_PROP:
            length, nameoff = struct.unpack('>II', buf[pos:pos + 8])
            pos += 8
            end = strings.index(b'\0', nameoff)
            name = strings[nameoff:end].decode()
            value = buf[pos:pos + length]
            pos = (pos + length + 3) & ~3
            out.setdefault(path, {})[name] = value
        elif tag == FDT_NOP:
            continue
        elif tag == FDT_END:
            break
        else:
            raise SystemExit('%s: bad tag %d at %d' % (path, tag, pos))
    return out


def parse_adt(path):
    """Apple device tree -> {node path: {property: raw bytes}}."""
    root, _ = adt.parse(adt.load(path))
    return adt_nodes(root)


def adt_nodes(root):
    """Flatten an already parsed ADT, including one embedded in a payload."""
    out = {}

    def walk(node, path):
        props = {}
        for name, _flags, value in node.props:
            if name != 'name':
                props[name] = value
        out[path] = props
        for child in node.children:
            name = dict((k, v) for k, _f, v in child.props).get('name', b'')
            name = name.rstrip(b'\0').decode()
            walk(child, path + '/' + name)

    walk(root, '')
    return out


def cells(value, endian='>'):
    """Property bytes -> list of u32 cells."""
    n = len(value) // 4
    return list(struct.unpack('%s%dI' % (endian, n), value[:n * 4]))


def cells64(value, endian='>'):
    n = len(value) // 8
    return list(struct.unpack('%s%dQ' % (endian, n), value[:n * 8]))


def find(nodes, pred):
    for path, props in nodes.items():
        if pred(path, props):
            return path, props
    return None, None


class Checker:
    def __init__(self):
        self.failed = 0

    def check(self, what, got, want):
        ok = got == want
        print('%-4s %-22s got %-24s want %s' % ('OK' if ok else 'FAIL', what,
                                               got, want))
        if not ok:
            self.failed += 1
        return ok

    def info(self, what, text):
        print('---- %-22s %s' % (what, text))

    def missing(self, what):
        self.check(what, 'missing', 'present')


def virtual_timer_intid(nodes):
    """Decode the virtual timer interrupt, including the GIC PPI base of 16."""
    _, timer = find(nodes, lambda p, pr: b'arm,armv8-timer' in
                    pr.get('compatible', b'').split(b'\0'))
    if timer is None:
        raise ValueError('missing arm,armv8-timer node')
    names = timer.get('interrupt-names', b'').rstrip(b'\0').split(b'\0')
    index = names.index(b'virt') if b'virt' in names else 2
    irq = cells(timer.get('interrupts', b''))
    if len(irq) % 3 or len(irq) < 3 * (index + 1):
        raise ValueError('missing or malformed virtual timer interrupt tuple')
    kind, number, _flags = irq[3 * index:3 * index + 3]
    if kind != 1 or not 0 <= number < 16:
        raise ValueError('virtual timer must be a GIC PPI numbered 0..15')
    return number + 16


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dtb')
    parser.add_argument('adt')
    parser.add_argument('--soc-base', type=lambda s: int(s, 0), default=0x01000000)
    args = parser.parse_args(argv[1:])
    return check_nodes(parse_dtb(args.dtb), parse_adt(args.adt), args.soc_base)


def check_nodes(dtb, adt_nodes, soc_base=0x01000000):
    c = Checker()

    try:
        timer = virtual_timer_intid(dtb)
        raw = adt_nodes.get('/arm-io/gic', {}).get('virtual-timer-intid', b'')
        if len(raw) != 4:
            raise ValueError('missing or malformed ADT virtual-timer-intid')
        c.check('virtual timer INTID', cells(raw, '<')[0], timer)
    except ValueError as error:
        c.missing(str(error))

    # --- GIC: /arm-io/gic in the ADT, "interrupt-controller" in the DTB ------
    adt_gic = adt_nodes.get('/arm-io/gic', {}).get('reg')
    dtb_gic_path, dtb_gic = find(dtb, lambda p, pr: 'interrupt-controller' in pr and
                                 pr.get('compatible', b'').startswith(b'arm,gic'))
    if adt_gic is None or dtb_gic is None:
        c.missing('gic registers')
    else:
        adt_reg = cells64(adt_gic, '<')
        dtb_reg = cells64(dtb_gic['reg'])
        c.check('gic distributor', hex(adt_reg[0] + soc_base), hex(dtb_reg[0]))
        c.check('gic distributor size', hex(adt_reg[1]), hex(dtb_reg[1]))
        c.check('gic redistributor', hex(adt_reg[2] + soc_base), hex(dtb_reg[2]))
        c.check('gic redistributor size', hex(adt_reg[3]), hex(dtb_reg[3]))

    # --- UFS host: /sweet-ufs in the ADT, ufshc@... in the DTB --------------
    adt_ufs = adt_nodes.get('/sweet-ufs', {}).get('reg')
    dtb_ufs_path, dtb_ufs = find(dtb, lambda p, pr: 'ufshc' in p)
    if adt_ufs is None or dtb_ufs is None:
        c.missing('ufs registers')
    else:
        adt_reg, dtb_reg = cells64(adt_ufs, '<'), cells64(dtb_ufs['reg'])
        c.check('ufs host', hex(adt_reg[0]), hex(dtb_reg[0]))
        c.check('ufs host size', hex(adt_reg[1]), hex(dtb_reg[1]))

    # --- framebuffer: /sweet-fbcon in the ADT, splash memory in the DTB -----
    adt_fb = adt_nodes.get('/sweet-fbcon', {}).get('reg')
    if adt_fb is not None:
        fb = cells64(adt_fb, '<')
        regions = []
        for path, props in dtb.items():
            if path.startswith('/reserved-memory/') and 'reg' in props:
                r = cells64(props['reg'])
                regions.append((r[0], r[1], path))
        inside = [r for r in regions if r[0] <= fb[0] and fb[0] + fb[1] <= r[0] + r[1]]
        if inside:
            c.check('framebuffer inside', inside[0][2], inside[0][2])
            c.info('framebuffer', '%s size %s in %s' % (hex(fb[0]), hex(fb[1]), inside[0][2]))
        else:
            c.check('framebuffer reserved', False, True)
    else:
        c.missing('framebuffer registers')

    # --- CPU topology: MPIDR affinities must match the phone's ------------
    adt_cpus = {}
    for path, props in adt_nodes.items():
        if path.startswith('/cpus/cpu') and 'reg' in props:
            adt_cpus[path.rsplit('/', 1)[1]] = cells(props['reg'], '<')[0]
    dtb_cpus = {}
    for path, props in dtb.items():
        if path.startswith('/cpus/cpu') and 'reg' in props:
            dtb_cpus[path.rsplit('/', 1)[1]] = cells64(props['reg'])[0]
    if not dtb_cpus:
        c.missing('DTB CPU nodes')
    c.check('cpu count', len(adt_cpus), len(dtb_cpus))
    for name in sorted(dtb_cpus, key=lambda n: dtb_cpus[n]):
        adt_name = 'cpu%d' % (dtb_cpus[name] >> 8)
        c.check('mpidr %s' % name, hex(adt_cpus.get(adt_name, -1)), hex(dtb_cpus[name]))

    print()
    print('FAILED %d check(s)' % c.failed)
    return 1 if c.failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
