import logging
import sys
from pathlib import Path

from whisperme.config import Config
from whisperme.app import App

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


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


def main() -> None:
    _setup_logging()
    print(f"[whisperme] Logs are saved to {LOG_DIR}", flush=True)
    config = Config.from_args()
    app = App(config)
    try:
        app.run()
    except KeyboardInterrupt:
        app.shutdown()


if __name__ == "__main__":
    main()
