#!/bin/bash
# Check patches/common/sigterm-autoresume-save.py against a fresh PPSSPP checkout.
#
#   git clone --depth 1 --branch v1.20.3 --recurse-submodules --shallow-submodules \
#       https://github.com/hrydgard/ppsspp.git /tmp/ppsspp-test
#   tools/test-autoresume-patch.sh /tmp/ppsspp-test
#
# The checkout must be unmodified (the script refuses a dirty SDL/SDLMain.cpp).
# It applies all common patches the way build-pvr.sh does, then checks the result.
# It only checks that the patches apply and what it contains; it does not compile.
set -eu

CHECKOUT="${1:?usage: $0 <fresh ppsspp checkout>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PATCH="$HERE/patches/common/sigterm-autoresume-save.py"
SRC=SDL/SDLMain.cpp

fail() { echo "FAIL: $*" >&2; exit 1; }
has() { grep -qF -- "$1" "$CHECKOUT/$SRC" || fail "$SRC lacks: $1"; echo "ok: $1"; }

cd "$CHECKOUT"
[ -f "$SRC" ] || fail "$CHECKOUT is not a PPSSPP checkout"
git diff --quiet -- "$SRC" || fail "$SRC is already modified; use a fresh checkout"
grep -qF PPSSPP_AUTORESUME_STATE "$SRC" && fail "$SRC already contains the patch"

echo "== apply common patches in build-pvr.sh order (alphabetical, same skips)"
for p in "$HERE"/patches/common/*.py; do
    case "$(basename "$p")" in
        fullscreen.py|skip-vulkan-probe.py) continue ;;
    esac
    python3 -I "$p" >/dev/null || fail "$(basename "$p") does not apply"
    echo "ok: $(basename "$p")"
done

echo "== anchors"
has 'PPSSPP_AUTORESUME_STATE'
has '#include "Core/SaveState.h"'
has 'SaveState::Save(g_autoResumePath.WithExtraExtension(".tmp")'
has 'WithExtraExtension(".done")'
has 'WithExtraExtension(".fail")'
has 'if (!StartAutoResumeSave())'
[ "$(grep -c '^\s*PollAutoResumeSave();' "$SRC")" = 2 ] || fail "expected PollAutoResumeSave() in both main loops"
echo "ok: PollAutoResumeSave() in both main loops"

echo "== second run is a no-op"
cp "$SRC" /tmp/.autoresume-before.$$
python3 -I "$PATCH"
cmp "$SRC" /tmp/.autoresume-before.$$ || fail "second run changed the file"
rm -f /tmp/.autoresume-before.$$

echo "PASS"
