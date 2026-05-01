#!/usr/bin/env bash
# Build script for Linux deb + rpm + AppImage
set -e

VERSION="${1:-1.0.0}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="$ROOT/dist"
APP_NAME="ghost-jarvis"

echo "=== Ghost Jarvis Linux Build ==="
echo "Version: $VERSION"
echo "Root:    $ROOT"

# 1. PyInstaller bundle
echo ""
echo "[1/5] Running PyInstaller..."
pyinstaller "$ROOT/build_scripts/common/ghost_jarvis.spec" --clean --noconfirm

BUNDLE_DIR="$OUTPUT_DIR/GhostJarvis"

# 2. Create .desktop file
echo ""
echo "[2/5] Creating .desktop entry..."
mkdir -p "$BUNDLE_DIR/usr/share/applications"
cat > "$BUNDLE_DIR/usr/share/applications/ghost-jarvis.desktop" <<EOF
[Desktop Entry]
Name=Ghost Jarvis
Comment=Voice interface for AI agents
Exec=/opt/$APP_NAME/GhostJarvis
Icon=/opt/$APP_NAME/assets/icon.png
Type=Application
Categories=AudioVideo;Audio;Utility;
Terminal=false
EOF

# 3. Build deb
echo ""
echo "[3/5] Building deb..."
fpm -s dir -t deb \
    -n "$APP_NAME" -v "$VERSION" \
    --prefix /opt \
    --description "Voice interface for AI agents" \
    --url "https://github.com/user/ghost-jarvis" \
    --maintainer "Ghost Labs <ghost@example.com>" \
    --depends portaudio19-2 \
    --depends libgl1 \
    -C "$BUNDLE_DIR" \
    "$OUTPUT_DIR/${APP_NAME}_${VERSION}_amd64.deb"

# 4. Build rpm
echo ""
echo "[4/5] Building rpm..."
fpm -s dir -t rpm \
    -n "$APP_NAME" -v "$VERSION" \
    --prefix /opt \
    --description "Voice interface for AI agents" \
    --url "https://github.com/user/ghost-jarvis" \
    --maintainer "Ghost Labs <ghost@example.com>" \
    -C "$BUNDLE_DIR" \
    "$OUTPUT_DIR/${APP_NAME}-${VERSION}-1.x86_64.rpm"

# 5. Build AppImage (optional, requires appimagetool)
if command -v appimagetool &> /dev/null; then
    echo ""
    echo "[5/5] Building AppImage..."
    # AppImage build steps here
    echo "AppImage build not yet implemented in this script."
else
    echo "[5/5] Skipping AppImage (appimagetool not found)."
fi

echo ""
echo "Done. Packages in $OUTPUT_DIR"
