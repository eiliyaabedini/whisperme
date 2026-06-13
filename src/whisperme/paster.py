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

    # Simulate Cmd+V. Release inside finally so a mid-combo error can never
    # leave Cmd "stuck down", which would otherwise hijack every subsequent
    # keystroke and look like a system-wide freeze.
    try:
        _kb.press(Key.cmd)
        _kb.press("v")
        _kb.release("v")
    finally:
        _kb.release(Key.cmd)

    # Restore old clipboard after a delay long enough for the target app to have
    # consumed the paste. Restoring too early races the async paste and can paste
    # the OLD clipboard contents instead of the dictated text.
    if old is not None:
        def _restore():
            time.sleep(1.2)
            try:
                subprocess.run(["pbcopy"], input=old, timeout=2)
            except Exception:
                pass

        threading.Thread(target=_restore, daemon=True).start()
