#!/bin/bash
# Check patches/common/sigterm-autoresume-save.py against a fresh PPSSPP checkout.
#
#   git clone --depth 1 --branch v1.20.3 --recurse-submodules --shallow-submodules \
#       https://github.com/hrydgard/ppsspp.git /tmp/ppsspp-test
#   tools/test-autoresume-patch.sh /tmp/ppsspp-test
#
# The checkout must be unmodified (the script refuses a dirty SDL/SDLMain.cpp).
# Steps: apply the common patches as build-pvr.sh does, check the result and that
# a second run is a no-op and a half-patched file is refused, compile the
# SDLMain.cpp translation unit if SDL2 dev files, cmake and ninja are installed
# (skipped with a notice otherwise), then apply the PVR patches that follow in
# build-pvr.sh. Running the game is not part of this script.
set -eu

CHECKOUT="${1:?usage: $0 <fresh ppsspp checkout>}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PATCH="$HERE/patches/common/sigterm-autoresume-save.py"
SRC=SDL/SDLMain.cpp
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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
has 'PPSSPP_AUTORESUME_TIMEOUT_MS'
has '#include "Core/SaveState.h"'
has 'SaveState::Save(tmp, -1,'
has 'WithExtraExtension(".done")'
has 'WithExtraExtension(".fail")'
has 'NetworkAllowSaveState()'
has 'Achievements::HardcoreModeActive()'
has 'if (!StartAutoResumeSave())'
n=$(grep -c '^[[:space:]]*PollAutoResumeSave();' "$SRC" || true)
[ "$n" = 3 ] || fail "expected 3 PollAutoResumeSave() calls (Vulkan loop + two in the GL loop), found $n"
echo "ok: PollAutoResumeSave() in both main loops ($n calls)"

echo "== second run is a no-op"
cp "$SRC" "$TMP/before"
python3 -I "$PATCH"
cmp "$SRC" "$TMP/before" || fail "second run changed the file"

echo "== half-patched file is refused"
grep -v '^[[:space:]]*PollAutoResumeSave();$' "$TMP/before" > "$SRC"
if python3 -I "$PATCH" 2>"$TMP/err"; then fail "half-patched file was accepted"; fi
grep -q 'half-patched' "$TMP/err" || fail "no clear message for a half-patched file: $(cat "$TMP/err")"
echo "ok: $(cat "$TMP/err")"
cp "$TMP/before" "$SRC"

echo "== compile SDLMain.cpp"
if pkg-config --exists sdl2 2>/dev/null && command -v cmake >/dev/null && command -v ninja >/dev/null && [ -d ext/glslang/glslang ] && [ -n "$(ls -A ext/glslang 2>/dev/null)" ]; then
    cmake -S . -B "$TMP/build" -G Ninja -DCMAKE_BUILD_TYPE=Release -DHEADLESS=ON -DUNITTEST=OFF >"$TMP/cmake.log" 2>&1 \
        || { tail -20 "$TMP/cmake.log"; fail "cmake configure failed"; }
    ninja -C "$TMP/build" CMakeFiles/native.dir/SDL/SDLMain.cpp.o >"$TMP/ninja.log" 2>&1 \
        || { tail -30 "$TMP/ninja.log"; fail "SDLMain.cpp does not compile"; }
    echo "ok: SDLMain.cpp compiles"
else
    echo "SKIPPED: needs libsdl2-dev, cmake, ninja and a checkout with submodules"
fi

echo "== PVR patches after it (build-pvr.sh order and skips)"
for p in "$HERE"/patches/pvr/*.py; do
    case "$(basename "$p")" in
        revert-fullscreen.py|debug-gl-init.py|glfinish-before-swap.py) continue ;;
    esac
    python3 -I "$p" >/dev/null || fail "pvr/$(basename "$p") does not apply"
    echo "ok: pvr/$(basename "$p")"
done
has 'PPSSPP_AUTORESUME_STATE'

echo "PASS"
