from __future__ import annotations

import logging
import shlex
import subprocess
import threading

from PyObjCTools import AppHelper

from whisperme import autofix, permissions
from whisperme.config import Config
from whisperme.hotkey import HotkeyListener
from whisperme.overlay import Overlay
from whisperme.paster import paste
from whisperme.paths import app_bundle_path
from whisperme.postprocess import cleanup
from whisperme.recorder import Recorder
from whisperme.statusbar import StatusBar

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
        self._autofixer = autofix.AutoFixer()
        self._statusbar = StatusBar(
            on_quit=self.shutdown,
            on_permission_setup=self._run_permission_setup,
            on_autofix=self._start_autofix,
        )

        self._llm_running = False
        self._llm_pending: str | None = None
        self._llm_last_input: str = ""

    def run(self) -> None:
        # Start recorder worker thread (doesn't open mic until first activation)
        threading.Thread(target=self._recorder.run_worker, daemon=True).start()

        # Interactive permission setup (mic + accessibility) before the hotkey,
        # so the event tap is created only once accessibility is granted.
        report = permissions.ensure_permissions(interactive=not self._config.no_setup)

        hotkey_ok = self._hotkey.start()
        if not hotkey_ok and report.accessibility and not self._config.no_setup:
            # Accessibility looks granted but the tap still failed — some macOS
            # versions additionally require Input Monitoring.
            hotkey_ok = permissions.hotkey_failure_flow(self._hotkey.start)
        self._statusbar.set_permissions_ok(report.all_granted and hotkey_ok)

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

    def _run_permission_setup(self) -> None:
        """Menu action: re-run the interactive permission wizard."""
        report = permissions.ensure_permissions(interactive=True)
        hotkey_ok = self._hotkey.start()
        if not hotkey_ok and report.accessibility:
            hotkey_ok = permissions.hotkey_failure_flow(self._hotkey.start)
        self._statusbar.set_permissions_ok(report.all_granted and hotkey_ok)

    def _start_autofix(self) -> None:
        """Menu action: headless Claude Code session to diagnose + fix + push."""
        if autofix.find_claude() is None:
            permissions.alert(
                "Claude Code Not Found",
                "Auto-Fix runs the claude CLI, which wasn't found on this Mac.\n\n"
                "Install Claude Code (https://claude.com/claude-code) and try again.",
                ["OK"],
            )
            return
        if not self._autofixer.start(self._on_autofix_done):
            return
        logger.info("Auto-fix started from menu")
        self._statusbar.set_autofix_running(True)
        self._statusbar.set_state("fixing")

    def _on_autofix_done(self, result: autofix.AutoFixResult) -> None:
        def _show() -> None:
            self._statusbar.set_autofix_running(False)
            self._statusbar.set_state("recording" if self._recording else "idle")

            buttons = ["OK"]
            if result.report_path is not None:
                buttons.append("Open Full Report")
            offer_restart = result.committed and app_bundle_path() is not None
            if offer_restart:
                buttons.insert(0, "Restart WhisperMe")

            summary = result.summary
            if len(summary) > 1500:
                summary = summary[:1500] + "…\n\n(full text in the report)"
            title = "Auto-Fix Finished" if result.ok else "Auto-Fix Failed"
            choice = permissions.alert(title, summary, buttons)

            label = buttons[choice] if 0 <= choice < len(buttons) else "OK"
            if label == "Open Full Report":
                subprocess.run(["open", str(result.report_path)], check=False)
            elif label == "Restart WhisperMe":
                self._restart_via_bundle()

        AppHelper.callAfter(_show)

    def _restart_via_bundle(self) -> None:
        bundle = app_bundle_path()
        if bundle is None:
            return
        logger.info("Restarting via %s", bundle)
        # Detached so it survives our exit; the delay lets this instance
        # release the single-instance lock first.
        subprocess.Popen(
            ["/bin/sh", "-c", f"sleep 2; /usr/bin/open {shlex.quote(str(bundle))}"],
            start_new_session=True,
        )
        self.shutdown()

    def _start_recording(self) -> None:
        logger.info("Recording start requested")
        self._recording = True
        self._llm_last_input = ""
        self._overlay.show()
        self._statusbar.set_state("recording")
        threading.Thread(target=self._recorder.start, daemon=True).start()

    def _stop_recording(self) -> None:
        self._recording = False
        text = self._recorder.stop()
        logger.info("Recording stopped: final_text_len=%d", len(text))
        self._overlay.hide()

        if text.strip():
            self._statusbar.set_state("processing")
            threading.Thread(target=self._finish_and_paste, args=(text,), daemon=True).start()
        else:
            self._statusbar.set_state("idle")

    def _finish_and_paste(self, text: str) -> None:
        try:
            if not self._config.no_llm:
                try:
                    text = cleanup(text)
                except Exception:
                    logger.exception("LLM cleanup failed; pasting raw transcription")
            paste(text)
        except Exception:
            logger.exception("paste failed")
        finally:
            self._statusbar.set_state("idle")

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
        self._statusbar.set_state("idle")

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
