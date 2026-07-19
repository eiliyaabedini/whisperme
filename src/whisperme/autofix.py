from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from whisperme.paths import LOG_DIR, REPO_DIR

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 15 * 60

# Stable install locations first; PATH lookup last (terminal sessions may
# expose transient shims that the app won't have).
_CLAUDE_CANDIDATES = (
    Path.home() / ".local" / "bin" / "claude",
    Path.home() / ".claude" / "local" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
)

# Scoped permissions: read anything, edit the repo, verify, and use git for
# exactly the commit-and-push flow. No arbitrary shell.
_ALLOWED_TOOLS = ",".join(
    [
        "Read",
        "Grep",
        "Glob",
        "Edit",
        "MultiEdit",
        "Write",
        "Bash(git status:*)",
        "Bash(git diff:*)",
        "Bash(git log:*)",
        "Bash(git add:*)",
        "Bash(git commit:*)",
        "Bash(git push:*)",
        "Bash(git rev-parse:*)",
        "Bash(uv run:*)",
        "Bash(ls:*)",
        "Bash(tail:*)",
    ]
)

_PROMPT = """\
You are WhisperMe's automated repair agent ("Auto-Fix"), launched headlessly from \
the app's menu bar icon. WhisperMe is a local push-to-talk dictation app for macOS; \
this working directory is its source checkout, and the installed app runs directly \
from this checkout, so a pushed fix takes effect on the next app launch.

Goal: determine whether WhisperMe recently crashed or misbehaved, fix the root \
cause in the source, and ship the fix.

Investigate first (read-only):
1. logs/whisperme.log — app log. A "=== SESSION START ===" line with no matching \
"=== SESSION END ===" means that run died hard. Look for ERROR/CRITICAL entries and \
tracebacks near the ends of recent sessions.
2. logs/crash.log — faulthandler output from native crashes (C-level fatal signals \
with per-thread Python stacks; the "Current thread" is the one that crashed).
3. logs/crashes/*.ips — preserved macOS crash reports (JSON; check exception type \
and the faulting thread's frames).
4. logs/whisperme_stdout.log — prints and third-party warnings.
Focus on the MOST RECENT failure. Ignore problems that later sessions show as \
already fixed (check git log for recent fixes before re-fixing something).

Then:
- If you find a real, current defect: implement the smallest correct fix in \
src/whisperme/. For environmental failures (device unplugged, model missing), \
prefer defensive handling plus clearer logging over speculative rewrites.
- Verify the app still imports: uv run python -c "import whisperme.__main__"
- Commit ONLY the files you changed (git add those specific paths — the tree may \
contain unrelated uncommitted work; leave it alone) with a message starting \
"autofix:", then git push.
- If you find NO real recent failure, change nothing, commit nothing, and say so.

Your final reply is shown to the user in a small dialog. Write 3-6 plain sentences: \
what you found (or that nothing was wrong), what you changed, whether you committed \
and pushed, and whether the app needs a restart to pick up the fix.
"""


@dataclass
class AutoFixResult:
    ok: bool
    summary: str
    report_path: Path | None
    committed: bool


def find_claude() -> str | None:
    for candidate in _CLAUDE_CANDIDATES:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which("claude")


def is_available() -> bool:
    """Auto-Fix only makes sense when running from a source checkout.

    It edits src/ and pushes to the whisperme remote, so a downloaded .app has
    nothing to repair — REPO_DIR is None there and the menu item stays hidden.
    """
    return REPO_DIR is not None and find_claude() is not None


def _git_head() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_DIR,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        return ""


class AutoFixer:
    """Runs a headless Claude Code session that diagnoses and fixes the app."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self, on_done: Callable[[AutoFixResult], None]) -> bool:
        """Kick off an auto-fix in a background thread. False if one is active."""
        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(target=self._run, args=(on_done,), daemon=True).start()
        return True

    def _run(self, on_done: Callable[[AutoFixResult], None]) -> None:
        result = AutoFixResult(
            ok=False, summary="Auto-fix did not run.", report_path=None, committed=False
        )
        try:
            result = self._run_claude()
        except Exception as e:
            logger.exception("Auto-fix crashed")
            result = AutoFixResult(
                ok=False,
                summary=f"Auto-fix crashed unexpectedly: {e}",
                report_path=None,
                committed=False,
            )
        finally:
            with self._lock:
                self._running = False
            try:
                on_done(result)
            except Exception:
                logger.exception("Auto-fix completion callback failed")

    def _run_claude(self) -> AutoFixResult:
        claude = find_claude()
        if claude is None:
            return AutoFixResult(
                ok=False,
                summary=(
                    "The claude CLI was not found. Install Claude Code "
                    "(https://claude.com/claude-code) and try again."
                ),
                report_path=None,
                committed=False,
            )

        report_dir = LOG_DIR / "autofix"
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_path = report_dir / f"autofix-{stamp}.md"

        # Don't inherit nested-session markers if the app was started from a
        # terminal that itself runs inside Claude Code.
        env = {
            k: v
            for k, v in os.environ.items()
            if k != "CLAUDECODE" and not k.startswith("CLAUDE_CODE_")
        }

        head_before = _git_head()
        cmd = [
            claude,
            "-p",
            _PROMPT,
            "--allowedTools",
            _ALLOWED_TOOLS,
            "--max-turns",
            "60",
        ]
        logger.info("Auto-fix starting: %s (report: %s)", claude, report_path)

        try:
            proc = subprocess.run(
                cmd,
                cwd=REPO_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
            )
        except subprocess.TimeoutExpired:
            summary = f"Auto-fix timed out after {_TIMEOUT_SECONDS // 60} minutes."
            report_path.write_text(f"# Auto-fix {stamp}\n\n{summary}\n")
            return AutoFixResult(False, summary, report_path, _git_head() != head_before)

        committed = _git_head() != head_before
        summary = proc.stdout.strip() or "(no output from claude)"
        report_path.write_text(
            f"# Auto-fix {stamp}\n\n"
            f"- exit code: {proc.returncode}\n"
            f"- committed: {committed}\n\n"
            f"## Claude's report\n\n{summary}\n\n"
            f"## stderr\n\n```\n{proc.stderr.strip()}\n```\n"
        )
        logger.info(
            "Auto-fix finished: exit=%d committed=%s", proc.returncode, committed
        )
        return AutoFixResult(proc.returncode == 0, summary, report_path, committed)
