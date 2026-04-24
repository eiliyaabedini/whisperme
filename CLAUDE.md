# whisperme

Local push-to-talk voice dictation for macOS. Press Option+/ to record, release to transcribe and paste.

## Architecture

- `src/whisperme/app.py` — Orchestrator: hotkey -> recorder -> overlay -> paster
- `src/whisperme/recorder.py` — Wraps RealtimeSTT with manual mic feeding (`use_microphone=False`)
- `src/whisperme/overlay.py` — Floating NSPanel (AppKit) showing live transcription
- `src/whisperme/hotkey.py` — Quartz CGEventTap for global Option+/ hotkey
- `src/whisperme/paster.py` — pbcopy + simulated Cmd+V via pynput
- `src/whisperme/postprocess.py` — Optional Apple Foundation Model cleanup
- `src/whisperme/config.py` — CLI args (--model, --realtime-model, --language, --no-llm)

## Running

```
uv run whisperme
```

## Logs

Logs are written to `logs/` in the project root:
- `whisperme.log` — Python logging output (rotated at 5 MB, 3 backups)
- `whisperme_stdout.log` — stdout/stderr print output

## Key details

- RealtimeSTT runs in a subprocess with multiprocessing pipes — EOFError on those pipes means the connection died
- Overlay runs on the main thread (AppKit event loop); recorder runs on a daemon thread
- Mic is opened/closed per session, not kept open
