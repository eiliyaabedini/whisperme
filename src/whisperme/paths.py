from __future__ import annotations

import os
from pathlib import Path

# Two very different layouts run this code:
#
#   source checkout  — src/whisperme/paths.py, with pyproject.toml at the root
#   installed .app   — WhisperMe.app/Contents/Resources/python/lib/python3.12/
#                      site-packages/whisperme/paths.py
#
# The checkout can write to its own directory and has a git remote, so logs live
# beside the source and developer-only features (Auto-Fix) are available. A
# downloaded .app has neither, so its logs go to the standard macOS location.
_SRC_ROOT = Path(__file__).resolve().parent.parent.parent

#: True when running from an installed app bundle rather than a source checkout.
IS_BUNDLED = not (_SRC_ROOT / "pyproject.toml").is_file()

#: The source checkout, or None in a distributed build (there is no checkout).
REPO_DIR: Path | None = None if IS_BUNDLED else _SRC_ROOT

if IS_BUNDLED:
    LOG_DIR = Path.home() / "Library" / "Logs" / "WhisperMe"
else:
    LOG_DIR = _SRC_ROOT / "logs"

#: Whisper models are downloaded at runtime rather than shipped in the DMG.
#: None means "use the standard Hugging Face cache", which lets anyone who
#: already pulled these models (or uses other HF tooling) reuse them instead of
#: re-downloading ~1.6 GB.
_model_dir_override = os.environ.get("WHISPERME_MODEL_DIR")
MODEL_CACHE_DIR: Path | None = Path(_model_dir_override) if _model_dir_override else None


def app_bundle_path() -> Path | None:
    """Path to WhisperMe.app when launched via the app bundle, else a best guess.

    The .app launcher exports WHISPERME_APP_BUNDLE. When running from a
    terminal, fall back to the standard install locations so menu items like
    "Start at Login" can still target the installed app.
    """
    env = os.environ.get("WHISPERME_APP_BUNDLE")
    if env and Path(env).is_dir():
        return Path(env)
    for candidate in (
        Path("/Applications/WhisperMe.app"),
        Path.home() / "Applications" / "WhisperMe.app",
    ):
        if candidate.is_dir():
            return candidate
    return None
