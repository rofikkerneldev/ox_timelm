#!/bin/bash
# Assemble the standalone "ios16-root-builder" package: the scripts and the
# host tools needed to build the iOS 16 root image (.img + .simg) on another
# Linux box (that box needs root for the HFS+ mount, nothing else).
#
#   tools/make-root-builder-pkg.sh [outdir]
#
# The host tools come from ~/tools (built without root by
# ~/tools/src/build-root-tools.sh); the sources are copied too, so the tools
# can be rebuilt on a box with a different glibc.
set -euo pipefail
cd "$(dirname "$0")/.."

OUT="${1:-research/pkg}"
NAME=ios16-root-builder
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

mkdir -p "$STAGE/$NAME/bin" "$STAGE/$NAME/src"
cp tools/mkiosroot.sh tools/sweet-root-tweaks.py tools/remotezip.py "$STAGE/$NAME/"
cp tools/root-builder/build-iosroot.sh tools/root-builder/README.md "$STAGE/$NAME/"
chmod +x "$STAGE/$NAME/build-iosroot.sh" "$STAGE/$NAME/mkiosroot.sh"

for t in apfs-fuse mkfs.hfsplus fsck.hfsplus img2simg fusermount; do
	[ -x "$HOME/tools/bin/$t" ] || { echo "missing $HOME/tools/bin/$t (run ~/tools/src/build-root-tools.sh)" >&2; exit 1; }
	cp "$HOME/tools/bin/$t" "$STAGE/$NAME/bin/"
done

# sources: rebuild the tools without root on the target box
cp "$HOME/tools/src/build-root-tools.sh" "$STAGE/$NAME/src/"
for s in fuse-2.9.9.tar.gz bzip2-1.0.8.tar.gz hfsprogs.tar.gz; do
	[ -f "$HOME/tools/src/$s" ] && cp "$HOME/tools/src/$s" "$STAGE/$NAME/src/" || true
done
if [ -d "$HOME/tools/src/apfs-fuse" ]; then
	tar czf "$STAGE/$NAME/src/apfs-fuse-src.tar.gz" -C "$HOME/tools/src/apfs-fuse" --exclude=.git --exclude=build .
fi

mkdir -p "$OUT"
tar -cf - -C "$STAGE" "$NAME" | zstd -19 -f -T0 --long=27 -o "$OUT/$NAME-20H392.tar.zst"
ls -la "$OUT/$NAME-20H392.tar.zst"
sha256sum "$OUT/$NAME-20H392.tar.zst"
echo
echo "upload: ~/bin/gofile-upload $OUT/$NAME-20H392.tar.zst $NAME-20H392.tar.zst"
