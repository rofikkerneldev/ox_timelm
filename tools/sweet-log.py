#!/usr/bin/env python3
"""Decode the SWEET persistent console log (kernel patch 0047).

  sweet-log.py AREA.bin [OUT.txt]

AREA.bin is the last (1 + ring/4096) device blocks of the selected root
partition as read by tools/sweet-log-pull.sh: block 0 is the header
("SWEETLOG", u32 version, u32 ring size, u64 total bytes, u64 flush seq),
the ring follows. Byte n of the console stream is at ring offset n % size.
"""
import json, struct, sys

data = open(sys.argv[1], 'rb').read()
magic, version, size, total, seq = struct.unpack_from('<8sIIQQ', data, 0)
if magic != b'SWEETLOG':
    sys.exit('no SWEETLOG header (log never flushed, or wrong area)')
if version != 1 or size == 0 or len(data) < 4096 + size:
    sys.exit('unsupported or truncated SWEETLOG area')
if data[64:72] == b'TIMELMDG':
    diag_version, diag_bytes = struct.unpack_from('<II', data, 72)
    fields = ('heartbeat', 'heartbeat_abs', 'timer_irqs', 'submitted',
              'presented', 'copied', 'rejected', 'log_success_seq',
              'log_success_abs', 'log_phase', 'ufs_waiters', 'ufs_commands',
              'ufs_completed', 'ufs_opcode', 'last_errno', 'ufs_phase')
    if diag_version == 1 and diag_bytes == 208:
        values = struct.unpack_from('<16Q', data, 80)
        diag = dict(zip(fields, values))
        diag['trial'] = data[208:272].split(b'\0')[0].decode('ascii', 'replace')
        print('# TIMELM_DIAG ' + json.dumps(diag, sort_keys=True), file=sys.stderr)
    else:
        print('# unsupported TIMELM_DIAG extension', file=sys.stderr)
if data[320:328] == b'TIMELMIQ':
    irq_version, irq_bytes = struct.unpack_from('<II', data, 328)
    if irq_version == 1 and irq_bytes == 80:
        fields = ('entered', 'handler_done', 'entry_abs', 'handler_done_abs',
                  'interrupted_pc', 'interrupted_cpsr', 'phase', 'draw_abs')
        irq = dict(zip(fields, struct.unpack_from('<8Q', data, 336)))
        print('# TIMELM_IRQ ' + json.dumps(irq, sort_keys=True), file=sys.stderr)
    else:
        print('# unsupported TIMELM_IRQ extension', file=sys.stderr)
if data[448:456] == b'TIMELM90':
    version90, bytes90 = struct.unpack_from('<II', data, 456)
    if version90 == 1 and bytes90 == 128:
        fields = ('event_kind', 'event_count', 'caller_pc', 'fault_pc', 'fault_lr',
                  'esr', 'far', 'format_pointer', 'irq_stage', 'irq_full_done',
                  'timer_ctl', 'timer_cval', 'draw_enabled', 'panic_draw_stage')
        info90 = dict(zip(fields, struct.unpack_from('<14Q', data, 464)))
        print('# TIMELM90 ' + json.dumps(info90, sort_keys=True), file=sys.stderr)
    else:
        print('# unsupported TIMELM90 extension', file=sys.stderr)
if data[640:648] == b'TIMELM91':
    version91, bytes91 = struct.unpack_from('<II', data, 648)
    if version91 == 1 and bytes91 == 104:
        fields = ('vsyncs', 'vsync_abs', 'submit_abs', 'present_abs', 'copy_abs',
                  'power_off', 'power_abs', 'sample_cycle', 'sample_abs',
                  'sample_targets', 'sample_phase')
        info91 = dict(zip(fields, struct.unpack_from('<11Q', data, 656)))
        print('# TIMELM91 ' + json.dumps(info91, sort_keys=True), file=sys.stderr)
    else:
        print('# unsupported TIMELM91 extension', file=sys.stderr)
ring = data[4096:4096 + size]
if total <= size:
    text = ring[:total]
else:
    start = total % size
    text = ring[start:] + ring[:start]
out = text.decode('utf-8', 'replace').replace('\r', '')
print(f'# version {version}, ring {size}, total {total} bytes, flush #{seq}'
      f'{" (wrapped: oldest bytes lost)" if total > size else ""}', file=sys.stderr)
if len(sys.argv) > 2:
    open(sys.argv[2], 'w').write(out)
else:
    sys.stdout.write(out)
