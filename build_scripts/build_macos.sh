#!/usr/bin/env bash
# Build script for macOS PKG installer
set -e

VERSION="${1:-1.0.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="$ROOT/dist"
APP_NAME="GhostJarvis"

echo "=== Ghost Jarvis macOS Build ==="
echo "Version: $VERSION"
echo "Root:    $ROOT"

# 1. PyInstaller bundle
echo ""
echo "[1/4] Running PyInstaller..."
pyinstaller "$ROOT/build_scripts/common/ghost_jarvis.spec" --clean --noconfirm

# 2. Copy model if present
if [ -d "$ROOT/models" ]; then
    echo "[2/4] Copying Whisper model..."
    cp -R "$ROOT/models" "$OUTPUT_DIR/$APP_NAME.app/Contents/Resources/"
fi

# 3. Sign the app bundle (optional, requires Apple Developer cert)
# codesign --deep --force --verify --verbose --sign "Developer ID" "$OUTPUT_DIR/$APP_NAME.app"

# 4. Build PKG
echo ""
echo "[3/4] Building PKG..."
pkgbuild --component "$OUTPUT_DIR/$APP_NAME.app" \
         --install-location /Applications \
         "$OUTPUT_DIR/${APP_NAME}-${VERSION}.pkg"

echo ""
echo "[4/4] Done. PKG: $OUTPUT_DIR/${APP_NAME}-${VERSION}.pkg"
