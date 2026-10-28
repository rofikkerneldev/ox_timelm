#!/usr/bin/env python3
"""Verify the pinned V60 native-touch baseline and apply only the XNU handoff."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def prepare(mu, apply=False):
    pins = json.loads((HERE / 'baseline.json').read_text())
    head = subprocess.check_output(['git', '-C', str(mu), 'rev-parse', 'HEAD'], text=True).strip()
    if head != pins['commit']:
        raise ValueError('Mu-Silicium commit differs from pinned native-touch baseline')
    for path, expected in pins['inputs_sha256'].items():
        raw = subprocess.check_output(['git', '-C', str(mu), 'show', 'HEAD:' + path])
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('baseline Git blob mismatch: ' + path)
        if '/ACPI/' in path and hashlib.sha256((mu / path).read_bytes()).hexdigest() != expected:
            raise ValueError('working ACPI differs from pinned baseline: ' + path)
    patch = HERE / 'xnu-hook.patch'
    if apply:
        check = subprocess.run(['git', '-C', str(mu), 'apply', '--check', str(patch)], capture_output=True)
        if check.returncode == 0:
            subprocess.run(['git', '-C', str(mu), 'apply', str(patch)], check=True)
        else:
            subprocess.run(['git', '-C', str(mu), 'apply', '--reverse', '--check', str(patch)], check=True)
        dest = mu / 'Silicon/Silicium/SiliciumPkg/Library/BootManagerLib/TimelmPayload.h'
        shutil.copyfile(HERE / 'TimelmPayload.h', dest)
    print('Pinned timelm Mu/ACPI inputs verified' + ('; XNU handoff applied' if apply else ''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mu', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    prepare(args.mu, args.apply)
