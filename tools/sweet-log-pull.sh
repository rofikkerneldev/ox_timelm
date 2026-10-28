#!/bin/sh
# Read the SWEET console log (kernel patch 0047) from a phone booted into
# OrangeFox recovery (fastboot boot recovery/recovery.img; adb root shell).
#   tools/sweet-log-pull.sh [-s SERIAL] OUT.txt
# Only reads: the last 1025 blocks (4 KiB) of the selected partition (default ios).
# Optional: SWEET_LOG_PARTITION, SWEET_ADB_BIN, SWEET_ADB_HOST, SWEET_ADB_PORT.
set -eu

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
	echo "Usage: $(basename "$0") [-s SERIAL] OUT.txt"
	echo "Pull the 4 MiB kernel log ring from SWEET_LOG_PARTITION (default ios)."
	exit 0
fi

SWEET_ADB_BIN="${SWEET_ADB_BIN:-adb}"
SWEET_LOG_PARTITION="${SWEET_LOG_PARTITION:-ios}"
case "$SWEET_LOG_PARTITION" in
    ''|*[!a-zA-Z0-9_-]*) echo 'Invalid SWEET_LOG_PARTITION' >&2; exit 1 ;;
esac
[ "${#SWEET_LOG_PARTITION}" -le 36 ] || { echo 'Partition name too long' >&2; exit 1; }
if [ -n "${SWEET_ADB_PORT:-}" ]; then
    case "$SWEET_ADB_PORT" in *[!0-9]*) echo 'Invalid SWEET_ADB_PORT' >&2; exit 1 ;; esac
    [ "$SWEET_ADB_PORT" -ge 1 ] && [ "$SWEET_ADB_PORT" -le 65535 ] || { echo 'Invalid SWEET_ADB_PORT' >&2; exit 1; }
fi
serial=""
if [ "${1:-}" = "-s" ]; then
	shift
	serial=${1:?Error: -s requires a device serial}
	shift
fi

out=${1:?usage: sweet-log-pull.sh [-s SERIAL] OUT.txt}
command -v "$SWEET_ADB_BIN" >/dev/null 2>&1 || { echo "Error: ADB binary not found" >&2; exit 1; }

adb_cmd() {
    if [ -n "$serial" ]; then set -- -s "$serial" "$@"; fi
    if [ -n "${SWEET_ADB_PORT:-}" ]; then set -- -P "$SWEET_ADB_PORT" "$@"; fi
    if [ -n "${SWEET_ADB_HOST:-}" ]; then set -- -H "$SWEET_ADB_HOST" "$@"; fi
    "$SWEET_ADB_BIN" "$@"
}

dev=$(adb_cmd shell "for d in /dev/block/by-name/$SWEET_LOG_PARTITION /dev/block/bootdevice/by-name/$SWEET_LOG_PARTITION; do [ -e \$d ] && readlink -f \$d && break; done" 2>/dev/null | tr -d '\r')
case "$dev" in
    /dev/block/*) ;;
    *) echo 'Selected partition block device not found' >&2; exit 1 ;;
esac
case "$dev" in *[!a-zA-Z0-9_./-]*) echo 'Invalid block device path' >&2; exit 1 ;; esac
bytes=$(adb_cmd shell blockdev --getsize64 "$dev" | tr -d '\r')
case "$bytes" in ''|*[!0-9]*) echo 'Invalid partition size' >&2; exit 1 ;; esac
[ "$bytes" -ge 4198400 ] && [ "$((bytes % 4096))" -eq 0 ] || { echo 'Partition too small or not 4-KiB aligned' >&2; exit 1; }
skip=$((bytes / 4096 - 1025))
adb_cmd exec-out "dd if=$dev bs=4096 skip=$skip count=1025 2>/dev/null" > "$out.bin"
[ "$(stat -c %s "$out.bin")" -eq 4198400 ] || { echo 'Truncated log area read' >&2; exit 1; }
echo "$dev: $bytes bytes, read blocks $skip.. ($(stat -c %s "$out.bin") bytes)" >&2
python3 "$(dirname "$0")/sweet-log.py" "$out.bin" "$out"
