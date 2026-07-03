import faulthandler
import logging
import os
import shutil
import sys
import threading
from pathlib import Path

from whisperme.config import Config
from whisperme.app import App

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

logger = logging.getLogger(__name__)

# Kept alive for the whole process: faulthandler writes to the crash log from C
# on a fatal signal, so its file descriptor must never be closed or GC'd.
_crash_log_fp = None
_stdout_log_fp = None


def _roll_if_large(path: Path, max_bytes: int = 5 * 1024 * 1024) -> None:
    """One-step rotation for files not managed by RotatingFileHandler."""
    try:
        if path.exists() and path.stat().st_size > max_bytes:
            backup = path.with_suffix(path.suffix + ".1")
            backup.unlink(missing_ok=True)
            path.rename(backup)
    except OSError:
        pass


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    from logging.handlers import RotatingFileHandler

    file_handler = RotatingFileHandler(
        LOG_DIR / "whisperme.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
    )
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )

    # Capture all loggers (including RealtimeSTT's root logger)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    # Silence extremely chatty third-party DEBUG loggers. RealtimeSTT.safepipe
    # alone emits ~50k poll() lines per 5 MB log, which fills the rotation and
    # discards real crash context within minutes. Keep them at WARNING so the
    # log stays useful for diagnosing the next crash.
    for noisy in (
        "RealtimeSTT.safepipe",
        "httpcore",
        "httpx",
        "urllib3",
        "filelock",
        "huggingface_hub",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Native crashes (segfault / abort inside PortAudio, the RealtimeSTT
    # subprocess, AppKit, or Apple FM) kill the process with a signal and NEVER
    # reach Python's excepthook. faulthandler is the only thing that captures a
    # C-level stack trace at the moment of the fatal signal. It writes straight
    # to this file's fd, so the fd must stay open for the whole process and the
    # file must be line-buffered so nothing is lost when the process dies.
    global _crash_log_fp, _stdout_log_fp
    _roll_if_large(LOG_DIR / "crash.log")
    _crash_log_fp = open(LOG_DIR / "crash.log", "a", buffering=1)
    faulthandler.enable(file=_crash_log_fp, all_threads=True)

    # Redirect stdout/stderr (third-party prints, pyobjc warnings) to a capped,
    # line-buffered file so nothing is lost but it cannot grow without bound.
    _roll_if_large(LOG_DIR / "whisperme_stdout.log")
    _stdout_log_fp = open(LOG_DIR / "whisperme_stdout.log", "a", buffering=1)
    sys.stdout = _stdout_log_fp
    sys.stderr = _stdout_log_fp


def _install_excepthooks() -> None:
    """Ensure any uncaught exception lands in whisperme.log before the process dies."""

    def _main_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            # Let Ctrl+C behave normally
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logger.critical(
            "Uncaught exception on main thread",
            exc_info=(exc_type, exc_value, exc_tb),
        )

    def _thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, SystemExit):
            return
        logger.critical(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "<unknown>",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _main_excepthook
    threading.excepthook = _thread_excepthook


def _scan_previous_crashes() -> None:
    """Preserve macOS native crash reports (.ips) from earlier runs.

    A native crash kills us before any Python handler runs, but macOS writes an
    .ips report to ~/Library/Logs/DiagnosticReports. Copy ours into logs/crashes/
    on the next launch so the post-mortem survives even if macOS later prunes it.
    """
    reports_dir = Path.home() / "Library" / "Logs" / "DiagnosticReports"
    if not reports_dir.is_dir():
        return
    try:
        recent = sorted(
            reports_dir.glob("*.ips"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:50]
    except OSError:
        return

    crash_dir = LOG_DIR / "crashes"
    found = 0
    for src in recent:
        try:
            head = src.read_text(errors="ignore")[:16384]
        except OSError:
            continue
        # Only ours — the report embeds the executable path / command line.
        if "whisperme" not in head:
            continue
        dst = crash_dir / src.name
        if dst.exists():
            continue
        try:
            crash_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError:
            continue
        found += 1
        logger.warning("Preserved native crash report from a previous run: %s", src.name)
    if found:
        logger.warning(
            "%d native crash report(s) copied to %s — a previous run crashed natively",
            found,
            crash_dir,
        )


def main() -> None:
    _setup_logging()
    _install_excepthooks()
    print(f"[whisperme] Logs are saved to {LOG_DIR}", flush=True)
    logger.info("=== SESSION START (pid=%d) argv=%s ===", os.getpid(), sys.argv[1:])
    _scan_previous_crashes()
    try:
        config = Config.from_args()
        app = App(config)
        try:
            app.run()
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt, shutting down")
            app.shutdown()
        except Exception:
            logger.exception("Fatal error in app.run(), attempting shutdown")
            try:
                app.shutdown()
            except Exception:
                logger.exception("Error during emergency shutdown")
            raise
    except Exception:
        logger.exception("whisperme terminating with unhandled exception")
        raise
    finally:
        # If you ever see a SESSION START with no matching SESSION END, the
        # process was killed by a native signal — check logs/crash.log and
        # logs/crashes/ for the stack trace and macOS report.
        logger.info("=== SESSION END (pid=%d) ===", os.getpid())


if __name__ == "__main__":
    main()
