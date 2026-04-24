from __future__ import annotations

import asyncio

_PROMPT = (
    "Fix grammar, punctuation, and remove filler words (um, uh, like, you know). "
    "Return ONLY the corrected text, nothing else:\n\n"
)


async def _cleanup_async(text: str) -> str:
    import apple_fm_sdk as fm

    model = fm.SystemLanguageModel()
    available, reason = model.is_available()
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
    try:
        return asyncio.run(_cleanup_async(text))
    except Exception as e:
        print(f"[whisperme] LLM cleanup failed ({e}), using raw text")
        return text
