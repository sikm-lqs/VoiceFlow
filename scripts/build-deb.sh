#!/bin/bash
# Build a complete .deb package for VoiceFlow
# Includes: Tauri binary, Python sidecar, config, systemd service
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VOICEFLOW_UI="$PROJECT_ROOT/voiceflow-ui"
BUILD_DIR="$PROJECT_ROOT/build/deb-package"
VERSION="1.0.0"
ARCH="amd64"
PKG_NAME="voiceflow_${VERSION}_${ARCH}"

export PNPM_HOME="$HOME/.local/share/pnpm"
export PATH="$PNPM_HOME:$HOME/.cargo/bin:$PATH"

PYTHON_BUILD="${VOICEFLOW_PYTHON_BUILD:-/home/sikm/Tool/miniconda3/envs/build-py312/bin/python}"
PYTHON_VERSION="$("$PYTHON_BUILD" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
if [ "$PYTHON_VERSION" != "3.12" ]; then
    echo "ERROR: bundled extensions require Python 3.12, got $PYTHON_VERSION"
    exit 1
fi

echo "=== VoiceFlow deb builder ==="
echo "Project root: $PROJECT_ROOT"

# Step 1: Build Tauri binary
echo ""
echo "[1/4] Building Tauri binary..."
cd "$VOICEFLOW_UI"
pnpm build
# Tauri's own installer omits our Python sidecar. Build only the binary;
# the complete, installable deb is assembled below.
pnpm tauri build --no-bundle

TAURI_BIN="$VOICEFLOW_UI/src-tauri/target/release/voiceflow"
if [ ! -f "$TAURI_BIN" ]; then
    echo "ERROR: Tauri binary not found at $TAURI_BIN"
    exit 1
fi

# Step 2: Prepare deb directory structure
echo ""
echo "[2/4] Preparing deb package structure..."
# Preserve earlier release packages, including the user's rollback package.
rm -rf "$BUILD_DIR/$PKG_NAME"
mkdir -p "$BUILD_DIR/$PKG_NAME/DEBIAN"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/bin"
mkdir -p "$BUILD_DIR/$PKG_NAME/opt/voiceflow/voiceflow_py"
mkdir -p "$BUILD_DIR/$PKG_NAME/opt/voiceflow/python-libs"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/lib/systemd/user"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/share/icons/hicolor/128x128/apps"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$BUILD_DIR/$PKG_NAME/usr/share/applications"
mkdir -p "$BUILD_DIR/$PKG_NAME/etc/xdg/autostart"

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
cp "$PROJECT_ROOT/text_cleanup.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"
cp "$PROJECT_ROOT/minimax_cleanup.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"
cp "$PROJECT_ROOT/runtime_status.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"
cp "$PROJECT_ROOT/voiceflow_session.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"
cp "$PROJECT_ROOT/install_lifecycle.py" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/"

# Config (install as config.yaml.example to not overwrite user config)

# Pre-built Python libraries (bundled into deb, no network at install time)
echo "  Bundling Python libraries..."
BUNDLED_LIBS="$BUILD_DIR/$PKG_NAME/opt/voiceflow/python-libs"
    "$PYTHON_BUILD" -m pip install --target="$BUNDLED_LIBS" \
        'python-xlib>=0.33' \
        sounddevice==0.5.1 \
        numpy==2.2.6 \
        requests==2.32.3 \
        pyyaml==6.0.2 \
        'scipy>=1.14'
# Ship only defaults. The lifecycle migrates legacy personal configuration to
# /etc/voiceflow/config.yaml, which is never overwritten by dpkg.
cp "$PROJECT_ROOT/defaults.yaml" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/defaults.yaml"
cp "$PROJECT_ROOT/defaults.yaml" "$BUILD_DIR/$PKG_NAME/opt/voiceflow/config.yaml.example"

cat > "$BUILD_DIR/$PKG_NAME/usr/bin/voiceflow-session" << 'SESSION'
#!/bin/sh
exec /usr/bin/python3 /opt/voiceflow/voiceflow_session.py "$@"
SESSION
chmod 755 "$BUILD_DIR/$PKG_NAME/usr/bin/voiceflow-session"

# Icons
cp "$VOICEFLOW_UI/src-tauri/icons/128x128.png" "$BUILD_DIR/$PKG_NAME/usr/share/icons/hicolor/128x128/apps/voiceflow.png"
cp "$VOICEFLOW_UI/src-tauri/icons/128x128@2x.png" "$BUILD_DIR/$PKG_NAME/usr/share/icons/hicolor/256x256/apps/voiceflow.png"

# Desktop entry
cat > "$BUILD_DIR/$PKG_NAME/usr/share/applications/voiceflow.desktop" << 'EOF'
[Desktop Entry]
Name=VoiceFlow
Comment=Local Voice Input Assistant
Exec=/usr/bin/voiceflow-session --launch
Icon=voiceflow
Type=Application
Categories=Utility;
Keywords=voice;input;speech;transcription;
StartupNotify=false
EOF

cat > "$BUILD_DIR/$PKG_NAME/etc/xdg/autostart/voiceflow-session.desktop" << 'AUTOSTART'
[Desktop Entry]
Type=Application
Name=VoiceFlow Session Setup
Exec=/usr/bin/voiceflow-session --login
TryExec=/usr/bin/voiceflow-session
NoDisplay=true
X-GNOME-Autostart-enabled=true
AUTOSTART

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
Pre-Depends: python3 (>= 3.12), python3 (<< 3.13)
Depends: xclip, xdotool, libportaudio2, libgtk-3-0t64, libwebkit2gtk-4.1-0, libayatana-appindicator3-1
Maintainer: VoiceFlow <voiceflow@local>
Description: VoiceFlow - Local Voice Input Assistant
 A voice input tool that records speech via hotkey (F10/F11),
 transcribes using local ASR (vLLM), and pastes the result
 at the cursor position. Supports smart terminal detection.
Homepage: https://github.com/sikm-lqs/VoiceFlow
EOF

for phase in preinst postinst prerm postrm; do
    cp "$PROJECT_ROOT/install_lifecycle.py" "$BUILD_DIR/$PKG_NAME/DEBIAN/$phase"
    chmod 755 "$BUILD_DIR/$PKG_NAME/DEBIAN/$phase"
done

# Build deb
echo ""
echo "Building .deb package..."
dpkg-deb --root-owner-group --build "$BUILD_DIR/$PKG_NAME"

DEB_PATH="$BUILD_DIR/$PKG_NAME.deb"
DEB_SIZE=$(du -h "$DEB_PATH" | cut -f1)

echo ""
echo "=== Done! ==="
echo "Package: $DEB_PATH ($DEB_SIZE)"
echo ""
echo "Install with:  sudo dpkg -i $DEB_PATH"
echo "Uninstall with: sudo apt remove voiceflow"
