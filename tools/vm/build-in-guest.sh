#!/bin/sh
# Build the iOS root inside the qemu guest (the host has no root).
#
# The guest is the only place that can write into the HFS+ volume: on the
# host every writable HFS+ driver needs root, and mounting hfsplus needs
# root too.  Inside the guest we are root, so the file owners that the APFS
# images carry (0:80, 501:501, 272:272, ...) are restored exactly.
#
# Devices:
#   /dev/vda  the HFS+ image to build (made by mkfs.hfsplus on the host)
#   /dev/vdb  os.tar      - the iOS OS root (tar of the APFS "root" dir)
#   /dev/vdc  sysos.tar   - the SystemOS cryptex
#   /dev/vdd  appos.tar   - the AppOS cryptex
#   /dev/vde  tools.tar   - this script and the tweaks script
#
# The archives are attached as plain virtio disks instead of being shared
# over 9p: the guest's virtio-pci does not bind the modern 0x1111 9p device,
# so 9p never shows up inside the guest.
set -e

ROOT=/mnt/root
TOOLS=/mnt/tools
IMG=${IMG:-/dev/vda}
OS_TAR=${OS_TAR:-/dev/vdb}
SYSOS_TAR=${SYSOS_TAR:-/dev/vdc}
APPOS_TAR=${APPOS_TAR:-/dev/vdd}
TOOLS_TAR=${TOOLS_TAR:-/dev/vde}

log() { echo "=== $* ==="; }

modprobe hfsplus || true
modprobe loop || true

mkdir -p "$ROOT" "$TOOLS"
if [ ! -f "$TOOLS/build-in-guest.sh" ]; then
	# the tools tar is a plain disk image: extract it on the guest's tmpfs
	tar xpf "$TOOLS_TAR" -C "$TOOLS"
fi

log "mounting $IMG"
mount -t hfsplus -o force "$IMG" "$ROOT"
touch "$ROOT/.writable" && rm -f "$ROOT/.writable" || { echo "ERROR: volume is not writable"; exit 1; }

mkdir -p "$ROOT/private/preboot/Cryptexes/OS" "$ROOT/private/preboot/Cryptexes/App"

log "extracting the iOS root"
tar xpf "$OS_TAR" -C "$ROOT"

log "extracting the SystemOS cryptex"
tar xpf "$SYSOS_TAR" -C "$ROOT/private/preboot/Cryptexes/OS"

log "extracting the AppOS cryptex"
tar xpf "$APPOS_TAR" -C "$ROOT/private/preboot/Cryptexes/App"

# Shared cache where dyld looks at ignition level 1: hard links, no extra space.
log "linking the dyld shared cache"
dsc=private/preboot/Cryptexes/OS/System/Library/Caches/com.apple.dyld
mkdir -p "$ROOT/System/Library/Caches/com.apple.dyld"
for f in "$ROOT/$dsc"/*; do
	[ -e "$f" ] || continue
	ln "$f" "$ROOT/System/Library/Caches/com.apple.dyld/${f##*/}" 2>/dev/null || cp "$f" "$ROOT/System/Library/Caches/com.apple.dyld/${f##*/}"
done

# dyld does not search the cryptexes at ignition level 1: hard-link every
# cryptex file that has no counterpart in the OS root to its root path.
log "linking the cryptex files"
for c in OS App; do
	(cd "$ROOT/private/preboot/Cryptexes/$c" && find . ! -type d ! -path './System/Library/Caches/com.apple.dyld/*') |
	while IFS= read -r f; do
		f=${f#./}
		if [ ! -e "$ROOT/$f" ] && [ ! -L "$ROOT/$f" ]; then
			mkdir -p "$ROOT/$(dirname "$f")"
			src="$ROOT/private/preboot/Cryptexes/$c/$f"
			# Linux hfsplus cannot hard-link symlinks: copy those.
			if [ -L "$src" ]; then cp -P "$src" "$ROOT/$f"; else ln "$src" "$ROOT/$f"; fi
		fi
	done
done

# logd's log store: owner _logd (272), mode, plus the .migrated markers.
log "creating the logd directories"
db="$ROOT/private/var/db"
install -d -o 272 -g 272 -m 0751 "$db/diagnostics"
install -d -o 272 -g 272 -m 0755 "$db/uuidtext" "$db/diagnostics/timesync"
for d in diagnostics uuidtext diagnostics/timesync; do
	install -o 272 -g 272 -m 0644 /dev/null "$db/$d/.migrated"
done

# System keybag for the built-in AppleKeyStore (no passcode).
log "writing the system keybag"
install -d -m 0700 -o 0 -g 0 "$ROOT/private/var/keybags"
printf '%s\n' '<?xml version="1.0" encoding="UTF-8"?>' \
	'<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">' \
	'<plist version="1.0"><dict><key>KeyBagKeys</key><data>U1dFRVQ=</data></dict></plist>' |
	install -m 0600 -o 0 -g 0 /dev/stdin "$ROOT/private/var/keybags/systembag.kb"

log "applying the SWEET bring-up tweaks"
python3 "$TOOLS/sweet-root-tweaks.py" "$ROOT"

log "unmounting"
sync
umount "$ROOT"
log "BUILD_DONE"
