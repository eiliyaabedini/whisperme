from __future__ import annotations

import logging
import plistlib
from pathlib import Path

from whisperme.paths import app_bundle_path

logger = logging.getLogger(__name__)

_LABEL = "com.eiliya.whisperme"
_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def is_available() -> bool:
    """Start-at-login needs an installed WhisperMe.app to point at."""
    return app_bundle_path() is not None


def is_enabled() -> bool:
    return _PLIST_PATH.exists()


def enable() -> bool:
    app = app_bundle_path()
    if app is None:
        logger.warning("Cannot enable start-at-login: WhisperMe.app not installed")
        return False
    payload = {
        "Label": _LABEL,
        "ProgramArguments": ["/usr/bin/open", "-a", str(app)],
        "RunAtLoad": True,
    }
    _PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_PLIST_PATH, "wb") as fp:
        plistlib.dump(payload, fp)
    logger.info("Start-at-login enabled: %s", _PLIST_PATH)
    return True


def disable() -> None:
    _PLIST_PATH.unlink(missing_ok=True)
    logger.info("Start-at-login disabled")
