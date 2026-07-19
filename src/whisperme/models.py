"""Whisper model download management.

The DMG ships without models — large-v3-turbo alone is ~1.5 GB, which would
quadruple the download for every user. Instead the models are fetched from
Hugging Face on first launch, with a progress window, so a fresh install shows
something honest instead of hanging for several minutes on a silent download
inside the RealtimeSTT subprocess.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from whisperme.paths import MODEL_CACHE_DIR

logger = logging.getLogger(__name__)

#: (bytes) rough on-disk sizes, only used to render a total before download
#: starts — the real byte counts come from the progress hook.
_APPROX_SIZES = {
    "tiny": 75_000_000,
    "tiny.en": 75_000_000,
    "base": 145_000_000,
    "base.en": 145_000_000,
    "small": 480_000_000,
    "small.en": 480_000_000,
    "medium": 1_500_000_000,
    "medium.en": 1_500_000_000,
    "large-v3-turbo": 1_600_000_000,
    "turbo": 1_600_000_000,
}

#: Called as on_progress(model_name, downloaded_bytes, total_bytes).
ProgressFn = Callable[[str, int, int], None]


def _repo_for(model: str) -> str | None:
    """Map a Whisper model name to its Hugging Face repo, or None if it's a path."""
    from faster_whisper.utils import _MODELS

    return _MODELS.get(model)


def _cache_dir() -> str | None:
    """Cache directory to pass to huggingface_hub; None selects its default."""
    return str(MODEL_CACHE_DIR) if MODEL_CACHE_DIR is not None else None


def approx_size(model: str) -> int:
    return _APPROX_SIZES.get(model, 500_000_000)


def is_cached(model: str) -> bool:
    """True when the model is already downloaded and usable offline."""
    repo = _repo_for(model)
    if repo is None:
        # A local directory path — usable if it exists.
        return Path(model).is_dir()
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(repo, cache_dir=_cache_dir(), local_files_only=True)
        return True
    except Exception:
        return False


def missing_models(models: list[str]) -> list[str]:
    """Subset of `models` that still needs downloading, de-duplicated, order kept."""
    seen: set[str] = set()
    missing: list[str] = []
    for model in models:
        if model in seen:
            continue
        seen.add(model)
        if not is_cached(model):
            missing.append(model)
    return missing


def download(model: str, on_progress: ProgressFn | None = None) -> None:
    """Download one model into the WhisperMe cache, reporting byte progress.

    Raises on failure so the caller can surface a real error to the user.
    """
    repo = _repo_for(model)
    if repo is None:
        raise ValueError(f"unknown Whisper model: {model}")

    from huggingface_hub import snapshot_download
    from tqdm.auto import tqdm as _tqdm

    # snapshot_download drives one tqdm per file. Summing `n` across live bars
    # gives overall progress; each bar's `total` is its file size.
    state = {"done": 0, "total": 0}
    bars: dict[int, tuple[int, int]] = {}

    class _ProgressTqdm(_tqdm):
        def update(self, n=1):
            result = super().update(n)
            bars[id(self)] = (int(self.n), int(self.total or 0))
            state["done"] = sum(done for done, _ in bars.values())
            state["total"] = sum(total for _, total in bars.values())
            if on_progress is not None:
                on_progress(model, state["done"], state["total"])
            return result

    logger.info("Downloading model %s from %s", model, repo)
    snapshot_download(repo, cache_dir=_cache_dir(), tqdm_class=_ProgressTqdm)
    logger.info("Model %s ready", model)


def configure_environment() -> None:
    """Make the model cache location visible to the RealtimeSTT subprocess.

    RealtimeSTT builds its WhisperModel in a subprocess we don't control, so an
    overridden cache location has to travel as an environment variable the child
    inherits. With no override we leave HF's defaults alone, so models already
    in ~/.cache/huggingface are reused rather than downloaded again.
    """
    import os

    if MODEL_CACHE_DIR is not None:
        MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HUB_CACHE", str(MODEL_CACHE_DIR))
