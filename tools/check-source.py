#!/usr/bin/env python3
"""Check migrated shell/Python source and the pinned timelm configuration."""
from pathlib import Path
import ast
import json
import subprocess

root = Path(__file__).resolve().parent.parent
files = subprocess.check_output(['git', '-C', str(root), 'ls-files'], text=True).splitlines()
for name in files:
    p = root / name
    if not p.is_file():
        continue
    if p.suffix == '.py':
        ast.parse(p.read_text(), filename=name)
    elif p.suffix == '.sh':
        subprocess.run(['bash', '-n', str(p)], check=True)
sources = json.loads((root / 'common/sources.json').read_text())
assert sources['device'] == 'timelm' and sources['root_filesystem_writes'] is False
pins = json.loads((root / 'uefi/timelm/baseline.json').read_text())
assert pins['commit'] == sources['uefi']['commit']
assert not (root / 'xnu/patches/0091-timelm-bounded-gui-snapshots.patch').exists()
print('Migrated source syntax and source/UEFI pins verified.')
