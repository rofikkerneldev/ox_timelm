#!/bin/bash
# Build the iOS 16 root image (HFS+) for the LG V60 (timelm) and its sparse
# fastboot form.
#
#   sudo tools/build-iosroot.sh [workdir]
#
# The root is the unmodified iPhone X (D22AP) iOS 16.7.16 (20H392) userland.
# It is device independent: only the ADT, the boot image and the kernel
# command line are timelm specific.
#
# Steps
#   1. fetch the IPSW members (only the three disk images + the device tree)
#   2. tools/mkiosroot.sh    -> one case-sensitive, journaled HFS+ volume
#      (iOS rootfs + SystemOS/AppOS cryptexes, shared cache hard links)
#   3. tools/sweet-root-tweaks.py -> the bring-up changes that get iOS to the
#      home screen (launchd.plist, containers, keybag, Setup skipped, ...)
#   4. img2simg -> .simg (a raw 16 GB flash is not possible: fastboot needs
#      a sparse image and the host runs out of memory otherwise)
#
# Host tools (no root needed to build them, see tools/README-ROOT-TOOLS.md):
#   apfs-fuse  mkfs.hfsplus  fsck.hfsplus  rsync  img2simg
set -euo pipefail

# Re-exec as root: the HFS+ volume has to be mounted.
if [ "$(id -u)" != "0" ]; then
	exec sudo -E env PATH="$PATH" "$0" "$@"
fi
OWNER="${SUDO_USER:-$(id -un)}"
export PATH="$HOME/tools/bin:$PATH"

cd "$(dirname "$0")/.."
REPO=$(pwd)

IPSW_URL="https://updates.cdn-apple.com/2025FallFCS/fullrestores/122-77006/1158E502-D76D-4226-AA06-ED5E30CD1111/iPhone10,3,iPhone10,6_16.7.16_20H392_Restore.ipsw"
WORK=research/ipsw-x
OUTDIR=research/iosroot
IMG=$OUTDIR/ios16-root-20H392.img
SIMG=$OUTDIR/ios16-root-20H392.simg
SIZE="${SIZE:-16G}"
MNT=/mnt/iosroot

echo "== tool check =="
missing=
for t in apfs-fuse mkfs.hfsplus fsck.hfsplus rsync img2simg python3; do
	command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
done
if [ -n "$missing" ]; then
	echo "missing:$missing" >&2
	echo "  sudo apt install apfs-fuse hfsprogs rsync" >&2
	echo "  (img2simg: android-sdk-libsparse-utils, or build it from AOSP)" >&2
	exit 1
fi

mkdir -p "$WORK" "$OUTDIR" "$MNT"

echo "== 1. IPSW members =="
fetch() { # fetch PATTERN DESTNAME
	if [ -s "$2" ]; then
		echo "have $2"
		return
	fi
	echo "fetching $1"
	python3 tools/remotezip.py "$IPSW_URL" "$1" "$WORK"
}
fetch BuildManifest.plist "$WORK/BuildManifest.plist"
fetch DeviceTree.d22ap.im4p "$WORK/Firmware/all_flash/DeviceTree.d22ap.im4p"
if [ ! -s "$WORK/d22ap.adt" ]; then
	ipsw img4 im4p extract "$WORK/Firmware/all_flash/DeviceTree.d22ap.im4p" -o "$WORK/d22ap.adt"
fi
fetch 087-86593-047.dmg "$WORK/087-86593-047.dmg"   # SystemOS
fetch 087-86698-044.dmg "$WORK/087-86698-044.dmg"   # OS
fetch 087-86715-047.dmg "$WORK/087-86715-047.dmg"   # AppOS

echo "== 2. HFS+ root =="
if [ ! -s "$IMG" ]; then
	tools/mkiosroot.sh "$WORK/087-86698-044.dmg" "$WORK/087-86593-047.dmg" \
		"$WORK/087-86715-047.dmg" "$IMG" "$SIZE"
else
	echo "have $IMG"
fi

echo "== 3. bring-up tweaks =="
mountpoint -q "$MNT" || mount -t hfsplus -o loop,force "$IMG" "$MNT"
python3 tools/sweet-root-tweaks.py "$MNT"
umount "$MNT"
fsck.hfsplus -fy "$IMG" || true

echo "== 4. sparse image =="
if [ ! -s "$SIMG" ] || [ "$IMG" -nt "$SIMG" ]; then
	img2simg "$IMG" "$SIMG"
fi

chown -R "$OWNER" research/ 2>/dev/null || true
echo "== result =="
ls -la "$IMG" "$SIMG"
sha256sum "$IMG" "$SIMG"
echo
echo "flash: fastboot -S 512M flash userdata $REPO/$SIMG"
echo "boot : fastboot boot shim/out/shim-boot-timelm-high-ufs.img"
