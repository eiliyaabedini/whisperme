from __future__ import annotations

import logging
from collections.abc import Callable

import Quartz

logger = logging.getLogger(__name__)


# US-layout virtual keycodes
KEY_SLASH = 44
KEY_PERIOD = 47
KEY_X = 7
KEY_R = 15

# The OS delivers these event types when it disables a tap. They are sent
# regardless of the event mask, so the callback must handle them explicitly.
_TAP_DISABLED_TIMEOUT = getattr(Quartz, "kCGEventTapDisabledByTimeout", 0xFFFFFFFE)
_TAP_DISABLED_USER_INPUT = getattr(Quartz, "kCGEventTapDisabledByUserInput", 0xFFFFFFFF)


class HotkeyListener:
    """Global Option+<key> hotkeys via a Quartz CGEventTap.

    ``bindings`` maps a keycode to a handler that returns True when it
    consumed the event — the keystroke is then suppressed system-wide.
    Returning False lets the event pass through unchanged, so e.g. Option+X
    still types "≈" whenever WhisperMe isn't recording.
    """

    def __init__(self, bindings: dict[int, Callable[[], bool]]) -> None:
        self._bindings = bindings
        self._tap = None
        self._loop_source = None

    def start(self) -> bool:
        """Register the global event tap. Returns True if the hotkeys are active."""
        if self._tap is not None:
            return True

        mask = (1 << Quartz.kCGEventKeyDown)

        def callback(proxy, event_type, event, refcon):
            # macOS disables the tap if a callback runs too long (timeout) or on
            # certain user input. If we don't re-enable it, the hotkey silently
            # stops working until the app is restarted — a common "it just froze"
            # symptom. Re-enable and carry on.
            if event_type in (_TAP_DISABLED_TIMEOUT, _TAP_DISABLED_USER_INPUT):
                logger.warning("Event tap disabled by system (type=%s); re-enabling", event_type)
                print("[hotkey] Event tap disabled by system; re-enabling", flush=True)
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event

            try:
                keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                flags = Quartz.CGEventGetFlags(event)
                option = bool(flags & Quartz.kCGEventFlagMaskAlternate)

                if option:
                    handler = self._bindings.get(keycode)
                    if handler is not None:
                        logger.info("Hotkey Option+keycode=%d detected", keycode)
                        try:
                            consumed = handler()
                        except Exception:
                            logger.exception("Hotkey handler crashed")
                            consumed = True  # it was our combo; don't leak the keystroke
                        if consumed:
                            return None
            except Exception:
                logger.exception("Hotkey callback crashed")
            return event

        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,  # Can modify/suppress events
            mask,
            callback,
            None,
        )

        if self._tap is None:
            logger.error("Could not create event tap — Accessibility permissions likely missing")
            print("[hotkey] ERROR: Could not create event tap. Check Accessibility permissions.", flush=True)
            return False

        self._loop_source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        loop = Quartz.CFRunLoopGetMain()
        Quartz.CFRunLoopAddSource(loop, self._loop_source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        print("[hotkey] Global event tap registered", flush=True)
        logger.info("Global event tap registered")
        return True

    def stop(self) -> None:
        if self._tap:
            Quartz.CGEventTapEnable(self._tap, False)
            self._tap = None
