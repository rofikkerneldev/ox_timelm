#!/bin/bash
# Build the iOS 16.7.16 (20H392) root image for the LG V60 ThinQ 5G (timelm)
# and its sparse fastboot form (.simg).
#
#   sudo ./build-iosroot.sh
#
# Self-contained: the host tools are in ./bin, the scripts next to this file.
# The three IPSW disk images are downloaded from Apple's CDN into the work
# directory (they are skipped if they are already there).
#
# Environment:
#   WORK   work directory              (default $HOME/ios16-root-work)
#   SIZE   size of the HFS+ volume     (default 16G)
#   IPSW   URL of the IPSW              (default iPhone X 16.7.16 20H392)
set -euo pipefail

BASE=$(cd "$(dirname "$0")" && pwd)
export PATH="$BASE/bin:$PATH"

WORK="${WORK:-$HOME/ios16-root-work}"
SIZE="${SIZE:-16G}"
IPSW="${IPSW:-https://updates.cdn-apple.com/2025FallFCS/fullrestores/122-77006/1158E502-D76D-4226-AA06-ED5E30CD1111/iPhone10,3,iPhone10,6_16.7.16_20H392_Restore.ipsw}"
OWNER="${SUDO_USER:-$(id -un)}"

# root is needed to mount the HFS+ volume
if [ "$(id -u)" != "0" ]; then
	exec sudo -E env PATH="$PATH" "$0" "$@"
fi

MEMBERS="$WORK/ipsw-x"
OUTDIR="$WORK/out"
IMG="$OUTDIR/ios16-root-20H392.img"
SIMG="$OUTDIR/ios16-root-20H392.simg"
MNT="$WORK/mnt"

echo "== tool check =="
missing=
for t in apfs-fuse mkfs.hfsplus fsck.hfsplus rsync img2simg python3; do
	command -v "$t" >/dev/null 2>&1 || missing="$missing $t"
done
if [ -n "$missing" ]; then
	echo "missing:$missing" >&2
	echo "  sudo apt install rsync   (the rest is included in ./bin)" >&2
	exit 1
fi

mkdir -p "$MEMBERS" "$OUTDIR" "$MNT"

echo "== 1. IPSW members (iPhone X / D22AP, iOS 16.7.16 20H392) =="
fetch() { # fetch PATTERN FILE
	if [ -s "$2" ]; then
		echo "have $(basename "$2")"
		return
	fi
	echo "fetching $1 ..."
	python3 "$BASE/remotezip.py" "$IPSW" "$1" "$MEMBERS"
}
fetch BuildManifest.plist "$MEMBERS/BuildManifest.plist"
fetch DeviceTree.d22ap.im4p "$MEMBERS/Firmware/all_flash/DeviceTree.d22ap.im4p"
fetch 087-86593-047.dmg "$MEMBERS/087-86593-047.dmg"   # SystemOS (cryptex)
fetch 087-86698-044.dmg "$MEMBERS/087-86698-044.dmg"   # OS (root filesystem)
fetch 087-86715-047.dmg "$MEMBERS/087-86715-047.dmg"   # AppOS (cryptex)

echo "== 2. HFS+ root (case sensitive, journaled) =="
if [ ! -s "$IMG" ]; then
	"$BASE/mkiosroot.sh" "$MEMBERS/087-86698-044.dmg" "$MEMBERS/087-86593-047.dmg" \
		"$MEMBERS/087-86715-047.dmg" "$IMG" "$SIZE"
else
	echo "have $IMG"
fi

echo "== 3. bring-up tweaks (launchd, containers, keybag, Setup skipped, ...) =="
mountpoint -q "$MNT" || mount -t hfsplus -o loop,force "$IMG" "$MNT"
python3 "$BASE/sweet-root-tweaks.py" "$MNT"
umount "$MNT"
fsck.hfsplus -fy "$IMG" || true

echo "== 4. sparse image for fastboot =="
if [ ! -s "$SIMG" ] || [ "$IMG" -nt "$SIMG" ]; then
	img2simg "$IMG" "$SIMG"
fi

chown -R "$OWNER" "$WORK" 2>/dev/null || true

echo "== result =="
ls -la "$IMG" "$SIMG"
sha256sum "$IMG" "$SIMG"
cat <<EOF

flash (on the phone, in fastboot):
  fastboot -S 512M flash userdata $SIMG
  fastboot boot shim-boot-timelm-high-ufs.img
EOF
