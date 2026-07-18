from __future__ import annotations

import logging
import signal
import threading
from collections import deque
from collections.abc import Callable

import AppKit
import objc
from Foundation import NSObject, NSRange
from PyObjCTools import AppHelper
from Quartz import CABasicAnimation

logger = logging.getLogger(__name__)

_WIDTH = 440
_PAD = 16
_MIN_HEIGHT = 122
_MAX_HEIGHT = 420
_CORNER_RADIUS = 18
# Vertical space used by everything except the text area (header + waveform)
_CHROME_ABOVE_TEXT = 98
_BOTTOM_PAD = 14
_CURSOR_GAP = 14  # distance between the cursor and the panel edge


class _ButtonTarget(NSObject):
    """ObjC target for button actions."""

    @objc.python_method
    def set_callbacks(self, reset_cb: Callable, close_cb: Callable) -> None:
        self._reset_cb = reset_cb
        self._close_cb = close_cb

    @objc.typedSelector(b"v@:@")
    def onReset_(self, sender) -> None:
        if self._reset_cb:
            self._reset_cb()

    @objc.typedSelector(b"v@:@")
    def onClose_(self, sender) -> None:
        if self._close_cb:
            self._close_cb()


class _WaveformView(AppKit.NSView):
    """Rounded voice-level bars that fill left-to-right as speech accumulates,
    then scroll once the strip is full."""

    _BAR_WIDTH = 3.0
    _BAR_GAP = 2.0
    _MIN_BAR = 3.0

    def initWithFrame_(self, frame):
        self = objc.super(_WaveformView, self).initWithFrame_(frame)
        if self is None:
            return None
        self._levels: list[float] = []
        return self

    @objc.python_method
    def capacity(self) -> int:
        step = self._BAR_WIDTH + self._BAR_GAP
        return max(1, int(self.bounds().size.width // step))

    @objc.python_method
    def add_level(self, level: float) -> None:
        self._levels.append(max(0.0, min(1.0, level)))
        overflow = len(self._levels) - self.capacity()
        if overflow > 0:
            del self._levels[:overflow]
        self.setNeedsDisplay_(True)

    @objc.python_method
    def clear(self) -> None:
        self._levels = []
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect) -> None:
        try:
            bounds = self.bounds()
            height = bounds.size.height
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.85).setFill()
            step = self._BAR_WIDTH + self._BAR_GAP
            for i, level in enumerate(self._levels):
                bar_h = self._MIN_BAR + level * (height - self._MIN_BAR)
                bar = AppKit.NSMakeRect(
                    i * step, (height - bar_h) / 2.0, self._BAR_WIDTH, bar_h
                )
                AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    bar, self._BAR_WIDTH / 2.0, self._BAR_WIDTH / 2.0
                ).fill()
        except Exception:  # never let drawing take the app down
            logger.exception("waveform drawRect failed")


