import logging
import sys
import threading
from pathlib import Path

from whisperme.config import Config
from whisperme.app import App

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

logger = logging.getLogger(__name__)


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

    # Also redirect stdout/stderr prints to the log file
    log_file = open(LOG_DIR / "whisperme_stdout.log", "a")
    sys.stdout = log_file
    sys.stderr = log_file


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


def main() -> None:
    _setup_logging()
    _install_excepthooks()
    print(f"[whisperme] Logs are saved to {LOG_DIR}", flush=True)
    logger.info("whisperme starting")
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
        logger.info("whisperme exited")


if __name__ == "__main__":
    main()
