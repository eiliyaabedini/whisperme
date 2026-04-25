from __future__ import annotations

import logging
import threading

from whisperme.config import Config
from whisperme.hotkey import HotkeyListener
from whisperme.overlay import Overlay
from whisperme.paster import paste
from whisperme.postprocess import cleanup
from whisperme.recorder import Recorder

logger = logging.getLogger(__name__)


class App:
    """Orchestrates hotkey -> recorder -> overlay -> paster pipeline."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._recording = False
        self._lock = threading.Lock()

        self._overlay = Overlay(
            on_reset=self._reset_recording,
            on_close=self._close_without_paste,
            show_llm=not config.no_llm,
        )
        self._recorder = Recorder(
            config=config,
            on_realtime_update=self._on_realtime_text,
        )
        self._hotkey = HotkeyListener(on_toggle=self._toggle)

        self._llm_running = False
        self._llm_pending: str | None = None
        self._llm_last_input: str = ""

    def run(self) -> None:
        # Start recorder worker thread (doesn't open mic until first activation)
        threading.Thread(target=self._recorder.run_worker, daemon=True).start()

        self._hotkey.start()

        print("[whisperme] Ready! Press Option+/ to start/stop dictation.")
        print("[whisperme] First activation will load models (may take a few seconds).")
        if not self._config.no_llm:
            print("[whisperme] LLM post-processing enabled (Apple Foundation Model)")
        print("[whisperme] Press Ctrl+C to quit.\n")

        try:
            self._overlay.run_event_loop()
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self) -> None:
        print("\n[whisperme] Shutting down...")
        self._hotkey.stop()
        self._recorder.shutdown()
        self._overlay.stop_event_loop()

    def _toggle(self) -> None:
        with self._lock:
            if self._recording:
                self._stop_recording()
            else:
                self._start_recording()

    def _start_recording(self) -> None:
        logger.info("Recording start requested")
        self._recording = True
        self._llm_last_input = ""
        self._overlay.show()
        threading.Thread(target=self._recorder.start, daemon=True).start()

    def _stop_recording(self) -> None:
        self._recording = False
        text = self._recorder.stop()
        logger.info("Recording stopped: final_text_len=%d", len(text))
        self._overlay.hide()

        if text.strip():
            if self._config.no_llm:
                threading.Thread(target=paste, args=(text,), daemon=True).start()
            else:
                def _cleanup_and_paste():
                    try:
                        cleaned = cleanup(text)
                        paste(cleaned)
                    except Exception:
                        logger.exception("cleanup-and-paste failed")
                threading.Thread(target=_cleanup_and_paste, daemon=True).start()

    def _reset_recording(self) -> None:
        """Reset: stop current recording, clear text, start fresh — overlay stays open."""
        logger.info("Reset requested")
        with self._lock:
            self._recording = False
            self._llm_pending = None
            self._recorder.stop()
            self._overlay.reset()
            self._recording = True
            threading.Thread(target=self._recorder.start, daemon=True).start()

    def _close_without_paste(self) -> None:
        """Close: stop recording, hide overlay, discard text."""
        logger.info("Close without paste requested")
        with self._lock:
            self._recording = False
            self._llm_pending = None
            self._recorder.stop()
            self._overlay.hide()

    def _schedule_llm_cleanup(self, text: str) -> None:
        with self._lock:
            if text == self._llm_last_input:
                return
            if self._llm_running:
                self._llm_pending = text
                return
            self._llm_running = True
            self._llm_last_input = text
        threading.Thread(target=self._run_llm_cleanup, args=(text,), daemon=True).start()

    def _run_llm_cleanup(self, text: str) -> None:
        while True:
            with self._lock:
                if not self._recording:
                    self._llm_running = False
                    return

            try:
                cleaned = cleanup(text)
            except Exception as e:
                logger.exception("Realtime LLM cleanup crashed")
                print(f"[app] LLM realtime cleanup failed: {e}", flush=True)
                with self._lock:
                    self._llm_running = False
                return

            with self._lock:
                if not self._recording:
                    self._llm_running = False
                    return
            self._overlay.update_llm_text(cleaned)

            # Check if newer text arrived while we were processing
            with self._lock:
                if self._llm_pending and self._llm_pending != self._llm_last_input and self._recording:
                    text = self._llm_pending
                    self._llm_pending = None
                    self._llm_last_input = text
                else:
                    self._llm_running = False
                    return

    def _on_realtime_text(self, text: str) -> None:
        with self._lock:
            if not self._recording:
                return
        self._overlay.update_text(text)
        if not self._config.no_llm:
            self._schedule_llm_cleanup(text)
