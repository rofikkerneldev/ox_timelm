#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
mu=${MU_PATH:-$PWD/Mu-Silicium}
repo=${TIMELM_MU_SOURCE:-git@github.com:rofikkerneldev/test_mu-timelm.git}
commit=$(python3 -c 'import json; print(json.load(open("uefi/timelm/baseline.json"))["commit"])')
if [ ! -d "$mu" ]; then
    git clone --depth 1 --filter=blob:none --no-checkout "$repo" "$mu"
    if ! git -C "$mu" cat-file -e "$commit^{commit}" 2>/dev/null; then git -C "$mu" fetch --depth 1 origin "$commit"; fi
    git -C "$mu" checkout --detach "$commit"
fi
python3 uefi/timelm/prepare.py "$mu" --apply
if [ "${UEFI_INIT_SUBMODULES:-1}" = 1 ]; then
    git -C "$mu" submodule update --init --recursive
fi
printf 'Prepared timelm UEFI at %s; build with Resources/Scripts/build_timelm.sh RELEASE\n' "$mu"
