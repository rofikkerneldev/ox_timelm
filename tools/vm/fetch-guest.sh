#!/bin/bash
# Fetch the Alpine "lts" netboot files used by the qemu guest.
#
#   tools/vm/fetch-guest.sh [DEST]
#
# DEST defaults to $HOME/work/kimg.
#
# The guest boots the lts kernel because it is the only Alpine flavour with
# CONFIG_HFSPLUS_FS=m; the *virt* flavour has no HFS+ support at all, so the
# guest could not mount the volume it has to build.
#
# The modloop carries the kernel modules (hfsplus, fuse, ...).  It is 290 MB,
# so the build downloads it once and caches it here.
set -euo pipefail

dest=${1:-$HOME/work/kimg}
base=https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/netboot

mkdir -p "$dest"
for f in vmlinuz-lts initramfs-lts modloop-lts; do
	if [[ -s $dest/$f ]]; then
		echo "have $dest/$f"
		continue
	fi
	echo "GET $base/$f"
	curl -fL --retry 3 -o "$dest/$f" "$base/$f"
done
ls -l "$dest"
