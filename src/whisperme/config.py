from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass
class Config:
    model: str = "large-v3-turbo"
    realtime_model: str = "base.en"
    language: str = "en"
    no_llm: bool = False

    @classmethod
    def from_args(cls) -> Config:
        parser = argparse.ArgumentParser(
            prog="whisperme",
            description="Local push-to-talk voice dictation for macOS",
        )
        parser.add_argument(
            "--model",
            default="large-v3-turbo",
            help="Whisper model for final transcription (default: large-v3-turbo)",
        )
        parser.add_argument(
            "--realtime-model",
            default="base.en",
            help="Whisper model for live preview (default: base.en)",
        )
        parser.add_argument(
            "--language",
            default="en",
            help="Language code (default: en)",
        )
        parser.add_argument(
            "--no-llm",
            action="store_true",
            help="Disable LLM post-processing",
        )
        args = parser.parse_args()
        return cls(
            model=args.model,
            realtime_model=args.realtime_model,
            language=args.language,
            no_llm=args.no_llm,
        )
