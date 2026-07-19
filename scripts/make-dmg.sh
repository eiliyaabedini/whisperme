#!/bin/bash
# Package dist/WhisperMe.app into a drag-to-install DMG.
#
# Produces dist/WhisperMe-<version>.dmg: opening it shows the app and an
# Applications shortcut side by side over a background that says what to do.
#
# Environment:
#   WHISPERME_SIGN_IDENTITY   sign the DMG itself (matches the app's identity)
#   WHISPERME_NOTARY_PROFILE  notarytool keychain profile; enables notarize+staple
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="WhisperMe"
VERSION="$(awk -F'"' '/^version/{print $2; exit}' "$REPO_DIR/pyproject.toml")"
DIST_DIR="$REPO_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
VOL_NAME="$APP_NAME"
DMG_PATH="$DIST_DIR/$APP_NAME-$VERSION.dmg"
STAGE_DIR="$DIST_DIR/dmg-stage"
RW_DMG="$DIST_DIR/$APP_NAME-rw.dmg"

SIGN_IDENTITY="${WHISPERME_SIGN_IDENTITY:-}"
NOTARY_PROFILE="${WHISPERME_NOTARY_PROFILE:-}"

if [[ ! -d "$APP_DIR" ]]; then
    echo "error: $APP_DIR not found — run scripts/build-app.sh first" >&2
    exit 1
fi

echo "==> Packaging $APP_NAME $VERSION"

rm -rf "$STAGE_DIR" "$RW_DMG" "$DMG_PATH"
mkdir -p "$STAGE_DIR/.background"

echo "==> Staging contents"
/usr/bin/ditto "$APP_DIR" "$STAGE_DIR/$APP_NAME.app"
ln -s /Applications "$STAGE_DIR/Applications"

echo "==> Rendering background"
BG_TMP="$(mktemp -d -t whisperme-bg)"
trap 'rm -rf "$BG_TMP"' EXIT
BUNDLED_PYTHON="$APP_DIR/Contents/Resources/python/bin/python3.12"
if "$BUNDLED_PYTHON" "$REPO_DIR/scripts/make_dmg_background.py" "$BG_TMP" >/dev/null; then
    # Combine 1x + 2x into one HiDPI TIFF so the background stays sharp on
    # Retina displays instead of being upscaled.
    tiffutil -cathidpicheck "$BG_TMP/background.png" "$BG_TMP/background@2x.png" \
        -out "$STAGE_DIR/.background/background.tiff" >/dev/null 2>&1 \
        || cp "$BG_TMP/background.png" "$STAGE_DIR/.background/background.tiff"
else
    echo "warning: background generation failed; using a plain window" >&2
fi

# Size the read-write image generously. The bundle is ~27k files, and HFS+
# catalog overhead on that many entries blows through a small fixed margin
# ("hdiutil: create failed - No space left on device"). This image is temporary
# and compacted away by the UDZO conversion, so over-allocating costs nothing.
SIZE_MB=$(( $(du -sm "$STAGE_DIR" | cut -f1) * 3 / 2 + 200 ))
echo "==> Creating read-write image (${SIZE_MB}M)"
hdiutil create -srcfolder "$STAGE_DIR" -volname "$VOL_NAME" -fs HFS+ \
    -format UDRW -size "${SIZE_MB}m" "$RW_DMG" >/dev/null

echo "==> Applying window layout"
# Must mount under /Volumes and stay browsable: the AppleScript below addresses
# the volume as `disk "WhisperMe"`, and Finder cannot see a volume mounted at a
# custom path or hidden with -nobrowse.
MOUNT_DIR="/Volumes/$VOL_NAME"
hdiutil detach "$MOUNT_DIR" >/dev/null 2>&1 || true
hdiutil attach "$RW_DMG" -noverify -noautoopen >/dev/null

LAYOUT_ERR="$(mktemp -t whisperme-layout)"
LAYOUT_STATUS=0
osascript > /dev/null 2> "$LAYOUT_ERR" <<APPLESCRIPT || LAYOUT_STATUS=$?
tell application "Finder"
    tell disk "$VOL_NAME"
        open
        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set the bounds of container window to {200, 150, 800, 550}
        set opts to the icon view options of container window
        set arrangement of opts to not arranged
        set icon size of opts to 96
        set background picture of opts to file ".background:background.tiff"
        set position of item "$APP_NAME.app" of container window to {150, 190}
        set position of item "Applications" of container window to {450, 190}
        close
        open
        update without registering applications
        delay 2
    end tell
end tell
APPLESCRIPT

if [[ $LAYOUT_STATUS -ne 0 ]]; then
    echo "warning: Finder layout failed; the DMG installs but looks plain" >&2
    sed 's/^/         /' "$LAYOUT_ERR" >&2
fi
rm -f "$LAYOUT_ERR"
sync
hdiutil detach "$MOUNT_DIR" >/dev/null || hdiutil detach "$MOUNT_DIR" -force >/dev/null

echo "==> Compressing"
hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -o "$DMG_PATH" >/dev/null
rm -f "$RW_DMG"
rm -rf "$STAGE_DIR"

if [[ -n "$SIGN_IDENTITY" ]]; then
    echo "==> Signing DMG"
    codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG_PATH"
fi

if [[ -n "$NOTARY_PROFILE" ]]; then
    echo "==> Notarizing (this usually takes a few minutes)"
    xcrun notarytool submit "$DMG_PATH" --keychain-profile "$NOTARY_PROFILE" --wait
    echo "==> Stapling"
    xcrun stapler staple "$DMG_PATH"
    echo "==> Gatekeeper check"
    spctl -a -t open --context context:primary-signature -vv "$DMG_PATH" || true
else
    echo ""
    echo "NOTE: not notarized (WHISPERME_NOTARY_PROFILE unset)."
    echo "      macOS will block this DMG on other Macs until the user clears"
    echo "      quarantine — see the Troubleshooting section of README.md."
fi

SIZE="$(du -sh "$DMG_PATH" | cut -f1)"
echo ""
echo "==> Built $DMG_PATH ($SIZE)"
