from __future__ import annotations

from collections.abc import Callable

import Quartz


# Key code for "/" on US keyboard
_SLASH_KEYCODE = 44


class HotkeyListener:
    """Detects Option+/ toggle using Quartz CGEventTap (suppresses the keystroke)."""

    def __init__(self, on_toggle: Callable[[], None]) -> None:
        self._on_toggle = on_toggle
        self._tap = None
        self._loop_source = None

    def start(self) -> None:
        mask = (1 << Quartz.kCGEventKeyDown)

        def callback(proxy, event_type, event, refcon):
            keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)
            option = bool(flags & Quartz.kCGEventFlagMaskAlternate)

            if option and keycode == _SLASH_KEYCODE:
                print("[hotkey] Option+/ detected!", flush=True)
                self._on_toggle()
                # Suppress the keystroke so '÷' isn't typed
                return None
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
            print("[hotkey] ERROR: Could not create event tap. Check Accessibility permissions.", flush=True)
            return

        self._loop_source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        loop = Quartz.CFRunLoopGetMain()
        Quartz.CFRunLoopAddSource(loop, self._loop_source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        print("[hotkey] Global event tap registered", flush=True)

    def stop(self) -> None:
        if self._tap:
            Quartz.CGEventTapEnable(self._tap, False)
            self._tap = None
