from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

_PROMPT = (
    "Fix grammar, punctuation, and remove filler words (um, uh, like, you know). "
    "Return ONLY the corrected text, nothing else:\n\n"
)


async def _cleanup_async(text: str) -> str:
    import apple_fm_sdk as fm

    model = fm.SystemLanguageModel()
    available, reason = model.is_available()
    logger.debug("Apple FM availability check: available=%s reason=%s", available, reason)
    if not available:
        print(f"[whisperme] Apple FM not available: {reason}")
        return text

    session = fm.LanguageModelSession()
    response = await session.respond(_PROMPT + text)
    return str(response).strip()


def cleanup(text: str) -> str:
    """Run Apple Foundation Model to clean up transcription. Returns original on failure."""
    if not text.strip():
        return text
    start = time.monotonic()
    logger.debug("cleanup() called: input_len=%d", len(text))
    try:
        result = asyncio.run(_cleanup_async(text))
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "cleanup() ok: input_len=%d output_len=%d elapsed_ms=%.1f",
            len(text),
            len(result),
            elapsed_ms,
        )
        return result
    except Exception as e:
        logger.exception("cleanup() failed, returning raw text")
        print(f"[whisperme] LLM cleanup failed ({e}), using raw text")
        return text
