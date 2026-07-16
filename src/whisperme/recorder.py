from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from RealtimeSTT import AudioToTextRecorder

from whisperme.config import Config

logger = logging.getLogger(__name__)


class Recorder:
    """Keeps RealtimeSTT alive and opens the microphone only for active sessions."""

    def __init__(
        self,
        config: Config,
        on_realtime_update: Callable[[str], None] | None = None,
    ) -> None:
        self._config = config
        self._on_realtime_update = on_realtime_update
        self._latest_text: str = ""
        self._lock = threading.Lock()
        self._commands: queue.Queue[str] = queue.Queue()
        self._ready = threading.Event()
        self._accept_realtime = False

        self._recorder: AudioToTextRecorder | None = None
        self._session_active = False

        self._audio_interface = None
        self._audio_stream = None
        self._mic_thread: threading.Thread | None = None
        self._mic_stop = threading.Event()
        self._mic_sample_rate = 16000
        self._mic_chunk_size = 1024

    def run_worker(self) -> None:
        """Create the STT engine once, then process start/stop/shutdown commands."""
        try:
            print("[recorder] Initializing RealtimeSTT...", flush=True)
            logger.info(
                "Initializing RealtimeSTT: model=%s realtime_model=%s language=%s",
                self._config.model,
                self._config.realtime_model,
                self._config.language,
            )
            self._recorder = AudioToTextRecorder(
                model=self._config.model,
                language=self._config.language,
                use_microphone=False,
                enable_realtime_transcription=True,
                realtime_model_type=self._config.realtime_model,
                realtime_processing_pause=0.2,
                on_realtime_transcription_update=self._handle_realtime,
                on_realtime_transcription_stabilized=self._handle_realtime,
                device="cpu",
                compute_type="int8",
                spinner=False,
                silero_sensitivity=0.3,
            )
        except Exception as e:
            logger.exception("ERROR initializing recorder")
            print(f"[recorder] ERROR initializing recorder: {e}", flush=True)
            self._ready.set()
            return

        self._ready.set()
        print("[recorder] RealtimeSTT ready", flush=True)
        logger.info("RealtimeSTT ready")

        while True:
            command = self._commands.get()

            try:
                if command == "start":
                    self._start_session()
                    continue

                if command == "stop":
                    self._stop_session()
                    continue

                if command == "shutdown":
                    self._stop_session()
                    try:
                        if self._recorder is not None:
                            self._recorder.shutdown()
                    except Exception as e:
                        logger.exception("ERROR during shutdown")
                        print(f"[recorder] ERROR during shutdown: {e}", flush=True)
                    return
            except Exception:
                # Never let an unexpected error kill the worker thread.
                logger.exception("Recorder worker caught unexpected error on command=%s", command)

    def start(self) -> None:
        self._ready.wait()
        with self._lock:
            self._latest_text = ""
            self._accept_realtime = True
        self._commands.put("start")

    def stop(self) -> str:
        """Return the latest realtime text immediately and release the mic asynchronously."""
        with self._lock:
            self._accept_realtime = False
            text = self._latest_text
        self._commands.put("stop")
        return text

    def shutdown(self) -> None:
        self._commands.put("shutdown")

    def _start_session(self) -> None:
        recorder = self._recorder
        if recorder is None or self._session_active:
            return

        try:
            self._open_microphone()
            recorder.clear_audio_queue()
            recorder.start()
            self._start_microphone_feeder()
            self._session_active = True
            print("[recorder] Recording active", flush=True)
            logger.info("Session started: sample_rate=%d", self._mic_sample_rate)
        except Exception as e:
            self._close_microphone()
            logger.exception("ERROR starting session")
            print(f"[recorder] ERROR starting session: {e}", flush=True)

    def _stop_session(self) -> None:
        recorder = self._recorder
        if recorder is None:
            return

        self._close_microphone()

        if not self._session_active:
            return

        try:
            recorder.stop()
            recorder.clear_audio_queue()
            logger.info("Session stopped")
        except Exception as e:
            logger.exception("ERROR stopping session")
            print(f"[recorder] ERROR stopping session: {e}", flush=True)
        finally:
            self._session_active = False

    def _open_microphone(self) -> None:
        import pyaudio

        if self._audio_stream is not None:
            return

        audio = pyaudio.PyAudio()
        self._audio_interface = audio

        device_info = audio.get_default_input_device_info()
        sample_rates = []
        for candidate in (16000, int(device_info.get("defaultSampleRate", 16000))):
            if candidate > 0 and candidate not in sample_rates:
                sample_rates.append(candidate)

        last_error: Exception | None = None
        for sample_rate in sample_rates:
            try:
                stream = audio.open(
                    format=pyaudio.paInt16,
                    channels=1,
                    rate=sample_rate,
                    input=True,
                    frames_per_buffer=self._mic_chunk_size,
                    input_device_index=device_info["index"],
                )
                self._audio_stream = stream
                self._mic_sample_rate = sample_rate
                return
            except Exception as e:
                last_error = e

        self._audio_interface = None
        audio.terminate()
        raise RuntimeError(f"could not open microphone stream: {last_error}")

    def _start_microphone_feeder(self) -> None:
        if self._audio_stream is None or (self._mic_thread is not None and self._mic_thread.is_alive()):
            return

        self._mic_stop.clear()
        self._mic_thread = threading.Thread(target=self._feed_microphone, daemon=True)
        self._mic_thread.start()

    def _close_microphone(self) -> None:
        self._mic_stop.set()

        # The feeder thread must be fully out of stream.read() BEFORE the
        # stream is closed: pyaudio's read() runs in C with the GIL released,
        # and Pa_CloseStream frees the ring buffer it reads from. Closing
        # while a read is in flight is a use-after-free that kills the whole
        # process with SIGBUS/SIGSEGV in PaUtil_ReadRingBuffer (the "app just
        # vanished after I stopped dictating" crash). read() returns every
        # ~64 ms, so the join is quick.
        mic_thread = self._mic_thread
        if mic_thread is not None and mic_thread.is_alive():
            mic_thread.join(timeout=2)
            if mic_thread.is_alive():
                # Feeder is wedged inside PortAudio. Abandon the stream —
                # leaking it once is recoverable, freeing it under a live
                # read crashes the app.
                logger.error("Mic feeder thread did not exit; abandoning stream instead of freeing it")
                self._mic_thread = None
                self._audio_stream = None
                self._audio_interface = None
                return
        self._mic_thread = None

        stream = self._audio_stream
        self._audio_stream = None
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

        audio = self._audio_interface
        self._audio_interface = None
        if audio is not None:
            try:
                audio.terminate()
            except Exception:
                pass

    def _feed_microphone(self) -> None:
        recorder = self._recorder
        stream = self._audio_stream
        if recorder is None or stream is None:
            return

        while not self._mic_stop.is_set():
            try:
                chunk = stream.read(self._mic_chunk_size, exception_on_overflow=False)
            except Exception as e:
                if not self._mic_stop.is_set():
                    logger.exception("ERROR reading microphone")
                    print(f"[recorder] ERROR reading microphone: {e}", flush=True)
                break

            try:
                recorder.feed_audio(chunk, original_sample_rate=self._mic_sample_rate)
            except Exception as e:
                if not self._mic_stop.is_set():
                    logger.exception("ERROR feeding audio")
                    print(f"[recorder] ERROR feeding audio: {e}", flush=True)
                break

    def _handle_realtime(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return

        with self._lock:
            self._latest_text = stripped
            should_publish = self._accept_realtime

        if should_publish and self._on_realtime_update:
            self._on_realtime_update(stripped)
