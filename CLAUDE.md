# whisperme

Local push-to-talk voice dictation for macOS. Press Option+/ to record, release to transcribe and paste.

## Architecture

- `src/whisperme/app.py` — Orchestrator: hotkey -> recorder -> overlay -> paster
- `src/whisperme/recorder.py` — Wraps RealtimeSTT with manual mic feeding (`use_microphone=False`)
- `src/whisperme/overlay.py` — Floating NSPanel (AppKit) showing live transcription
- `src/whisperme/hotkey.py` — Quartz CGEventTap for global hotkeys: ⌥/ toggle; ⌥. or ⌥X cancel and ⌥R reset consume the key only while recording (pass through otherwise)
- `src/whisperme/paster.py` — pbcopy + simulated Cmd+V via pynput
- `src/whisperme/postprocess.py` — Optional Apple Foundation Model cleanup
- `src/whisperme/statusbar.py` — NSStatusItem menu bar icon (idle/recording/processing states, quit, start-at-login)
- `src/whisperme/permissions.py` — Interactive Microphone + Accessibility setup wizard (NSAlert flow)
- `src/whisperme/autofix.py` — "Auto-Fix Recent Issues" menu action: runs `claude -p` headlessly (scoped --allowedTools) to diagnose recent crashes from logs/, fix src/, commit + push; report saved to logs/autofix/
- `src/whisperme/autostart.py` — Start-at-login LaunchAgent (~/Library/LaunchAgents/com.eiliya.whisperme.plist)
- `src/whisperme/voice_commands.py` — Spoken commands in the live transcription: "whisperme cancel/reset/done" (wake word + adjacent verb) or a doubled verb like "cancel cancel"; end-anchored, and app.py requires the match in two consecutive realtime updates before firing
- `src/whisperme/paths.py` — IS_BUNDLED / REPO_DIR / LOG_DIR + app bundle discovery
- `src/whisperme/models.py` — Whisper model cache checks + downloads (models ship separately from the app)
- `src/whisperme/download_window.py` — First-run AppKit progress window driving those downloads
- `src/whisperme/config.py` — CLI args (--model, --realtime-model, --language, --no-llm, --no-setup)

### Checkout vs. distributed bundle

`paths.IS_BUNDLED` distinguishes the two layouts, detected by whether
`pyproject.toml` sits above the package. It drives two behaviours:

- **Logs** — `logs/` in the checkout, `~/Library/Logs/WhisperMe/` in a bundle
  (a downloaded .app has no writable checkout to log into).
- **Auto-Fix** — `REPO_DIR` is None in a bundle, so `autofix.is_available()` is
  False and statusbar.py omits the menu item entirely. It edits src/ and pushes,
  which is meaningless for someone who installed from a DMG.

## Running

```
uv run whisperme            # from a terminal (permissions attach to the terminal app)
```

## Installing as a macOS app

```
scripts/install.sh [--open]     # build + install to /Applications
scripts/build-app.sh            # dist/WhisperMe.app only
scripts/make-dmg.sh             # dist/WhisperMe-<version>.dmg
scripts/release.sh 0.2.0        # bump, build, tag, publish to GitHub Releases
```

`dist/WhisperMe.app` is fully self-contained (~900 MB): it carries its own
relocatable CPython in `Contents/Resources/python` with every dependency
installed into it, so it needs no uv, no Homebrew and no checkout. The DMG
compresses to ~250 MB.

The bundle's main executable is a small compiled stub (`scripts/launcher.c`)
that spawns `Contents/Resources/python/bin/python3.12 -m whisperme` as a child
and stays resident. It MUST stay resident: macOS attributes TCC permissions
(Microphone/Accessibility) to the bundle's responsible process, and children
inherit it. An earlier version exec'd into uv, which made TCC register/check the
"uv" binary instead of WhisperMe — grants to WhisperMe.app never matched and
permission prompts looped forever. The stub resolves the interpreter relative to
its own location, so the bundle works from anywhere.

The bundle has its own identifier (`com.eiliya.whisperme`) and `LSUIElement=true`
(menu bar only, no Dock icon). First launch runs the interactive permission
wizard; it can be re-run from the menu bar icon → Permission Setup. Rebuilding
changes the code signature; if permissions act stale after a reinstall, clear
them with `tccutil reset Accessibility com.eiliya.whisperme` (and `Microphone`)
so the wizard re-registers cleanly.

Only one instance runs at a time (flock on `whisperme.lock` in LOG_DIR); a second
launch shows an "already running" alert and exits.

### Build invariants

- **Install from `uv.lock`, never a fresh resolve.** `pyproject.toml` pins
  nothing, and a fresh resolve picks RealtimeSTT 1.x, which drops
  faster-whisper/ctranslate2 — producing an app that cannot transcribe at all.
  `build-app.sh` uses `uv export --frozen`.
- **Precompile the whole interpreter tree and set `PYTHONDONTWRITEBYTECODE=1`.**
  A `.pyc` written at runtime lands inside the bundle and invalidates its code
  signature ("a sealed resource is missing or invalid"), which makes a notarized
  build start failing Gatekeeper after its first launch.
- **Trim conservatively.** `torch/bin` looks like build tooling but holds
  `torch_shm_manager`, which torch resolves at import time and refuses to start
  without.
- **Sign inner-first.** Nested Mach-O binaries must be signed before the outer
  bundle; `scripts/sign-app.sh` does this and applies
  `scripts/entitlements.plist` (library validation must be disabled — CPython
  dlopen()s hundreds of .so files not signed by our team).

Signing/notarization are opt-in via `WHISPERME_SIGN_IDENTITY` and
`WHISPERME_NOTARY_PROFILE`; unset, builds are ad-hoc signed and macOS blocks
downloaded copies until quarantine is cleared.

## Logs

`LOG_DIR` is `logs/` in a checkout, `~/Library/Logs/WhisperMe/` in a bundle:
- `whisperme.log` — Python logging output (rotated at 5 MB, 3 backups)
- `whisperme_stdout.log` — stdout/stderr print output

## Key details

- RealtimeSTT runs in a subprocess with multiprocessing pipes — EOFError on those pipes means the connection died
- Overlay runs on the main thread (AppKit event loop); recorder runs on a daemon thread
- Mic is opened/closed per session, not kept open
- Models are downloaded on first launch, before the recorder thread starts — RealtimeSTT would otherwise fetch them inside its subprocess with no UI, looking like a multi-minute hang. They land in the standard HF cache (`~/.cache/huggingface`) so existing downloads are reused; `WHISPERME_MODEL_DIR` overrides it.
