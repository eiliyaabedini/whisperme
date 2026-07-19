#!/bin/bash
# Build dist/WhisperMe.app — a fully self-contained menu-bar app.
#
# The bundle carries its own CPython and every dependency, so it runs on any
# Apple Silicon Mac with no uv, no Homebrew and no source checkout. Whisper
# models are NOT bundled (they would add ~1.6 GB); the app downloads them on
# first launch with a progress window.
#
# Environment:
#   WHISPERME_SIGN_IDENTITY  Developer ID Application identity. Unset => ad-hoc
#                            signature, which macOS blocks on downloaded copies.
#   WHISPERME_NOTARY_PROFILE notarytool keychain profile. Unset => no notarization.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="WhisperMe"
BUNDLE_ID="com.eiliya.whisperme"
PY_VERSION="3.12"
VERSION="$(awk -F'"' '/^version/{print $2; exit}' "$REPO_DIR/pyproject.toml")"
DIST_DIR="$REPO_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
RESOURCES="$APP_DIR/Contents/Resources"
PYTHON_REL_PATH="Contents/Resources/python/bin/python${PY_VERSION}"

SIGN_IDENTITY="${WHISPERME_SIGN_IDENTITY:-}"
NOTARY_PROFILE="${WHISPERME_NOTARY_PROFILE:-}"

UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" ]]; then
    for candidate in "$HOME/.local/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
        [[ -x "$candidate" ]] && UV_BIN="$candidate" && break
    done
fi
if [[ -z "$UV_BIN" ]]; then
    echo "error: uv not found — install it from https://docs.astral.sh/uv/" >&2
    exit 1
fi

CC="${CC:-cc}"
if ! command -v "$CC" >/dev/null; then
    echo "error: no C compiler found — run xcode-select --install" >&2
    exit 1
fi

echo "==> Building $APP_NAME.app $VERSION"

# ---------------------------------------------------------------- interpreter
# uv's managed CPython builds (python-build-standalone) are relocatable: they
# resolve their prefix from the executable path, so the whole tree can be copied
# into the bundle and still find its own stdlib.
echo "==> Locating a standalone CPython $PY_VERSION"
"$UV_BIN" python install "$PY_VERSION" >/dev/null 2>&1 || true
PYTHON_SRC="$("$UV_BIN" python list --only-installed --output-format json \
    | /usr/bin/python3 -c "
import json, sys
want = '$PY_VERSION'
for entry in json.load(sys.stdin):
    path = entry.get('path') or ''
    if entry['version'].startswith(want + '.') and '/uv/python/' in path:
        # .../cpython-3.12.11-macos-aarch64-none/bin/python3.12 -> install root
        print(path.rsplit('/bin/', 1)[0])
        break
")"
if [[ -z "$PYTHON_SRC" || ! -d "$PYTHON_SRC" ]]; then
    echo "error: no uv-managed CPython $PY_VERSION found (try: uv python install $PY_VERSION)" >&2
    exit 1
fi
echo "    $PYTHON_SRC"

rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS" "$RESOURCES"

echo "==> Copying interpreter into the bundle"
/usr/bin/ditto "$PYTHON_SRC" "$RESOURCES/python"
# Ours to modify now; the marker only exists to protect uv's shared copy.
find "$RESOURCES/python" -name "EXTERNALLY-MANAGED" -delete

BUNDLED_PYTHON="$RESOURCES/python/bin/python${PY_VERSION}"

# --------------------------------------------------------------- dependencies
# Install from uv.lock, never a fresh resolve. RealtimeSTT 1.x restructured its
# dependencies and drops faster-whisper/ctranslate2 entirely, so an unpinned
# build produces an app that cannot transcribe at all.
echo "==> Installing dependencies from uv.lock"
REQ_FILE="$(mktemp -t whisperme-reqs)"
trap 'rm -f "$REQ_FILE"' EXIT
"$UV_BIN" export --frozen --no-dev --no-emit-project \
    --project "$REPO_DIR" --format requirements-txt > "$REQ_FILE"
"$UV_BIN" pip install --python "$BUNDLED_PYTHON" --link-mode=copy -r "$REQ_FILE" >/dev/null
"$UV_BIN" pip install --python "$BUNDLED_PYTHON" --link-mode=copy --no-deps "$REPO_DIR" >/dev/null

# ---------------------------------------------------------------------- slim
# Only things that provably never run: C headers for building extensions, plus
# stdlib components this app doesn't use. Deliberately conservative — torch/bin
# looks like build tooling but holds torch_shm_manager, which torch resolves at
# import time and refuses to start without.
echo "==> Trimming build-only files"
SITE="$RESOURCES/python/lib/python${PY_VERSION}/site-packages"
STDLIB="$RESOURCES/python/lib/python${PY_VERSION}"
rm -rf "$SITE/torch/include" \
       "$STDLIB/idlelib" \
       "$STDLIB/tkinter" \
       "$STDLIB/test" \
       "$RESOURCES/python/share" 2>/dev/null || true
find "$RESOURCES/python" -name "*.pyc" -delete 2>/dev/null || true

# Precompile the WHOLE interpreter tree, stdlib included. Any .pyc written at
# runtime lands inside the bundle and breaks its code signature ("a sealed
# resource is missing or invalid"), which would make a notarized build fail
# Gatekeeper the moment someone launched it. The launcher also exports
# PYTHONDONTWRITEBYTECODE=1 as a belt-and-braces guard.
echo "==> Precompiling bytecode"
"$BUNDLED_PYTHON" -m compileall -q -j 0 "$RESOURCES/python/lib" >/dev/null 2>&1 || true

# --------------------------------------------------------------------- bundle
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
    <key>NSAppleEventsUsageDescription</key>
    <string>WhisperMe pastes your transcribed text into the app you were typing in.</string>
</dict>
</plist>
PLIST

echo "==> Compiling launcher stub"
"$CC" -O2 -Wall \
    -DPYTHON_REL_PATH="\"$PYTHON_REL_PATH\"" \
    -o "$APP_DIR/Contents/MacOS/whisperme" \
    "$REPO_DIR/scripts/launcher.c"

echo "==> Generating icon"
if ! "$BUNDLED_PYTHON" "$REPO_DIR/scripts/make_icon.py" "$RESOURCES/$APP_NAME.icns"; then
    echo "warning: icon generation failed; the app will use the generic icon" >&2
fi

# -------------------------------------------------------------------- signing
"$REPO_DIR/scripts/sign-app.sh" "$APP_DIR"

SIZE="$(du -sh "$APP_DIR" | cut -f1)"
echo "==> Built $APP_DIR ($SIZE)"
