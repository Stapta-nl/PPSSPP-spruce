#!/bin/bash
set -e

PPSSPP_VERSION="${PPSSPP_VERSION:-save-on-exit-v1.20.3}"
# PPSSPP v1.20.3 plus the "Save state on exit" setting (Stapta-nl/ppsspp#2).
PPSSPP_REPO="${PPSSPP_REPO:-https://github.com/Stapta-nl/ppsspp.git}"
OUTPUT_DIR="${OUTPUT_DIR:-/output}"

echo "=== Building PPSSPP ${PPSSPP_VERSION} for Anbernic H700 (Allwinner H700 / Cortex-A53 / Mali-G31) ==="

# This target is for the Anbernic RG XX line running under BaseOS
# (github.com/pvaibhav/BaseOS). It reuses the universal aarch64 Ubuntu 20.04
# toolchain (glibc 2.31, forward-compatible with BaseOS 2.35): the Mesa
# GLES2/EGL and SDL2 packages are linked by soname, and at runtime on the device
# resolve to the H700 mali blob and the mali-fbdev SDL2 spruce ships. The build
# adds Cortex-A53 tuning and the handheld cmake flags MustardOS uses for the same
# SoC (ARM_NO_VULKAN / MOBILE_DEVICE / ATLAS_TOOL=OFF).
#
# Reference: MustardOS/tool/build_ppsspp.sh (h700 case). muOS builds against a
# crosstool-NG sysroot that carries the mali blob and force-links it plus
# SDL2_ttf 2.20.2; we do not need that here because dynamic soname linking
# defers GLES/EGL/SDL2 to the device. If fonts or GL init ever misbehave, the
# muOS-faithful path (build SDL2_ttf, force-link libmali) is the fallback.

# Clone PPSSPP with submodules
if [ ! -d "ppsspp" ]; then
    git clone --depth 1 --branch "$PPSSPP_VERSION" \
        --recurse-submodules --shallow-submodules \
        "$PPSSPP_REPO"
fi

cd ppsspp

# Apply common patches (includes the gameswitcher / menu-button integration)
echo "=== Applying patches ==="
for patch in /patches/common/*.py; do
    [ -f "$patch" ] && python3 "$patch" && echo "Applied: $(basename $patch)"
done

# Apply h700-specific patches
for patch in /patches/h700/*.py; do
    [ -f "$patch" ] && python3 "$patch" && echo "Applied: $(basename $patch)"
done

mkdir -p build && cd build

# Cross-compilation environment
export CCACHE_DIR="${CCACHE_DIR:-/ccache}"

# Configure for H700: SDL2 + GLES2 + EGL + fbdev, no Vulkan/X11/Wayland.
# Cortex-A53 tuning and the handheld flags match MustardOS's proven h700 build.
cmake .. \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE=/tmp/aarch64-toolchain.cmake \
    -DCMAKE_C_COMPILER_LAUNCHER=ccache \
    -DCMAKE_CXX_COMPILER_LAUNCHER=ccache \
    -DCMAKE_C_FLAGS="-O3 -mcpu=cortex-a53 -mtune=cortex-a53 -ffunction-sections -fdata-sections -fomit-frame-pointer -flto=auto -Wno-error" \
    -DCMAKE_CXX_FLAGS="-O3 -mcpu=cortex-a53 -mtune=cortex-a53 -ffunction-sections -fdata-sections -fomit-frame-pointer -flto=auto -Wno-error" \
    -DCMAKE_EXE_LINKER_FLAGS="-Wl,--copy-dt-needed-entries,--gc-sections -flto=auto" \
    -DUSING_GLES2=ON \
    -DUSING_EGL=ON \
    -DUSING_FBDEV=ON \
    -DVULKAN=OFF \
    -DARM_NO_VULKAN=ON \
    -DUSING_X11_VULKAN=OFF \
    -DUSE_WAYLAND_WSI=OFF \
    -DMOBILE_DEVICE=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DUSE_SYSTEM_LIBPNG=OFF \
    -DUSE_SYSTEM_FFMPEG=OFF \
    -DUSE_DISCORD=OFF \
    -DUSE_MINIUPNPC=OFF \
    -DHEADLESS=OFF \
    -DUNITTEST=OFF \
    -DATLAS_TOOL=OFF \
    -DCMAKE_DISABLE_FIND_PACKAGE_SDL2_ttf=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_Fontconfig=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_X11=ON

# Build
make -j$(nproc) PPSSPPSDL

# Output
mkdir -p "$OUTPUT_DIR"
cp PPSSPPSDL "$OUTPUT_DIR/PPSSPPSDL_h700"
aarch64-linux-gnu-strip -s "$OUTPUT_DIR/PPSSPPSDL_h700"

# Copy assets (required at runtime, found relative to the binary)
cp -r ../assets "$OUTPUT_DIR/assets"

echo "=== Build complete: ${OUTPUT_DIR}/PPSSPPSDL_h700 ==="
