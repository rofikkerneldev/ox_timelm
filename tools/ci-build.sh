#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export THEOS=${THEOS:-$PWD/.theos}
export JOBS=${JOBS:-$(nproc)}
mkdir -p out
DEVICE=timelm xnuloader/build.sh
cp xnuloader/loader.efi out/loader.efi
[ -d xnu-src/xnu-8796.121.2 ] || xnu/apply.sh
tools/setup-linux-sdk.sh
xnu-deps/setup.sh
start=$(date +%s)
set +e
./xnu-build.sh -j"$JOBS" -k 2>&1 | tee out/kernel-build.log
rc=${PIPESTATUS[0]}
set -e
python3 tools/verify-kernel-build.py "$start" "$rc" out/kernel-build.log
