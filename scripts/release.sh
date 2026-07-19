#!/bin/bash
# Cut a WhisperMe release: build, package, tag, and publish the DMG.
#
#   scripts/release.sh 0.2.0        # bump version, build, tag, publish
#   scripts/release.sh              # build + publish at the current version
#   scripts/release.sh 0.2.0 --dry-run
#
# Signing/notarization are picked up from the environment; without them the
# release still publishes, but the DMG is un-notarized (see README).
#   WHISPERME_SIGN_IDENTITY="Developer ID Application: Name (TEAMID)"
#   WHISPERME_NOTARY_PROFILE="whisperme-notary"
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

NEW_VERSION=""
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        -*) echo "error: unknown flag $arg" >&2; exit 1 ;;
        *) NEW_VERSION="$arg" ;;
    esac
done

if ! command -v gh >/dev/null; then
    echo "error: gh CLI not found — install it with: brew install gh" >&2
    exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
    echo "error: working tree is dirty — commit or stash first" >&2
    git status --short >&2
    exit 1
fi

if [[ -n "$NEW_VERSION" ]]; then
    if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "error: version must look like 1.2.3 (got: $NEW_VERSION)" >&2
        exit 1
    fi
    echo "==> Setting version to $NEW_VERSION"
    /usr/bin/sed -i '' -E "s/^version = \".*\"/version = \"$NEW_VERSION\"/" pyproject.toml
    # Keep uv.lock's record of the project version in step with pyproject.
    uv lock --offline >/dev/null 2>&1 || uv lock >/dev/null
fi

VERSION="$(awk -F'"' '/^version/{print $2; exit}' pyproject.toml)"
TAG="v$VERSION"
DMG="dist/WhisperMe-$VERSION.dmg"

if git rev-parse "$TAG" >/dev/null 2>&1; then
    echo "error: tag $TAG already exists" >&2
    exit 1
fi

echo "==> Releasing WhisperMe $VERSION"
[[ -z "${WHISPERME_SIGN_IDENTITY:-}" ]] && echo "    (unsigned build — see README Troubleshooting)"

./scripts/build-app.sh
./scripts/make-dmg.sh

SHA="$(shasum -a 256 "$DMG" | cut -d' ' -f1)"
SIZE="$(du -h "$DMG" | cut -f1)"
echo "==> $DMG ($SIZE)"
echo "    sha256: $SHA"

NOTES_FILE="$(mktemp -t whisperme-notes)"
trap 'rm -f "$NOTES_FILE"' EXIT
{
    echo "## Install"
    echo
    echo "1. Download \`WhisperMe-$VERSION.dmg\` below."
    echo "2. Open it and drag **WhisperMe** into **Applications**."
    echo "3. Launch WhisperMe from Applications and follow the permission setup."
    echo
    if [[ -z "${WHISPERME_NOTARY_PROFILE:-}" ]]; then
        echo "> **Note** — this build is not notarized, so macOS will say WhisperMe"
        echo "> \"is damaged\". It isn't; that message is what Gatekeeper shows for any"
        echo "> app without an Apple-notarized signature. To open it anyway, run:"
        echo "> \`\`\`"
        echo "> xattr -dr com.apple.quarantine /Applications/WhisperMe.app"
        echo "> \`\`\`"
        echo
    fi
    echo "On first launch WhisperMe downloads its speech models (~1.6 GB) with a"
    echo "progress window. After that it runs fully offline."
    echo
    echo "**Requires** an Apple Silicon Mac on macOS 12 or later."
    echo
    echo "\`sha256: $SHA\`"
} > "$NOTES_FILE"

if [[ "$DRY_RUN" == "1" ]]; then
    echo ""
    echo "==> Dry run — not tagging or publishing. Release notes would be:"
    echo ""
    sed 's/^/    /' "$NOTES_FILE"
    exit 0
fi

if [[ -n "$NEW_VERSION" ]]; then
    git add pyproject.toml uv.lock
    git commit -m "Release $TAG"
fi

echo "==> Tagging $TAG"
git tag -a "$TAG" -m "WhisperMe $VERSION"
git push origin HEAD
git push origin "$TAG"

echo "==> Publishing GitHub release"
gh release create "$TAG" "$DMG" \
    --title "WhisperMe $VERSION" \
    --notes-file "$NOTES_FILE"

echo ""
echo "==> Released: $(gh release view "$TAG" --json url -q .url)"
