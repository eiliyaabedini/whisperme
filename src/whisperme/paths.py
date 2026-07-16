from __future__ import annotations

import os
from pathlib import Path

# Repo root (src/whisperme/paths.py -> repo). Works both from a terminal
# checkout and from the WhisperMe.app launcher, which runs the same checkout.
REPO_DIR = Path(__file__).resolve().parent.parent.parent

LOG_DIR = REPO_DIR / "logs"


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