class Overlay:
    """Cursor-anchored frosted panel with live waveform and cleaned transcription."""

    def __init__(
        self,
        on_reset: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
        show_llm: bool = False,
    ) -> None:
        self._app: AppKit.NSApplication | None = None
        self._panel: AppKit.NSPanel | None = None
        self._status_field: AppKit.NSTextField | None = None
        self._target_field: AppKit.NSTextField | None = None
        self._dot_field: AppKit.NSTextField | None = None
        self._wave_view: _WaveformView | None = None
        self._scroll_view: AppKit.NSScrollView | None = None
        self._text_view: AppKit.NSTextView | None = None

        self._show_llm = show_llm
        self._has_cleaned = False
        self._levels: deque[float] = deque()  # fed from the mic thread
        self._wave_timer: AppKit.NSTimer | None = None
        self._target_timer: AppKit.NSTimer | None = None

        # Placement anchor: ("below", top_y) keeps the top edge pinned under the
        # cursor; ("above", bottom_y) keeps the bottom edge pinned over it.
        self._anchor_mode = "below"
        self._anchor_y = 0.0
        self._visible_frame = AppKit.NSMakeRect(0, 0, 0, 0)

        self._button_target = _ButtonTarget.alloc().init()
        self._button_target.set_callbacks(on_reset or (lambda: None), on_close or (lambda: None))
        self._setup()

    # -- construction --------------------------------------------------------

    def _setup(self) -> None:
        self._app = AppKit.NSApplication.sharedApplication()
        self._app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        rect = AppKit.NSMakeRect(0, 0, _WIDTH, _MIN_HEIGHT)
        self._panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            AppKit.NSWindowStyleMaskBorderless | AppKit.NSWindowStyleMaskNonactivatingPanel,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        self._panel.setLevel_(AppKit.NSStatusWindowLevel)
        self._panel.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            | AppKit.NSWindowCollectionBehaviorIgnoresCycle
        )
        self._panel.setOpaque_(False)
        self._panel.setBackgroundColor_(AppKit.NSColor.clearColor())
        self._panel.setHasShadow_(True)
        self._panel.setMovableByWindowBackground_(True)
        self._panel.setFloatingPanel_(True)
        self._panel.setHidesOnDeactivate_(False)
        self._panel.setAppearance_(
            AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
        )

        # Frosted-glass background with rounded corners
        effect = AppKit.NSVisualEffectView.alloc().initWithFrame_(rect)
        effect.setMaterial_(AppKit.NSVisualEffectMaterialHUDWindow)
        effect.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        effect.setState_(AppKit.NSVisualEffectStateActive)
        effect.setWantsLayer_(True)
        effect.layer().setCornerRadius_(_CORNER_RADIUS)
        effect.layer().setMasksToBounds_(True)
        self._panel.setContentView_(effect)
        content = effect

        # Pulsing recording dot
        self._dot_field = self._make_label("●", 13, AppKit.NSColor.systemRedColor())
        self._dot_field.setWantsLayer_(True)
        content.addSubview_(self._dot_field)

        self._status_field = self._make_label(
            "Listening…", 14, AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95)
        )
        self._status_field.setFont_(AppKit.NSFont.boldSystemFontOfSize_(14))
        content.addSubview_(self._status_field)

        self._target_field = self._make_label(
            "", 11, AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.45)
        )
        content.addSubview_(self._target_field)

        self._reset_btn = self._make_icon_button(
            "arrow.counterclockwise.circle.fill",
            "R",
            "Reset and keep listening (⌥R)",
            objc.selector(self._button_target.onReset_, signature=b"v@:@"),
        )
        content.addSubview_(self._reset_btn)

        self._close_btn = self._make_icon_button(
            "xmark.circle.fill",
            "X",
            "Close without pasting (⌥X)",
            objc.selector(self._button_target.onClose_, signature=b"v@:@"),
        )
        content.addSubview_(self._close_btn)

        self._wave_view = _WaveformView.alloc().initWithFrame_(
            AppKit.NSMakeRect(_PAD, 0, _WIDTH - 2 * _PAD, 30)
        )
        content.addSubview_(self._wave_view)

        self._scroll_view = AppKit.NSScrollView.alloc().initWithFrame_(
            AppKit.NSMakeRect(_PAD, _BOTTOM_PAD, _WIDTH - 2 * _PAD, 10)
        )
        self._scroll_view.setHasVerticalScroller_(True)
        self._scroll_view.setHasHorizontalScroller_(False)
        self._scroll_view.setAutohidesScrollers_(True)
        self._scroll_view.setDrawsBackground_(False)
        self._scroll_view.setBorderType_(AppKit.NSNoBorder)

        self._text_view = AppKit.NSTextView.alloc().initWithFrame_(
            AppKit.NSMakeRect(0, 0, _WIDTH - 2 * _PAD, 10)
        )
        self._text_view.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        self._text_view.setTextColor_(self._preview_color())
        self._text_view.setBackgroundColor_(AppKit.NSColor.clearColor())
        self._text_view.setDrawsBackground_(False)
        self._text_view.setEditable_(False)
        self._text_view.setSelectable_(False)
        self._text_view.setRichText_(False)
        self._text_view.textContainer().setWidthTracksTextView_(True)
        self._text_view.setHorizontallyResizable_(False)
        self._text_view.setVerticallyResizable_(True)
        self._text_view.setMaxSize_(AppKit.NSMakeSize(_WIDTH, 1e7))
        self._scroll_view.setDocumentView_(self._text_view)
        content.addSubview_(self._scroll_view)

        self._layout(_MIN_HEIGHT)

    @staticmethod
    def _make_label(text: str, size: float, color) -> AppKit.NSTextField:
        label = AppKit.NSTextField.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 10, 10))
        label.setStringValue_(text)
        label.setFont_(AppKit.NSFont.systemFontOfSize_(size))
        label.setTextColor_(color)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        return label

    def _make_icon_button(self, symbol: str, fallback: str, tooltip: str, action) -> AppKit.NSButton:
        btn = AppKit.NSButton.alloc().initWithFrame_(AppKit.NSMakeRect(0, 0, 26, 26))
        image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            symbol, tooltip
        )
        if image is not None:
            config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
                17, AppKit.NSFontWeightRegular
            )
            sized = image.imageWithSymbolConfiguration_(config)
            btn.setImage_(sized if sized is not None else image)
            btn.setImagePosition_(AppKit.NSImageOnly)
            btn.setContentTintColor_(
                AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55)
            )
        else:
            btn.setTitle_(fallback)
        btn.setBordered_(False)
        btn.setToolTip_(tooltip)
        btn.setTarget_(self._button_target)
        btn.setAction_(action)
        return btn

    def _preview_color(self):
        return AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.55)

    def _final_color(self):
        return AppKit.NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.95)

    # -- layout & placement --------------------------------------------------

    def _layout(self, height: float) -> None:
        """Place subviews for a panel of the given height (AppKit y-up coords)."""
        w = _WIDTH
        self._dot_field.setFrame_(AppKit.NSMakeRect(_PAD, height - 34, 16, 18))
        self._status_field.setFrame_(AppKit.NSMakeRect(_PAD + 17, height - 36, 220, 22))
        self._close_btn.setFrame_(AppKit.NSMakeRect(w - _PAD - 26, height - 36, 26, 26))
        self._reset_btn.setFrame_(AppKit.NSMakeRect(w - _PAD - 60, height - 36, 26, 26))
        self._target_field.setFrame_(AppKit.NSMakeRect(_PAD + 17, height - 54, w - 2 * _PAD - 90, 15))
        self._wave_view.setFrame_(AppKit.NSMakeRect(_PAD, height - 90, w - 2 * _PAD, 30))
        text_h = max(4.0, height - _CHROME_ABOVE_TEXT - _BOTTOM_PAD)
        self._scroll_view.setFrame_(AppKit.NSMakeRect(_PAD, _BOTTOM_PAD, w - 2 * _PAD, text_h))

    def _pick_position(self, height: float) -> None:
        """Anchor the panel to the current mouse location, flipping above the
        cursor when there isn't room below, and clamping to the screen."""
        mouse = AppKit.NSEvent.mouseLocation()
        screen = None
        for candidate in AppKit.NSScreen.screens():
            if AppKit.NSPointInRect(mouse, candidate.frame()):
                screen = candidate
                break
        if screen is None:
            screen = AppKit.NSScreen.mainScreen()
        vis = screen.visibleFrame()
        self._visible_frame = vis

        x = mouse.x - _WIDTH / 2.0
        x = max(vis.origin.x + 8, min(x, vis.origin.x + vis.size.width - _WIDTH - 8))

        below_origin = mouse.y - _CURSOR_GAP - height
        if below_origin >= vis.origin.y + 8:
            self._anchor_mode = "below"
            self._anchor_y = mouse.y - _CURSOR_GAP  # panel top edge
            y = below_origin
        else:
            self._anchor_mode = "above"
            self._anchor_y = mouse.y + _CURSOR_GAP  # panel bottom edge
            y = self._anchor_y

        self._panel.setFrame_display_(AppKit.NSMakeRect(x, y, _WIDTH, height), True)
        self._layout(height)

    def _frame_for_height(self, height: float) -> AppKit.NSRect:
        frame = self._panel.frame()
        vis = self._visible_frame
        if self._anchor_mode == "below":
            y = self._anchor_y - height
            if vis.size.height and y < vis.origin.y + 8:
                y = vis.origin.y + 8
        else:
            y = self._anchor_y
            if vis.size.height and y + height > vis.origin.y + vis.size.height - 8:
                y = vis.origin.y + vis.size.height - 8 - height
        return AppKit.NSMakeRect(frame.origin.x, y, _WIDTH, height)

    def _resize_panel(self) -> None:
        layout_manager = self._text_view.layoutManager()
        text_container = self._text_view.textContainer()
        layout_manager.ensureLayoutForTextContainer_(text_container)
        used = layout_manager.usedRectForTextContainer_(text_container)
        text_h = used.size.height + 8 if self._text_view.string() else 0
        desired = _CHROME_ABOVE_TEXT + _BOTTOM_PAD + text_h
        desired = int(min(_MAX_HEIGHT, max(_MIN_HEIGHT, desired)))

        if int(self._panel.frame().size.height) != desired:
            self._panel.setFrame_display_animate_(
                self._frame_for_height(desired), True, True
            )
            self._layout(desired)

    # -- timers ---------------------------------------------------------------

    def _start_timers(self) -> None:
        self._stop_timers()
        self._wave_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.05, True, lambda timer: self._drain_levels()
        )
        self._target_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.5, True, lambda timer: self._update_target_app()
        )

    def _stop_timers(self) -> None:
        for timer in (self._wave_timer, self._target_timer):
            if timer:
                timer.invalidate()
        self._wave_timer = None
        self._target_timer = None

    def _drain_levels(self) -> None:
        while self._levels:
            self._wave_view.add_level(self._levels.popleft())

    def _update_target_app(self) -> None:
        front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        name = front.localizedName() if front else "?"
        self._target_field.setStringValue_(f"→ {name}")

    def _start_dot_pulse(self) -> None:
        layer = self._dot_field.layer()
        if layer is None:
            return
        pulse = CABasicAnimation.animationWithKeyPath_("opacity")
        pulse.setFromValue_(1.0)
        pulse.setToValue_(0.25)
        pulse.setDuration_(0.8)
        pulse.setAutoreverses_(True)
        pulse.setRepeatCount_(1e9)
        layer.addAnimation_forKey_(pulse, "pulse")

    # -- public API (thread-safe) ---------------------------------------------

    def push_level(self, level: float) -> None:
        """Feed a mic level sample (0..1) from any thread."""
        self._levels.append(level)

    def show(self) -> None:
        def _show():
            self._has_cleaned = False
            self._levels.clear()
            self._wave_view.clear()
            self._status_field.setStringValue_("Listening…")
            self._text_view.setString_("")
            self._text_view.setTextColor_(
                self._preview_color() if self._show_llm else self._final_color()
            )
            self._update_target_app()
            self._pick_position(_MIN_HEIGHT)
            self._start_timers()
            self._start_dot_pulse()
            self._panel.orderFrontRegardless()

        AppHelper.callAfter(_show)

    def hide(self) -> None:
        def _hide():
            self._stop_timers()
            self._panel.orderOut_(None)

        AppHelper.callAfter(_hide)

    def reset(self) -> None:
        def _reset():
            self._has_cleaned = False
            self._levels.clear()
            self._wave_view.clear()
            self._status_field.setStringValue_("Listening…")
            self._text_view.setString_("")
            self._text_view.setTextColor_(
                self._preview_color() if self._show_llm else self._final_color()
            )
            self._update_target_app()
            self._panel.setFrame_display_animate_(
                self._frame_for_height(_MIN_HEIGHT), True, True
            )
            self._layout(_MIN_HEIGHT)

        AppHelper.callAfter(_reset)

    def update_text(self, text: str) -> None:
        """Live raw transcription: shown as a dim preview until the first
        cleaned version arrives (or as the only text when LLM is off)."""

        def _update():
            if self._show_llm and self._has_cleaned:
                return
            self._text_view.setString_(text)
            self._resize_panel()
            self._text_view.scrollRangeToVisible_(NSRange(len(self._text_view.string()), 0))

        AppHelper.callAfter(_update)

    def update_llm_text(self, text: str) -> None:
        def _update():
            if not self._show_llm:
                return
            self._has_cleaned = True
            self._text_view.setTextColor_(self._final_color())
            self._text_view.setString_(text)
            self._resize_panel()
            self._text_view.scrollRangeToVisible_(NSRange(len(self._text_view.string()), 0))

        AppHelper.callAfter(_update)

    def update_status(self, status: str) -> None:
        AppHelper.callAfter(self._status_field.setStringValue_, status)

    def run_event_loop(self) -> None:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        AppHelper.runEventLoop()

    def stop_event_loop(self) -> None:
        AppHelper.stopEventLoop()
