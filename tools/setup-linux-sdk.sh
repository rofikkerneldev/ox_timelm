#!/usr/bin/env bash
set -euo pipefail
: "${THEOS:?Set THEOS to a local toolchain directory}"
mkdir -p "$THEOS/toolchain" "$THEOS/sdks"
fetch_unpack() {
    local url=$1 hash=$2 dest=$3 marker=$4
    [ -e "$marker" ] && return
    local tmp
    tmp=$(mktemp -d "${TMPDIR:-/tmp}/timelm-sdk.XXXXXX")
    curl -fsSL --retry 3 "$url" -o "$tmp/archive.tar.xz"
    printf '%s  %s\n' "$hash" "$tmp/archive.tar.xz" | sha256sum -c -
    tar -xJf "$tmp/archive.tar.xz" -C "$dest"
    rm -rf "$tmp"
    [ -e "$marker" ]
}
fetch_unpack https://github.com/L1ghtmann/llvm-project/releases/download/test-210562a/iOSToolchain-x86_64.tar.xz \
    a72a7a577e2fbe2838b6b5e9c72034fa7d114af96f0e1d4b016f18730ce4056e \
    "$THEOS/toolchain" "$THEOS/toolchain/linux/iphone/bin/ld"
fetch_unpack https://github.com/theos/sdks/releases/download/master-146e41f/iPhoneOS16.5.sdk.tar.xz \
    5e0fd3f01266cce4ce012d4a99b38eb56578fca40d09edc81cd83dee958202fb \
    "$THEOS/sdks" "$THEOS/sdks/iPhoneOS16.5.sdk/usr/include"
