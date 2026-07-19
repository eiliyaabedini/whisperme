# WhisperMe

Push-to-talk voice dictation for macOS that runs entirely on your Mac.

Hold **⌥ /**, talk, release. WhisperMe transcribes what you said and pastes it
into whatever app you were already typing in — Slack, your editor, a browser
text box, anywhere. No account, no API key, and after the first launch, no
internet.

### [⬇ Download the latest release](https://github.com/eiliyaabedini/whisperme/releases/latest)

---

## Install

1. Download `WhisperMe-x.y.z.dmg` from the [releases page](https://github.com/eiliyaabedini/whisperme/releases/latest).
2. Open it and drag **WhisperMe** onto **Applications**.
3. Open WhisperMe from your Applications folder.

WhisperMe has no Dock icon — it lives in the menu bar as a 🎤 icon. That icon is
where you'll find its settings, logs, and the quit button.

> **macOS says WhisperMe "is damaged and can't be opened"?**
> It isn't. Current builds aren't notarized by Apple yet, and that's the message
> Gatekeeper shows for any app it can't verify. Clear the quarantine flag:
> ```bash
> xattr -dr com.apple.quarantine /Applications/WhisperMe.app
> ```
> Then open it normally. See [Troubleshooting](#troubleshooting) for context.

### First launch

Two things happen once, on the first run:

- **Speech models download** (~1.6 GB) with a progress window. They're cached in
  `~/.cache/huggingface`, shared with any other Whisper tooling you use, and
  never downloaded again.
- **A permission wizard** walks you through granting Microphone and
  Accessibility access in System Settings. Accessibility is what lets WhisperMe
  see the ⌥/ hotkey and paste for you — macOS has no way to do either without it.

You can re-run the wizard any time from the menu bar icon → **Permission Setup**.

## Using it

| Shortcut | What it does |
| --- | --- |
| **⌥ /** | Start recording; press again to stop, transcribe, and paste |
| **⌥ .** or **⌥ X** | Cancel — discard the recording, paste nothing |
| **⌥ R** | Reset — clear what's been said and keep recording |

While you're talking, a small panel appears near your cursor showing a live
waveform and the transcription as it forms.

You can also steer it by voice, without touching the keyboard. Say **"whisperme
cancel"**, **"whisperme reset"**, or **"whisperme done"** — or just double a verb,
like "cancel cancel". These only count at the end of what you've said, and have
to be heard twice in a row, so saying the word mid-sentence won't trigger them.

## Privacy

Everything runs locally. Audio is transcribed on your Mac by
[Whisper](https://github.com/openai/whisper) (via
[faster-whisper](https://github.com/SYSTRAN/faster-whisper)) and never leaves it.
The only network request WhisperMe ever makes is downloading the models on first
launch.

The microphone is opened when you start dictating and closed when you stop — it
is not held open in the background.

If your Mac supports Apple Intelligence, transcripts are additionally cleaned up
(grammar, punctuation, filler words) by Apple's on-device Foundation Model, which
is also local. On Macs without it, this step is skipped automatically.

## Requirements

- **Apple Silicon** Mac (M1 or newer)
- **macOS 12** Monterey or later
- ~1 GB disk for the app, ~1.6 GB for the speech models
- Apple Intelligence, *optionally*, for transcript cleanup

## Troubleshooting

**"WhisperMe is damaged and can't be opened."**
The app isn't damaged and isn't corrupted in transit. macOS quarantines
everything downloaded from the internet, and shows this specific wording for
apps without an Apple-notarized signature. Notarization requires a paid Apple
Developer ID; until these builds are notarized, clear the flag by hand:
```bash
xattr -dr com.apple.quarantine /Applications/WhisperMe.app
```

**The ⌥/ hotkey does nothing.**
Accessibility permission is missing or was invalidated. Open the menu bar icon →
**Permission Setup**. If it still fails, macOS occasionally also requires *Input
Monitoring* — the wizard will tell you and open the right pane.

**Permissions keep getting asked for after an update.**
Reinstalling changes the app's code signature, and macOS ties permissions to it.
Reset them so the wizard can re-register cleanly:
```bash
tccutil reset Microphone com.eiliya.whisperme
tccutil reset Accessibility com.eiliya.whisperme
```

**It won't start / something looks wrong.**
Logs are in `~/Library/Logs/WhisperMe/` (menu bar icon → **Open Logs Folder**).
`whisperme.log` has the detail; `crash.log` captures hard crashes. Those files
are the useful thing to attach to a bug report.

**Only one instance runs at a time.** Launching a second copy shows an "already
running" alert instead — look for the existing icon in the menu bar.

## Building from source

Requires [uv](https://docs.astral.sh/uv/) and Xcode Command Line Tools.

```bash
git clone https://github.com/eiliyaabedini/whisperme.git
cd whisperme

uv run whisperme                 # run from the checkout
scripts/install.sh --open        # build + install to /Applications
```

Running via `uv run` attaches permissions to your terminal app rather than to
WhisperMe, which is usually what you want while developing.

### Packaging a release

```bash
scripts/build-app.sh             # -> dist/WhisperMe.app  (self-contained, ~900 MB)
scripts/make-dmg.sh              # -> dist/WhisperMe-x.y.z.dmg
scripts/release.sh 0.2.0         # bump, build, tag, publish to GitHub Releases
```

The app bundle carries its own CPython and every dependency, installed from
`uv.lock` — never a fresh resolve, because RealtimeSTT 1.x drops the
faster-whisper backend this app depends on. Whisper models are deliberately not
bundled; they're fetched on first launch instead.

To produce a DMG other people can open without the quarantine dance, set a
Developer ID identity and a notarization profile before building:

```bash
# One-time: create a "Developer ID Application" certificate in Xcode
#   (Settings > Accounts > Manage Certificates > + ), then store notary creds:
xcrun notarytool store-credentials whisperme-notary \
    --apple-id you@example.com --team-id TEAMID --password APP-SPECIFIC-PASSWORD

export WHISPERME_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)"
export WHISPERME_NOTARY_PROFILE="whisperme-notary"
scripts/release.sh 0.2.0
```

Pushing a `v*` tag runs the same build on GitHub Actions
(`.github/workflows/release.yml`); it signs and notarizes when the corresponding
repository secrets are configured, and falls back to an unsigned build when
they're not.

Architecture notes for contributors live in [CLAUDE.md](CLAUDE.md).
