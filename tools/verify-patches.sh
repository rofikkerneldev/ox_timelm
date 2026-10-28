#!/bin/bash
# Verify that every repo patch applies cleanly on top of upstream xnu-8796.141.3.
#
#   tools/verify-patches.sh [PATCH_DIR]
#
# Stages Apple's open source HFS (as xnu/patches/0026-stage-hfs.sh does in CI),
# then applies every patch in order with git apply. Exits non-zero on the first
# failure.
set -euo pipefail
cd "$(dirname "$0")/.."

PATCH_DIR="${1:-xnu/patches}"
SRC="${SRC:-$HOME/work/xnu-src/xnu-xnu-8796.141.3}"
DST="${DST:-$HOME/work/xnu-verify}"

[[ -d "$SRC" ]] || { echo "missing upstream source $SRC" >&2; exit 1; }

echo "staging tree in $DST"
rm -rf "$DST"
cp -a "$SRC" "$DST"
cd "$DST"

if [[ ! -d bsd/sweet_hfs ]]; then
	tmp=$(mktemp -d)
	trap 'rm -rf "$tmp"' EXIT
	git init -q "$tmp"
	git -C "$tmp" fetch -q --depth 1 \
	    https://github.com/apple-oss-distributions/hfs \
	    31d5ec238dfab485e78a9a30b8b92d13e8914f03
	git -C "$tmp" checkout -q FETCH_HEAD
	python3 - "$tmp" <<'PY'
from pathlib import Path
import shutil, sys
source = Path(sys.argv[1]); target = Path('bsd/sweet_hfs')
for directory in ('core', 'hfs_encodings'):
    shutil.copytree(source / directory, target / directory)
path = target / 'core' / 'hfs_iokit.h'
text = path.read_text()
include = '#include <AppleKeyStore/AppleKeyStoreFSServices.h>\n'
first = 'int hfs_unwrap_key('
end = '__END_DECLS'
text = text.replace(include, '#if CONFIG_PROTECT\n' + include + '#endif\n')
i, j = text.index(first), text.index(end)
text = text[:i] + '#if CONFIG_PROTECT\n' + text[i:j].rstrip('\n') + '\n#endif\n\n' + text[j:]
path.write_text(text)
PY
	rm -rf "$tmp"
	trap - EXIT
fi

repo=$(cd "$OLDPWD" && pwd)
n=0
for p in "$repo/$PATCH_DIR"/*.patch; do
	if git apply --check "$p" 2>/dev/null; then
		git apply "$p"
		n=$((n + 1))
	else
		echo "FAILED: $p"
		git apply --check "$p" 2>&1 | head -10
		exit 1
	fi
done
echo "OK: $n patches applied cleanly"
