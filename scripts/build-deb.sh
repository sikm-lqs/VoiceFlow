#!/bin/bash
# Build a complete .deb package for VoiceFlow
# Includes: Tauri binary, Python sidecar, config, systemd service
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VOICEFLOW_UI="$PROJECT_ROOT/voiceflow-ui"
BUILD_DIR="$PROJECT_ROOT/build/deb-package"
VERSION="0.1.0"
ARCH="amd64"
PKG_NAME="voiceflow_${VERSION}_${ARCH}"

export PNPM_HOME="$HOME/.local/share/pnpm"
export PATH="$PNPM_HOME:$HOME/.cargo/bin:$PATH"

echo "=== VoiceFlow deb builder ==="
echo "Project root: $PROJECT_ROOT"

# Step 1: Build Tauri binary
echo ""
echo "[1/4] Building Tauri binary..."
cd "$VOICEFLOW_UI"
pnpm build
pnpm tauri build

TAURI_BIN="$VOICEFLOW_UI/src-tauri/target/release/voiceflow"
if [ ! -f "$TAURI_BIN" ]; then
    echo "ERROR: Tauri binary not found at $TAURI_BIN"
    exit 1
fi

# Step 2: Prepare deb directory structure
echo ""
echo "[2/4] Preparing deb package structure..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/$PKG_NAME/DEBIAN"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/bin"
mkdir -p "$BUILD_DIR/$PKG_NAME/opt/voiceflow/voiceflow_py"
mkdir -p "$BUILD_DIR/$PKG_NAME/opt/voiceflow/python-libs"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/lib/systemd/user"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/share/icons/hicolor/128x128/apps"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/share/applications"

# Step 3: Install files
echo ""
echo "[3/4] Installing files..."

# Binary
cp "$TAURI_BIN" "$BUILD_DIR/$PKG_NAME/usr/bin/voiceflow"
strip "$BUILD_DIR/$PKG_NAME/usr/bin/voiceflow" 2>/dev/null || true

# Python sidecar
cp "$PROJECT_ROOT/voiceflow_py/daemon.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/voiceflow_py/"
cp "$PROJECT_ROOT/audio_recorder.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"
cp "$PROJECT_ROOT/asr_client.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"
cp "$PROJECT_ROOT/window_detector.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"
cp "$PROJECT_ROOT/utils.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"

# Config (install as config.yaml.example to not overwrite user config)

# Pre-built Python libraries (bundled into deb, no network at install time)
echo "  Bundling Python libraries..."
BUNDLED_LIBS="$BUILD_DIR/$PKG_NAME/opt/voiceflow/python-libs"
if [ ! -d "$BUNDLED_LIBS/sounddevice" ]; then
    /home/sikm/Tool/miniconda3/envs/build-py312/bin/pip install --target="$BUNDLED_LIBS" \
        'python-xlib>=0.33' \
        sounddevice==0.5.1 \
        numpy==2.2.6 \
        requests==2.32.3 \
        pyyaml==6.0.2 \
        'scipy>=1.14' \
        2>/dev/null || {
        echo "WARNING: pip install failed, trying with project venv..."
        "$PROJECT_ROOT/.venv/bin/pip" install --target="$BUNDLED_LIBS" \
            'python-xlib>=0.33' \
            sounddevice==0.5.1 \
            numpy==2.2.6 \
            requests==2.32.3 \
            pyyaml==6.0.2 \
            'scipy>=1.14'
    }
fi
cp "$PROJECT_ROOT/config.yaml" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/config.yaml.example"
if [ ! -f "$BUILD_DIR/$PKG_NAME/opt/voiceflow/config.yaml" ]; then
    cp "$PROJECT_ROOT/config.yaml" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/config.yaml"
fi

# Icons
cp "$VOICEFLOW_UI/src-tauri/icons/128x128.png" "$BUILD_DIR/$PKG_NAME/usr/share/icons/hicolor/128x128/apps/voiceflow.png"
cp "$VOICEFLOW_UI/src-tauri/icons/128x128@2x.png" "$BUILD_DIR/$PKG_NAME/usr/share/icons/hicolor/256x256/apps/voiceflow.png"

# Desktop entry
cat > "$BUILD_DIR/$PKG_NAME/usr/share/applications/voiceflow.desktop" << 'EOF'
[Desktop Entry]
Name=VoiceFlow
Comment=Local Voice Input Assistant
Exec=/usr/bin/voiceflow
Icon=voiceflow
Type=Application
Categories=Utility;
Keywords=voice;input;speech;transcription;
StartupNotify=false
EOF

# Systemd service
cp "$SCRIPT_DIR/packaging/voiceflow.service" "$BUILD_DIR/$PKG_NAME/usr/lib/systemd/user/"

# Step 4: Create control files
echo ""
echo "[4/4] Creating control files..."

# control
cat > "$BUILD_DIR/$PKG_NAME/DEBIAN/control" << EOF
Package: voiceflow
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3, xclip, xdotool, libportaudio2
Maintainer: VoiceFlow <voiceflow@local>
Description: VoiceFlow - Local Voice Input Assistant
 A voice input tool that records speech via hotkey (F10/F11),
 transcribes using local ASR (vLLM), and pastes the result
 at the cursor position. Supports smart terminal detection.
Homepage: https://github.com/cmyandlqs/VoiceFlow
EOF

# postinst
cat > "$BUILD_DIR/$PKG_NAME/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e

VOICEFLOW_DIR="/opt/voiceflow"

# Ensure config.yaml exists (don't overwrite user config on upgrade)
if [ ! -f "$VOICEFLOW_DIR/config.yaml" ]; then
    cp "$VOICEFLOW_DIR/config.yaml.example" "$VOICEFLOW_DIR/config.yaml"
fi

# Reload systemd user units
systemctl --user daemon-reload 2>/dev/null || true

echo "[VoiceFlow] Installation complete!"
echo "[VoiceFlow] To enable auto-start: systemctl --user enable --now voiceflow.service"
echo "[VoiceFlow] To run manually: /usr/bin/voiceflow"
echo "[VoiceFlow] Config file: $VOICEFLOW_DIR/config.yaml"
POSTINST

chmod 755 "$BUILD_DIR/$PKG_NAME/DEBIAN/postinst"

# prerm
cat > "$BUILD_DIR/$PKG_NAME/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
set -e
systemctl --user stop voiceflow.service 2>/dev/null || true
systemctl --user disable voiceflow.service 2>/dev/null || true
PRERM

chmod 755 "$BUILD_DIR/$PKG_NAME/DEBIAN/prerm"

# Build deb
echo ""
echo "Building .deb package..."
dpkg-deb --build "$BUILD_DIR/$PKG_NAME"

DEB_PATH="$BUILD_DIR/$PKG_NAME.deb"
DEB_SIZE=$(du -h "$DEB_PATH" | cut -f1)

echo ""
echo "=== Done! ==="
echo "Package: $DEB_PATH ($DEB_SIZE)"
echo ""
echo "Install with:  sudo dpkg -i $DEB_PATH"
echo "Uninstall with: sudo apt remove voiceflow"
