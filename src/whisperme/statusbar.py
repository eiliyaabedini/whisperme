from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable

import AppKit
import objc
from Foundation import NSObject
from PyObjCTools import AppHelper

from whisperme import autostart
from whisperme.paths import LOG_DIR

logger = logging.getLogger(__name__)

# state -> (SF Symbol, tint color factory or None for template default)
_STATES = {
    "idle": ("mic", None),
    "recording": ("mic.fill", lambda: AppKit.NSColor.systemRedColor()),
    "processing": ("waveform", lambda: AppKit.NSColor.systemOrangeColor()),
    "warning": ("mic.slash", lambda: AppKit.NSColor.systemYellowColor()),
    "fixing": ("wrench.and.screwdriver", lambda: AppKit.NSColor.systemBlueColor()),
}

_STATE_LABELS = {
    "idle": "Status: idle — press ⌥/ to dictate",
    "recording": "Status: recording…",
    "processing": "Status: transcribing…",
    "warning": "Status: permissions missing",
    "fixing": "Status: auto-fix running…",
}


class _MenuTarget(NSObject):
    """ObjC target for menu item actions."""

    @objc.python_method
    def set_callbacks(
        self,
        on_quit: Callable[[], None],
        on_permission_setup: Callable[[], None],
        on_toggle_login: Callable[[], None],
        on_autofix: Callable[[], None],
    ) -> None:
        self._on_quit = on_quit
        self._on_permission_setup = on_permission_setup
        self._on_toggle_login = on_toggle_login
        self._on_autofix = on_autofix

    @objc.typedSelector(b"v@:@")
    def onQuit_(self, sender) -> None:
        if self._on_quit:
            self._on_quit()

    @objc.typedSelector(b"v@:@")
    def onPermissionSetup_(self, sender) -> None:
        if self._on_permission_setup:
            self._on_permission_setup()

    @objc.typedSelector(b"v@:@")
    def onAutoFix_(self, sender) -> None:
        if self._on_autofix:
            self._on_autofix()

    @objc.typedSelector(b"v@:@")
    def onOpenLogs_(self, sender) -> None:
        subprocess.run(["open", str(LOG_DIR)], check=False)

    @objc.typedSelector(b"v@:@")
    def onToggleLogin_(self, sender) -> None:
        if self._on_toggle_login:
            self._on_toggle_login()


