#!/bin/bash
# Build the SWEET iOS root image without root on the host.
#
#   tools/build-iosroot-vm.sh OS.dmg SYSTEMOS.dmg APPOS.dmg OUT.simg [SIZE]
#
# SIZE defaults to 16G.
#
# The only privileged step of tools/mkiosroot.sh is writing into the HFS+
# volume: mounting hfsplus needs root and every writable userspace HFS+
# driver is read-only.  So the build runs inside a qemu guest, where the
# guest's root does the work:
#
#   host (unprivileged)          qemu guest (root)
#   ---------------------------  -------------------------------------
#   apfs-fuse mounts the IPSW     mounts the HFS+ image read-write
#   tar --numeric-owner archives  extracts the archives as root, so the
#                                 APFS owners (0:80, 501:501, 272:272)
#                                 survive into the volume
#
# The archives are handed over as plain virtio disks, not over 9p: the
# guest's virtio-pci does not bind the modern 0x1111 9p device, so 9p never
# shows up inside the guest.
#
# Requirements on the host (none of them need root):
#   - apfs-fuse, mkfs.hfsplus, fsck.hfsplus, img2simg
#   - qemu-system-x86_64
#   - the guest files (tools/vm/fetch-guest.sh): vmlinuz-lts, initramfs-lts
#
# The guest is Alpine's "lts" kernel: it is the only Alpine flavour that has
# CONFIG_HFSPLUS_FS=m, which the build needs to mount the volume.
set -euo pipefail

[[ $# -ge 4 ]] || { sed -n '2,26p' "$0"; exit 2; }
os=$(realpath "$1")
sysos=$(realpath "$2")
appos=$(realpath "$3")
out=$(realpath -m "$4")
size=${5:-16G}

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
work=${WORK:-$HOME/work/iosroot-build}
guestdir=${GUESTDIR:-$HOME/work/kimg}
qemu=${QEMU:-$(command -v qemu-system-x86_64 || true)}
apfs_fuse=${APFS_FUSE:-$(command -v apfs-fuse || true)}
mkfs_hfsplus=${MKFS_HFSPLUS:-$(command -v mkfs.hfsplus || true)}
fsck_hfsplus=${FSCK_HFSPLUS:-$(command -v fsck.hfsplus || true)}
img2simg=${IMG2SIMG:-$(command -v img2simg || true)}

missing=()
for t in "$qemu" "$apfs_fuse" "$mkfs_hfsplus" "$fsck_hfsplus" "$img2simg"; do
	[[ -n $t && -x $t ]] || missing+=("$t")
done
if [[ ${#missing[@]} -gt 0 ]]; then
	echo "Error: missing host tools: ${missing[*]}" >&2
	echo "  (see tools/README and tools/root-builder/README.md)" >&2
	exit 1
fi

for f in vmlinuz-lts initramfs-lts; do
	[[ -s $guestdir/$f ]] || {
		echo "Error: $guestdir/$f is missing, run tools/vm/fetch-guest.sh" >&2
		exit 1
	}
done

mkdir -p "$work"
img=$work/ios16-root.img
rm -f "$img" "$work"/*.tar

echo "== mounting the IPSW images with apfs-fuse"
for m in os sysos appos; do
	rm -rf "$work/$m" "$work/$m.dmg"
	ln -s "$(eval echo \$$m)" "$work/$m.dmg"
	mkdir -p "$work/$m"
	"$apfs_fuse" "$work/$m.dmg" "$work/$m" &
	fuse_pid=$!
	# wait for the mount to appear
	for _ in $(seq 60); do
		mountpoint -q "$work/$m" && break
		sleep 1
	done
	mountpoint -q "$work/$m" || { echo "apfs-fuse failed for $m" >&2; exit 1; }
	eval "${m}_pid=$fuse_pid"
done

cleanup() {
	for m in os sysos appos; do
		pid=$(eval echo "\${${m}_pid:-}")
		[[ -n $pid ]] && kill "$pid" 2>/dev/null || true
		mountpoint -q "$work/$m" && fusermount -u "$work/$m" 2>/dev/null || true
	done
}
trap cleanup EXIT

echo "== packing the APFS trees into numeric-owner archives"
tar --numeric-owner --xattrs --xattrs-include='*' -C "$work/os/root" -cf "$work/os.tar" .
tar --numeric-owner -C "$work/sysos/root" -cf "$work/sysos.tar" .
tar --numeric-owner -C "$work/appos/root" -cf "$work/appos.tar" .

echo "== creating the HFS+ volume"
truncate -s "$size" "$img"
"$mkfs_hfsplus" -s -J -v iOSRoot "$img"

echo "== packing the guest tooling"
rm -rf "$work/toolsdir"
mkdir -p "$work/toolsdir"
cp "$here/vm/build-in-guest.sh" "$work/toolsdir/"
cp "$here/sweet-root-tweaks.py" "$work/toolsdir/"
tar -C "$work/toolsdir" -cf "$work/tools.tar" .

echo "== running the build inside the qemu guest"
"$here/vm/build-in-vm.sh" \
	--qemu "$qemu" --img "$img" \
	--os-tar "$work/os.tar" --sysos-tar "$work/sysos.tar" \
	--appos-tar "$work/appos.tar" --tools-tar "$work/tools.tar" \
	--guestdir "$guestdir" --work "$work"

echo "== checking the volume"
"$fsck_hfsplus" -fy "$img" || true

echo "== converting to a sparse image"
rm -f "$out"
"$img2simg" "$img" "$out"
ls -l "$out"
echo "done: $out"
