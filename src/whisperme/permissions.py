from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import AppKit

logger = logging.getLogger(__name__)

_MIC_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
_AX_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
_INPUT_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent"

# AVAuthorizationStatus values
_AV_NOT_DETERMINED = 0
_AV_AUTHORIZED = 3


@dataclass
class PermissionsReport:
    microphone: bool
    accessibility: bool

    @property
    def all_granted(self) -> bool:
        return self.microphone and self.accessibility


def microphone_status() -> str:
    """Return 'granted', 'undetermined', or 'denied' for microphone access."""
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

    status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio)
    if status == _AV_AUTHORIZED:
        return "granted"
    if status == _AV_NOT_DETERMINED:
        return "undetermined"
    return "denied"


def accessibility_trusted() -> bool:
    from ApplicationServices import AXIsProcessTrusted

    return bool(AXIsProcessTrusted())


def check_silently() -> PermissionsReport:
    """Non-interactive snapshot of the permissions we need."""
    return PermissionsReport(
        microphone=microphone_status() == "granted",
        accessibility=accessibility_trusted(),
    )


def _pump_run_loop(seconds: float) -> None:
    """Keep the (pre-app-loop) main run loop alive while waiting."""
    AppKit.NSRunLoop.currentRunLoop().runMode_beforeDate_(
        AppKit.NSDefaultRunLoopMode,
        AppKit.NSDate.dateWithTimeIntervalSinceNow_(seconds),
    )


def _request_microphone(timeout: float = 180.0) -> bool:
    """Trigger the system microphone prompt and wait for the user's answer."""
    from AVFoundation import AVCaptureDevice, AVMediaTypeAudio

    done = threading.Event()
    result: dict[str, bool] = {}

    def handler(granted: bool) -> None:
        result["granted"] = bool(granted)
        done.set()

    AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, handler)

    deadline = time.time() + timeout
    while not done.is_set() and time.time() < deadline:
        _pump_run_loop(0.1)
    return result.get("granted", False)


def _prompt_accessibility() -> None:
    """Show the system Accessibility prompt; also registers us in the list."""
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt

    AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})


def open_settings_pane(url: str) -> None:
    subprocess.run(["open", url], check=False)


def alert(title: str, info: str, buttons: list[str]) -> int:
    """Run a modal alert on the main thread; returns the 0-based button index."""
    app = AppKit.NSApplication.sharedApplication()
    app.activateIgnoringOtherApps_(True)
    panel = AppKit.NSAlert.alloc().init()
    panel.setMessageText_(title)
    panel.setInformativeText_(info)
    for button in buttons:
        panel.addButtonWithTitle_(button)
    return int(panel.runModal() - AppKit.NSAlertFirstButtonReturn)


def _grant_loop(
    *,
    title: str,
    info: str,
    pane_url: str,
    is_granted: Callable[[], bool],
) -> bool:
    """Alert loop: open the right System Settings pane, re-check until granted."""
    while True:
        if is_granted():
            return True
        choice = alert(title, info, ["Open System Settings", "Check Again", "Skip for Now"])
        if choice == 0:
            open_settings_pane(pane_url)
            # Give the user time in System Settings, then fall through to re-check
            for _ in range(3):
                _pump_run_loop(0.3)
        elif choice == 1:
            _pump_run_loop(0.2)
        else:
            return is_granted()


def ensure_permissions(interactive: bool = True) -> PermissionsReport:
    """Check mic + accessibility; when interactive, walk the user through granting.

    Runs on the main thread. Safe to call before the AppKit event loop starts
    (modal alerts spin their own run loop) and again later from a menu action.
    """
    report = check_silently()
    logger.info(
        "Permissions check: microphone=%s accessibility=%s",
        report.microphone,
        report.accessibility,
    )
    if not interactive or report.all_granted:
        return report

    alert(
        "Welcome to WhisperMe",
        "WhisperMe needs two permissions to work:\n\n"
        "1. Microphone — to hear your dictation\n"
        "2. Accessibility — to detect the Option+/ hotkey and paste text for you\n\n"
        "The next steps will guide you through granting each one.",
        ["Continue"],
    )

    # --- Microphone ---
    mic = microphone_status()
    if mic == "undetermined":
        logger.info("Requesting microphone access (system prompt)")
        _request_microphone()
        mic = microphone_status()
    if mic != "granted":
        _grant_loop(
            title="Microphone Access Needed",
            info="WhisperMe can't hear you yet.\n\n"
            "In System Settings → Privacy & Security → Microphone, "
            "turn ON the switch next to WhisperMe, then click Check Again.",
            pane_url=_MIC_PANE,
            is_granted=lambda: microphone_status() == "granted",
        )

    # --- Accessibility ---
    if not accessibility_trusted():
        logger.info("Requesting accessibility trust (system prompt)")
        _prompt_accessibility()
        _grant_loop(
            title="Accessibility Access Needed",
            info="This lets WhisperMe see the Option+/ hotkey anywhere and paste "
            "the transcribed text into the app you're using.\n\n"
            "In System Settings → Privacy & Security → Accessibility, "
            "turn ON the switch next to WhisperMe, then click Check Again.",
            pane_url=_AX_PANE,
            is_granted=accessibility_trusted,
        )

    report = check_silently()
    logger.info(
        "Permissions after setup: microphone=%s accessibility=%s",
        report.microphone,
        report.accessibility,
    )
    if report.all_granted:
        alert(
            "WhisperMe Is Ready",
            "All permissions granted!\n\n"
            "Press Option+/ in any app to start dictating, and press it again "
            "to transcribe and paste.\n\n"
            "Look for the small microphone icon in the menu bar — it turns red "
            "while recording.",
            ["Start Dictating"],
        )
    else:
        missing = []
        if not report.microphone:
            missing.append("Microphone")
        if not report.accessibility:
            missing.append("Accessibility")
        alert(
            "WhisperMe Is Not Fully Set Up",
            f"Still missing: {', '.join(missing)}.\n\n"
            "WhisperMe will run, but dictation won't work until access is "
            "granted. You can re-run this setup any time from the menu bar "
            "icon → Permission Setup.",
            ["OK"],
        )
    return report


def hotkey_failure_flow(retry_start: Callable[[], bool]) -> bool:
    """Interactive recovery when the global hotkey event tap can't be created.

    On some macOS versions an active keyboard event tap additionally requires
    Input Monitoring. Guides the user there and retries until it works or
    they give up. Returns True if the hotkey is now active.
    """
    while True:
        choice = alert(
            "Hotkey Could Not Be Registered",
            "macOS blocked WhisperMe from watching for the Option+/ hotkey.\n\n"
            "Make sure WhisperMe is enabled in BOTH:\n"
            "• Privacy & Security → Accessibility\n"
            "• Privacy & Security → Input Monitoring\n\n"
            "(If macOS asks to relaunch WhisperMe after changing Input "
            "Monitoring, let it.)",
            ["Open Input Monitoring", "Open Accessibility", "Try Again", "Skip for Now"],
        )
        if choice == 0:
            open_settings_pane(_INPUT_PANE)
        elif choice == 1:
            open_settings_pane(_AX_PANE)
        elif choice == 2:
            if retry_start():
                alert("Hotkey Active", "Option+/ is ready — try dictating!", ["OK"])
                return True
        else:
            return False
        _pump_run_loop(0.2)
