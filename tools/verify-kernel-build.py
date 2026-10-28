#!/usr/bin/env python3
"""Validate the fresh Linux Mach-O kernel and distinguish documented export checks."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

start, rc = int(sys.argv[1]), int(sys.argv[2])
log = Path(sys.argv[3]).read_text(errors='replace')
kernel = Path('xnu-src/xnu-8796.121.2/BUILD/obj/DEVELOPMENT_ARM64_BCM2837/mach.development.bcm2837')
if not kernel.is_file() or kernel.stat().st_mtime < start:
    sys.exit('No freshly linked kernel produced')
b = kernel.read_bytes()
if len(b) < 32 or struct.unpack_from('<II', b) != (0xfeedfacf, 0x100000c):
    sys.exit('Kernel is not an arm64 Mach-O')
errors = [line for line in log.splitlines() if re.search(r'\berror:', line, re.I)
          and 'exported name not in import list' not in line]
if errors:
    sys.exit('Compile/link errors remain:\n' + '\n'.join(errors[-12:]))
postlink = rc != 0
if postlink and 'check_all_exports' not in log:
    sys.exit('Build failed outside the documented upstream export-list checks')
shutil.copyfile(kernel, 'out/kernel')
provenance = {'commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
              'sources': json.loads(Path('common/sources.json').read_text()),
              'kernel_sha256': hashlib.sha256(b).hexdigest(),
              'loader_sha256': hashlib.sha256(Path('out/loader.efi').read_bytes()).hexdigest(),
              'make_exit_code': rc, 'upstream_postlink_export_checks_failed': postlink,
              'hardware_tested': False}
Path('out/build-provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
print('Fresh arm64 kernel linked; SHA256=' + provenance['kernel_sha256'])
if postlink:
    print('Upstream export-list checks remain failed; this is recorded in provenance.')
