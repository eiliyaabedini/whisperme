#!/bin/bash
# Build dist/WhisperMe.app — a menu-bar app bundle that launches this checkout
# via uv. Giving WhisperMe its own bundle means macOS attributes Microphone /
# Accessibility permissions to "WhisperMe" instead of your terminal.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="WhisperMe"
BUNDLE_ID="com.eiliya.whisperme"
VERSION="$(awk -F'"' '/^version/{print $2; exit}' "$REPO_DIR/pyproject.toml")"
DIST_DIR="$REPO_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"

# Locate uv now and bake the path in, so Finder/login-item launches don't
# depend on the user's shell PATH.
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" ]]; then
    for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
        if [[ -x "$candidate" ]]; then
            UV_BIN="$candidate"
            break
        fi
    done
fi
if [[ -z "$UV_BIN" ]]; then
    echo "error: uv not found — install it from https://docs.astral.sh/uv/" >&2
    exit 1
fi

echo "==> Building $APP_NAME.app"
echo "    repo: $REPO_DIR"
echo "    uv:   $UV_BIN"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources"

cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>
    <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>
    <string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>CFBundleExecutable</key>
    <string>whisperme</string>
    <key>CFBundleIconFile</key>
    <string>$APP_NAME</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>WhisperMe listens to your microphone only while you dictate (Option+/).</string>
</dict>
</plist>
PLIST

# Compile the native launcher stub. It stays resident as the bundle's main
# process (spawning uv as a child), which is what makes macOS attribute
# Microphone/Accessibility permissions to "WhisperMe" — a script that exec'd
# uv would hand the permission identity to uv instead, and grants to
# WhisperMe would never match (permission prompts loop forever).
CC="${CC:-cc}"
if ! command -v "$CC" >/dev/null; then
    echo "error: no C compiler found — install Xcode Command Line Tools (xcode-select --install)" >&2
    exit 1
fi
echo "==> Compiling launcher stub"
"$CC" -O2 -Wall \
    -DUV_PATH="\"$UV_BIN\"" \
    -DREPO_DIR="\"$REPO_DIR\"" \
    -o "$APP_DIR/Contents/MacOS/whisperme" \
    "$REPO_DIR/scripts/launcher.c"

echo "==> Generating icon"
if ! "$UV_BIN" run --directory "$REPO_DIR" python "$REPO_DIR/scripts/make_icon.py" \
        "$APP_DIR/Contents/Resources/$APP_NAME.icns"; then
    echo "warning: icon generation failed; the app will use the generic icon" >&2
fi

echo "==> Signing (ad-hoc)"
if ! codesign --force --sign - "$APP_DIR" 2>/dev/null; then
    echo "warning: ad-hoc codesign failed; the app will still run locally" >&2
fi

echo "==> Built $APP_DIR"
