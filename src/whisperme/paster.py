from __future__ import annotations

import subprocess
import time
import threading
from pynput.keyboard import Controller, Key


_kb = Controller()


def paste(text: str) -> None:
    """Copy text to clipboard and simulate Cmd+V to paste into the active app."""
    # Save current clipboard
    try:
        old = subprocess.run(
            ["pbpaste"], capture_output=True, timeout=2
        ).stdout
    except Exception:
        old = None

    # Copy new text
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=2)

    # Small delay to ensure clipboard is ready
    time.sleep(0.05)

    # Simulate Cmd+V
    _kb.press(Key.cmd)
    _kb.press("v")
    _kb.release("v")
    _kb.release(Key.cmd)

    # Restore old clipboard after a short delay
    if old is not None:
        def _restore():
            time.sleep(0.5)
            try:
                subprocess.run(["pbcopy"], input=old, timeout=2)
            except Exception:
                pass

        threading.Thread(target=_restore, daemon=True).start()
