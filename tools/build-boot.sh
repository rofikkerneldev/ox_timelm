#!/bin/bash
# Build a verified timelm boot image. Default: validated2GiB HIGH RAM, one CPU, RAM-disk init.
# Usage: PROFILE=rd|ufs-ro|ufs-rw tools/build-boot.sh [high] [output.img]
# Optional: UFS_PARTITION=ios KERNEL=path ADT=path CMDLINE='...' TRIAL=id JOBS=56 BUILD=directory
# Building writes local artifacts only. A ufs-rw boot writes the selected GPT partition.
set -euo pipefail
cd "$(dirname "$0")/.."

MEM="${1:-high}"
PROFILE="${PROFILE:-rd}"
UFS_PARTITION="${UFS_PARTITION:-ios}"
[[ "$UFS_PARTITION" =~ ^[a-zA-Z0-9_-]{1,36}$ ]] || { echo 'invalid UFS_PARTITION GPT name' >&2; exit 1; }
TRIAL="${TRIAL:-0085-$PROFILE-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ "$TRIAL" =~ ^[a-zA-Z0-9_-]{1,63}$ ]] || { echo 'invalid TRIAL identifier' >&2; exit 1; }
NAME="${2:-timelm-$TRIAL-c1.img}"
KERNEL="${KERNEL:-xnu/out/kernel.development.sweet}"
BUILD="${BUILD:-$HOME/work/timelm-build/$TRIAL}"
mkdir -p "$BUILD"

[ -f "$KERNEL" ] || { echo "missing $KERNEL" >&2; exit 1; }
if [ -z "${ADT:-}" ]; then
    [ "$MEM" = high ] || { echo 'Use explicit ADT= for experimental RAM windows' >&2; exit 1; }
    ADT="$BUILD/timelm-high.adt"
    python3 tools/mkadt.py "$ADT" --mem high
fi
python3 tools/check-adt-vs-dtb.py dtb/boot_unpacked/dtb "$ADT"
RAM_AUDIT="${RAM_AUDIT:-docs/evidence/0090-recovery-ram-audit.json}"
python3 tools/check-ram-window.py "$ADT" --audit "$RAM_AUDIT"

# Always rebuild init, so assembly edits cannot be hidden by a stale file.
CLANG="${CLANG:-$HOME/tools/llvm-bin/clang}"
"$CLANG" --target=aarch64-apple-ios16.0 -c tools/sweet-init.S -o "$BUILD/sweet-init.o"
python3 tools/mkinit.py "$BUILD/sweet-init.o" "$BUILD/sweet-init"

case "$PROFILE" in
    rd) ROOT_ARGS='rd=md0' ;;
    ufs-ro) ROOT_ARGS="rd=ufsroot0 ufsroot=$UFS_PARTITION ufsrw=0 rootrw=0" ;;
    ufs-rw) ROOT_ARGS="rd=ufsroot0 ufsroot=$UFS_PARTITION ufsrw=1 rootrw=1 launchd_unsecure_cache=1" ;;
    *) echo 'PROFILE must be rd, ufs-ro, or ufs-rw' >&2; exit 1 ;;
esac
CMDLINE="${CMDLINE:-debug=0xc serial=3 cpus=1 keepsyms=1 -enable_kprintf_spam -noprogress $ROOT_ARGS unentitled_ios_sim_launch=1 ignition_level=1 wdt_test=no_user_checkin wdt=-1 sweetfb_hz=30 startup_debug=0 sweetbeat=5 sweetkeys=0 sweettouch=0 sweetusb=0 sweetlog=0}"
if [[ "$PROFILE" == ufs-* ]]; then
    # Custom command lines must select the same partition as the build profile.
    # Without this argument the inherited kernel silently defaults to userdata.
    read -r -a cmdline_words <<< "$CMDLINE"
    partition_args=0
    for word in "${cmdline_words[@]}"; do
        if [[ "$word" == ufsroot=* ]]; then
            [[ "$word" == "ufsroot=$UFS_PARTITION" ]] || { echo 'CMDLINE ufsroot conflicts with UFS_PARTITION' >&2; exit 1; }
            partition_args=$((partition_args + 1))
        fi
    done
    [[ "$partition_args" -le 1 ]] || { echo 'duplicate ufsroot arguments' >&2; exit 1; }
    if [[ "$partition_args" == 0 ]]; then
        CMDLINE="$CMDLINE ufsroot=$UFS_PARTITION"
    fi
fi
CMDLINE="$CMDLINE timelm_trial=$TRIAL"
python3 tools/mkpayload.py "$KERNEL" "$ADT" "$BUILD/payload.bin" \
    --ramdisk "$BUILD/sweet-init" --cmdline "$CMDLINE"

(cd shim && JOBS="${JOBS:-56}" ./build.sh "$BUILD/payload.bin")
case "$NAME" in
    /*) OUT="$NAME" ;;
    *) OUT="shim/out/$NAME" ;;
esac
mkdir -p "$(dirname "$OUT")"
cp shim/out/shim-boot.img "$OUT"
python3 tools/unpack_bootv2.py "$OUT" "$BUILD/unpacked" >/dev/null
cmp "$BUILD/unpacked/ramdisk" "$BUILD/payload.bin"
python3 tools/inspect_boot.py "$OUT" --kernel "$KERNEL" --dtb dtb/boot_unpacked/dtb \
    --trial "$TRIAL" --manifest "$OUT.json" --ram-audit "$RAM_AUDIT"
sha256sum "$OUT" | tee "$OUT.sha256"
