# whisperme

Local push-to-talk voice dictation for macOS. Press Option+/ to record, release to transcribe and paste.

## Architecture

- `src/whisperme/app.py` — Orchestrator: hotkey -> recorder -> overlay -> paster
- `src/whisperme/recorder.py` — Wraps RealtimeSTT with manual mic feeding (`use_microphone=False`)
- `src/whisperme/overlay.py` — Floating NSPanel (AppKit) showing live transcription
- `src/whisperme/hotkey.py` — Quartz CGEventTap for global hotkeys: ⌥/ toggle; ⌥X cancel and ⌥R reset consume the key only while recording (pass through otherwise)
- `src/whisperme/paster.py` — pbcopy + simulated Cmd+V via pynput
- `src/whisperme/postprocess.py` — Optional Apple Foundation Model cleanup
- `src/whisperme/statusbar.py` — NSStatusItem menu bar icon (idle/recording/processing states, quit, start-at-login)
- `src/whisperme/permissions.py` — Interactive Microphone + Accessibility setup wizard (NSAlert flow)
- `src/whisperme/autofix.py` — "Auto-Fix Recent Issues" menu action: runs `claude -p` headlessly (scoped --allowedTools) to diagnose recent crashes from logs/, fix src/, commit + push; report saved to logs/autofix/
- `src/whisperme/autostart.py` — Start-at-login LaunchAgent (~/Library/LaunchAgents/com.eiliya.whisperme.plist)
- `src/whisperme/voice_commands.py` — Spoken commands in the live transcription: "whisperme cancel/reset/done" (wake word + adjacent verb) or a doubled verb like "cancel cancel"; end-anchored, and app.py requires the match in two consecutive realtime updates before firing
- `src/whisperme/paths.py` — Shared REPO_DIR/LOG_DIR + app bundle discovery
- `src/whisperme/config.py` — CLI args (--model, --realtime-model, --language, --no-llm, --no-setup)

## Running

```
uv run whisperme            # from a terminal (permissions attach to the terminal app)
```

## Installing as a macOS app

```
scripts/install.sh [--open]
```

Builds `dist/WhisperMe.app` and installs it to /Applications. The bundle's main
executable is a small compiled stub (`scripts/launcher.c`, paths baked in at build
time) that spawns `uv run whisperme` as a child and stays resident. It MUST stay
resident: macOS attributes TCC permissions (Microphone/Accessibility) to the
bundle's responsible process, and children inherit it. An earlier version exec'd
into uv, which made TCC register/check the "uv" binary instead of WhisperMe —
grants to WhisperMe.app never matched and permission prompts looped forever.
The bundle has its own identifier (`com.eiliya.whisperme`) and `LSUIElement=true`
(menu bar only, no Dock icon). First launch runs the interactive permission
wizard; it can be re-run from the menu bar icon → Permission Setup. Rebuilding
changes the ad-hoc code signature; if permissions act stale after a reinstall,
clear them with `tccutil reset Accessibility com.eiliya.whisperme` (and
`Microphone`) so the wizard re-registers cleanly.

Only one instance runs at a time (flock on `logs/whisperme.lock`); a second
launch shows an "already running" alert and exits.

## Logs

Logs are written to `logs/` in the project root:
- `whisperme.log` — Python logging output (rotated at 5 MB, 3 backups)
- `whisperme_stdout.log` — stdout/stderr print output

## Key details

- RealtimeSTT runs in a subprocess with multiprocessing pipes — EOFError on those pipes means the connection died
- Overlay runs on the main thread (AppKit event loop); recorder runs on a daemon thread
- Mic is opened/closed per session, not kept open
