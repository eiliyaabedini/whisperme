#!/bin/bash
# Build WhisperMe.app and install it into /Applications (or ~/Applications).
# Usage: scripts/install.sh [--open]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$REPO_DIR/scripts/build-app.sh"

TARGET_DIR="/Applications"
if [[ ! -w "$TARGET_DIR" ]]; then
    TARGET_DIR="$HOME/Applications"
    mkdir -p "$TARGET_DIR"
fi
TARGET="$TARGET_DIR/WhisperMe.app"

if [[ -d "$TARGET" ]]; then
    echo "==> Replacing existing $TARGET"
    echo "    (if WhisperMe is running, quit it from the menu bar icon first)"
    rm -rf "$TARGET"
fi

ditto "$REPO_DIR/dist/WhisperMe.app" "$TARGET"

echo ""
echo "==> Installed $TARGET"
echo ""
echo "First launch walks you through granting Microphone + Accessibility."
echo "Note: rebuilding/reinstalling changes the app's signature, so macOS may"
echo "ask you to re-grant permissions after an update."
echo ""
echo "Launch it with:  open '$TARGET'"

if [[ "${1:-}" == "--open" ]]; then
    open "$TARGET"
fi
