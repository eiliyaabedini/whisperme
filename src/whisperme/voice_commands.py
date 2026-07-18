from __future__ import annotations

import re

# "whisper me", "whisper-me", "WhisperMe" … -> canonical wake word
_WAKE_RE = re.compile(r"whisper[\s\-]*me\b", re.IGNORECASE)

_COMMANDS = {
    "cancel": {"cancel", "close", "discard", "abort"},
    "reset": {"reset", "restart", "clear"},
    "paste": {"done", "paste", "send", "finish"},
}
_ALL_VERBS = set().union(*_COMMANDS.values())

# Words allowed between the wake word and the verb ("cancel it whisperme")
_FILLERS = {"it", "please", "now", "the", "this"}

# Only the last few words of the live transcription are considered, so the
# command has to be the thing you just said.
_TAIL_WORDS = 6

# Saying the verb twice ("cancel cancel") also works, without the wake word —
# Whisper sometimes mishears "whisperme". "done" is excluded because
# "done, done" occurs in natural speech.
_DOUBLE_VERBS = _ALL_VERBS - {"done"}

_VERBS_PATTERN = "|".join(sorted(_ALL_VERBS))
_STRIP_RE = re.compile(
    rf"(?:(?:{_VERBS_PATTERN})[\s,.!?]*)?"
    rf"(?:(?:it|please|now|this)[\s,.!?]*)*"
    rf"whisper[\s\-]*me"
    rf"(?:[\s,.!?]*(?:it|please|now|this))*"
    rf"(?:[\s,.!?]*(?:{_VERBS_PATTERN}))?"
    rf"[\s,.!?]*$",
    re.IGNORECASE,
)
_STRIP_DOUBLE_RE = re.compile(
    rf"[,\s]*({_VERBS_PATTERN})\b[\s,.!?]+\1[\s,.!?]*$",
    re.IGNORECASE,
)


def _verb_command(word: str) -> str | None:
    for command, verbs in _COMMANDS.items():
        if word in verbs:
            return command
    return None


def match(text: str) -> str | None:
    """Detect a spoken command at the tail of the live transcription.

    Two forms: wake word with a command verb directly adjacent (fillers like
    "it"/"please" allowed in between) — "whisperme cancel", "cancel it
    whisperme", "whisperme done" — or a doubled verb without the wake word,
    e.g. "cancel cancel". Returns "cancel", "reset", "paste", or None.
    """
    norm = _WAKE_RE.sub(" whisperme ", text.lower())
    norm = re.sub(r"[^a-z\s]", " ", norm)
    words = norm.split()
    tail = words[-_TAIL_WORDS:]

    def _end_anchored(last_index: int) -> bool:
        # The command must be what the utterance ENDS with — at most one
        # trailing filler word after it. Guards against firing on sentences
        # that merely pass through the phrase ("…whisperme cancel button…").
        trailing = tail[last_index + 1 :]
        return len(trailing) <= 1 and all(t in _FILLERS for t in trailing)

    for i, word in enumerate(tail):
        if word != "whisperme":
            continue
        # verb after the wake word: "whisperme cancel"
        j = i + 1
        while j < len(tail) and tail[j] in _FILLERS:
            j += 1
        if j < len(tail):
            command = _verb_command(tail[j])
            if command and _end_anchored(j):
                return command
        # verb before the wake word: "cancel it whisperme"
        k = i - 1
        while k >= 0 and tail[k] in _FILLERS:
            k -= 1
        if k >= 0:
            command = _verb_command(tail[k])
            if command and _end_anchored(i):
                return command

    # Doubled verb without the wake word: "cancel cancel"
    trimmed = list(tail)
    if trimmed and trimmed[-1] in _FILLERS:
        trimmed.pop()
    if len(trimmed) >= 2 and trimmed[-1] == trimmed[-2] and trimmed[-1] in _DOUBLE_VERBS:
        return _verb_command(trimmed[-1])
    return None


def strip_tail(text: str) -> str:
    """Remove the trailing spoken command ("…, whisperme done" or a doubled
    verb like "send send") from the text that is about to be pasted."""
    stripped = _STRIP_RE.sub("", text)
    if stripped == text:
        stripped = _STRIP_DOUBLE_RE.sub("", text)
    return stripped.rstrip(" ,;")
