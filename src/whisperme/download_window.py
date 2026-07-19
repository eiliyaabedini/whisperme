"""First-run model download window.

Shown once, before the recorder starts, while Whisper models are fetched. The
download runs on a worker thread; every UI mutation is marshalled back to the
main thread because AppKit owns it.
"""

from __future__ import annotations

import logging
import threading

import AppKit
from PyObjCTools import AppHelper

from whisperme import models

logger = logging.getLogger(__name__)

_WIDTH = 420.0
_HEIGHT = 150.0


def _human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class DownloadWindow:
    """Modal-ish progress panel driving `models.download` on a worker thread."""

    def __init__(self, to_download: list[str]) -> None:
        self._models = to_download
        self._error: Exception | None = None
        self._done = threading.Event()

        style = AppKit.NSWindowStyleMaskTitled
        self._window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(0, 0, _WIDTH, _HEIGHT), style, AppKit.NSBackingStoreBuffered, False
        )
        self._window.setTitle_("Setting up WhisperMe")
        self._window.setLevel_(AppKit.NSFloatingWindowLevel)
        self._window.center()

        content = self._window.contentView()

        self._title = AppKit.NSTextField.labelWithString_("Downloading speech models…")
        self._title.setFrame_(AppKit.NSMakeRect(20, _HEIGHT - 50, _WIDTH - 40, 20))
        self._title.setFont_(AppKit.NSFont.boldSystemFontOfSize_(13))
        content.addSubview_(self._title)

        self._detail = AppKit.NSTextField.labelWithString_(
            "This happens once. The models run entirely on your Mac."
        )
        self._detail.setFrame_(AppKit.NSMakeRect(20, _HEIGHT - 74, _WIDTH - 40, 18))
        self._detail.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self._detail.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        content.addSubview_(self._detail)

        self._bar = AppKit.NSProgressIndicator.alloc().initWithFrame_(
            AppKit.NSMakeRect(20, _HEIGHT - 104, _WIDTH - 40, 16)
        )
        self._bar.setStyle_(AppKit.NSProgressIndicatorStyleBar)
        self._bar.setIndeterminate_(True)
        self._bar.setMinValue_(0.0)
        self._bar.setMaxValue_(1.0)
        content.addSubview_(self._bar)

        self._status = AppKit.NSTextField.labelWithString_("Starting…")
        self._status.setFrame_(AppKit.NSMakeRect(20, 20, _WIDTH - 40, 18))
        self._status.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self._status.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        content.addSubview_(self._status)

    def _set_status(self, text: str) -> None:
        AppHelper.callAfter(lambda: self._status.setStringValue_(text))

    def _on_progress(self, model: str, done: int, total: int) -> None:
        if total <= 0:
            return
        fraction = min(1.0, done / total)

        def _apply() -> None:
            if self._bar.isIndeterminate():
                self._bar.setIndeterminate_(False)
                self._bar.stopAnimation_(None)
            self._bar.setDoubleValue_(fraction)
            self._status.setStringValue_(f"{model} — {_human(done)} of {_human(total)}")

        AppHelper.callAfter(_apply)

    def _worker(self) -> None:
        try:
            for index, model in enumerate(self._models, start=1):
                label = f"Downloading {model} ({index} of {len(self._models)})…"
                AppHelper.callAfter(lambda t=label: self._title.setStringValue_(t))

                def _reset() -> None:
                    self._bar.setIndeterminate_(True)
                    self._bar.startAnimation_(None)

                AppHelper.callAfter(_reset)
                self._set_status(f"Contacting Hugging Face for {model}…")
                models.download(model, self._on_progress)
        except Exception as exc:  # surfaced to the caller, which alerts the user
            logger.exception("Model download failed")
            self._error = exc
        finally:
            self._done.set()
            AppHelper.callAfter(AppKit.NSApp().stopModal)

    def run(self) -> Exception | None:
        """Show the window and pump the event loop until downloads finish.

        Returns the failure, or None on success.
        """
        self._window.makeKeyAndOrderFront_(None)
        AppKit.NSApp().activateIgnoringOtherApps_(True)
        self._bar.startAnimation_(None)

        threading.Thread(target=self._worker, daemon=True).start()
        AppKit.NSApp().runModalForWindow_(self._window)

        self._window.orderOut_(None)
        self._window.close()
        return self._error


def ensure_models_downloaded(wanted: list[str]) -> Exception | None:
    """Download any missing models behind a progress window. None if nothing to do."""
    models.configure_environment()
    missing = models.missing_models(wanted)
    if not missing:
        return None
    logger.info("First-run model download: %s", ", ".join(missing))
    return DownloadWindow(missing).run()