class StatusBar:
    """Small always-visible menu bar icon showing WhisperMe's state.

    Must be created on the main thread. State updates may come from any
    thread; they are marshalled onto the main thread.
    """

    def __init__(
        self,
        on_quit: Callable[[], None],
        on_permission_setup: Callable[[], None],
        on_autofix: Callable[[], None] | None = None,
    ) -> None:
        self._target = _MenuTarget.alloc().init()
        self._target.set_callbacks(
            on_quit, on_permission_setup, self._toggle_login, on_autofix or (lambda: None)
        )
        self._permissions_ok = True

        self._item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(
            AppKit.NSSquareStatusItemLength
        )
        button = self._item.button()
        button.setToolTip_("WhisperMe — press Option+/ to dictate")

        menu = AppKit.NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)

        header = menu.addItemWithTitle_action_keyEquivalent_("WhisperMe", None, "")
        header.setEnabled_(False)

        self._status_menu_item = menu.addItemWithTitle_action_keyEquivalent_(
            _STATE_LABELS["idle"], None, ""
        )
        self._status_menu_item.setEnabled_(False)

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        self._autofix_menu_item = menu.addItemWithTitle_action_keyEquivalent_(
            "Auto-Fix Recent Issues",
            objc.selector(self._target.onAutoFix_, signature=b"v@:@"),
            "",
        )
        self._autofix_menu_item.setTarget_(self._target)
        self._autofix_menu_item.setToolTip_(
            "Run Claude Code headlessly to diagnose recent crashes from the "
            "logs, fix the code, and push the fix"
        )

        self._permissions_menu_item = menu.addItemWithTitle_action_keyEquivalent_(
            "Permission Setup…",
            objc.selector(self._target.onPermissionSetup_, signature=b"v@:@"),
            "",
        )
        self._permissions_menu_item.setTarget_(self._target)

        logs_item = menu.addItemWithTitle_action_keyEquivalent_(
            "Open Logs Folder",
            objc.selector(self._target.onOpenLogs_, signature=b"v@:@"),
            "",
        )
        logs_item.setTarget_(self._target)

        self._login_menu_item = menu.addItemWithTitle_action_keyEquivalent_(
            "Start at Login",
            objc.selector(self._target.onToggleLogin_, signature=b"v@:@"),
            "",
        )
        self._login_menu_item.setTarget_(self._target)
        self._refresh_login_item()

        menu.addItem_(AppKit.NSMenuItem.separatorItem())

        quit_item = menu.addItemWithTitle_action_keyEquivalent_(
            "Quit WhisperMe",
            objc.selector(self._target.onQuit_, signature=b"v@:@"),
            "q",
        )
        quit_item.setTarget_(self._target)

        self._item.setMenu_(menu)
        self._apply_state("idle")
        logger.info("Status bar item installed")

    # -- public API (thread-safe) ------------------------------------------

    def set_state(self, state: str) -> None:
        AppHelper.callAfter(self._apply_state, state)

    def set_autofix_running(self, running: bool) -> None:
        def _apply() -> None:
            self._autofix_menu_item.setEnabled_(not running)
            self._autofix_menu_item.setTitle_(
                "Auto-Fix Running…" if running else "Auto-Fix Recent Issues"
            )

        AppHelper.callAfter(_apply)

    def set_permissions_ok(self, ok: bool) -> None:
        def _apply() -> None:
            self._permissions_ok = ok
            self._permissions_menu_item.setTitle_(
                "Permission Setup…" if ok else "⚠️ Permission Setup Needed…"
            )
            self._apply_state("idle" if ok else "warning")

        AppHelper.callAfter(_apply)

    def remove(self) -> None:
        def _remove() -> None:
            AppKit.NSStatusBar.systemStatusBar().removeStatusItem_(self._item)

        AppHelper.callAfter(_remove)

    # -- internals (main thread only) --------------------------------------

    def _apply_state(self, state: str) -> None:
        if state == "idle" and not self._permissions_ok:
            state = "warning"
        symbol, tint = _STATES.get(state, _STATES["idle"])
        button = self._item.button()
        image = self._symbol_image(symbol)
        if image is not None:
            button.setImage_(image)
            button.setTitle_("")
        else:  # very old macOS without SF Symbols — fall back to text
            button.setImage_(None)
            button.setTitle_({"recording": "●", "processing": "…"}.get(state, "W"))
        button.setContentTintColor_(tint() if tint else None)
        self._status_menu_item.setTitle_(_STATE_LABELS.get(state, state))

    @staticmethod
    def _symbol_image(name: str) -> AppKit.NSImage | None:
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            name, "WhisperMe"
        )
        if image is None:
            return None
        config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
            13.0, AppKit.NSFontWeightRegular, AppKit.NSImageSymbolScaleSmall
        )
        sized = image.imageWithSymbolConfiguration_(config)
        if sized is not None:
            image = sized
        image.setTemplate_(True)
        return image

    def _refresh_login_item(self) -> None:
        available = autostart.is_available()
        self._login_menu_item.setEnabled_(available)
        enabled = available and autostart.is_enabled()
        self._login_menu_item.setState_(
            AppKit.NSControlStateValueOn if enabled else AppKit.NSControlStateValueOff
        )
        if not available:
            self._login_menu_item.setToolTip_(
                "Install WhisperMe.app first (scripts/install.sh)"
            )

    def _toggle_login(self) -> None:
        if autostart.is_enabled():
            autostart.disable()
        else:
            autostart.enable()
        self._refresh_login_item()
