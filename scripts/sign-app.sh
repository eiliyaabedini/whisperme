#!/bin/bash
# Code-sign WhisperMe.app. Usage: scripts/sign-app.sh <path to .app>
#
# With WHISPERME_SIGN_IDENTITY set to a "Developer ID Application" identity the
# bundle is signed with the hardened runtime and can then be notarized, which is
# what lets other people open the DMG normally. Without it we fall back to an
# ad-hoc signature: fine on the machine that built it, but macOS refuses to open
# a downloaded copy ("WhisperMe is damaged") until the quarantine flag is
# cleared by hand.
set -euo pipefail

APP_DIR="${1:?usage: sign-app.sh <path to .app>}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENTITLEMENTS="$REPO_DIR/scripts/entitlements.plist"

SIGN_IDENTITY="${WHISPERME_SIGN_IDENTITY:-}"

if [[ -z "$SIGN_IDENTITY" ]]; then
    echo "==> Signing ad-hoc (set WHISPERME_SIGN_IDENTITY to distribute)"
    # Inner-first: a bundle's signature covers its nested code, so anything
    # signed after the outer bundle would invalidate it.
    find "$APP_DIR" -type f \( -name "*.so" -o -name "*.dylib" \) -print0 \
        | xargs -0 -P 8 -n 50 codesign --force --sign - 2>/dev/null || true
    codesign --force --deep --sign - "$APP_DIR" 2>/dev/null \
        || echo "warning: ad-hoc codesign failed; the app will still run locally" >&2
    exit 0
fi

echo "==> Signing with: $SIGN_IDENTITY"

# Every Mach-O inside the bundle needs its own signature under the hardened
# runtime. Collect them explicitly rather than trusting --deep, which Apple
# documents as unreliable for exactly this shape of bundle.
echo "    collecting Mach-O binaries…"
MACHO_LIST="$(mktemp -t whisperme-macho)"
trap 'rm -f "$MACHO_LIST"' EXIT
find "$APP_DIR" -type f \
    \( -name "*.so" -o -name "*.dylib" -o -name "*.a" -o -perm -u+x \) \
    ! -path "*/Contents/MacOS/whisperme" -print0 \
    | xargs -0 -P 8 -I{} sh -c 'file -b "{}" | grep -q "Mach-O" && echo "{}"' \
    > "$MACHO_LIST" || true

COUNT="$(wc -l < "$MACHO_LIST" | tr -d ' ')"
echo "    signing $COUNT nested binaries…"
xargs -a "$MACHO_LIST" -P 8 -n 20 \
    codesign --force --timestamp --options runtime \
             --entitlements "$ENTITLEMENTS" --sign "$SIGN_IDENTITY"

echo "    signing launcher + bundle…"
codesign --force --timestamp --options runtime \
         --entitlements "$ENTITLEMENTS" --sign "$SIGN_IDENTITY" \
         "$APP_DIR/Contents/MacOS/whisperme"
codesign --force --timestamp --options runtime \
         --entitlements "$ENTITLEMENTS" --sign "$SIGN_IDENTITY" \
         "$APP_DIR"

echo "==> Verifying signature"
codesign --verify --deep --strict --verbose=2 "$APP_DIR"
