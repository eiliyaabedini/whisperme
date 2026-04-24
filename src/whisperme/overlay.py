from __future__ import annotations

import signal
from collections.abc import Callable

import AppKit
import objc
from Foundation import NSObject, NSRange
from PyObjCTools import AppHelper


_WIDTH = 480
_HEADER_HEIGHT = 70  # status + target + buttons
_MIN_HEIGHT = 130
_MAX_HEIGHT = 400
_PADDING = 20


class _ButtonTarget(NSObject):
    """ObjC target for button actions."""

    def initWithCallbacks_close_(self, reset_cb, close_cb):
        self = objc.super(_ButtonTarget, self).init()
        self._reset_cb = reset_cb
        self._close_cb = close_cb
        return self

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


class Overlay:
    """Floating NSPanel overlay showing recording status and live transcription."""

    def __init__(
        self,
        on_reset: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
        show_llm: bool = False,
    ) -> None:
        self._panel: AppKit.NSPanel | None = None
        self._status_field: AppKit.NSTextField | None = None
        self._target_field: AppKit.NSTextField | None = None
        self._scroll_view: AppKit.NSScrollView | None = None
        self._text_view: AppKit.NSTextView | None = None
        self._llm_label: AppKit.NSTextField | None = None
        self._llm_scroll_view: AppKit.NSScrollView | None = None
        self._llm_text_view: AppKit.NSTextView | None = None
        self._app: AppKit.NSApplication | None = None
        self._target_timer: AppKit.NSTimer | None = None
        self._screen_y: float = 0
        self._show_llm = show_llm
        self._button_target = _ButtonTarget.alloc().init()
        self._button_target.set_callbacks(on_reset or (lambda: None), on_close or (lambda: None))
        self._setup()

    def _setup(self) -> None:
        self._app = AppKit.NSApplication.sharedApplication()
        self._app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        screen = AppKit.NSScreen.mainScreen().frame()
        x = (screen.size.width - _WIDTH) / 2
        self._screen_y = screen.size.height * 0.75

        rect = AppKit.NSMakeRect(x, self._screen_y, _WIDTH, _MIN_HEIGHT)
        style = AppKit.NSWindowStyleMaskBorderless

        self._panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            rect,
            style | AppKit.NSWindowStyleMaskNonactivatingPanel,
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
        self._panel.setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.1, 0.1, 0.1, 0.92)
        )
        self._panel.setHasShadow_(True)
        self._panel.setMovableByWindowBackground_(True)
        self._panel.setFloatingPanel_(True)
        self._panel.setHidesOnDeactivate_(False)

        content = self._panel.contentView()
        content.setWantsLayer_(True)
        content.layer().setCornerRadius_(16.0)
        content.layer().setMasksToBounds_(True)

        # Status label — top left
        self._status_field = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(_PADDING, _MIN_HEIGHT - 40, _WIDTH - 160, 30)
        )
        self._status_field.setStringValue_("Listening...")
        self._status_field.setFont_(AppKit.NSFont.boldSystemFontOfSize_(16))
        self._status_field.setTextColor_(AppKit.NSColor.systemRedColor())
        self._status_field.setBezeled_(False)
        self._status_field.setDrawsBackground_(False)
        self._status_field.setEditable_(False)
        self._status_field.setSelectable_(False)
        self._status_field.setAutoresizingMask_(AppKit.NSViewMinYMargin)
        content.addSubview_(self._status_field)

        # Close button (X) — top right
        close_btn = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(_WIDTH - 45, _MIN_HEIGHT - 40, 30, 30)
        )
        close_btn.setBezelStyle_(AppKit.NSBezelStyleCircular)
        close_btn.setTitle_("X")
        close_btn.setFont_(AppKit.NSFont.boldSystemFontOfSize_(12))
        close_btn.setTarget_(self._button_target)
        close_btn.setAction_(objc.selector(self._button_target.onClose_, signature=b"v@:@"))
        close_btn.setToolTip_("Close without pasting")
        close_btn.setAutoresizingMask_(AppKit.NSViewMinYMargin)
        content.addSubview_(close_btn)

        # Reset button — next to close
        reset_btn = AppKit.NSButton.alloc().initWithFrame_(
            AppKit.NSMakeRect(_WIDTH - 90, _MIN_HEIGHT - 40, 40, 30)
        )
        reset_btn.setBezelStyle_(AppKit.NSBezelStyleCircular)
        reset_btn.setTitle_("R")
        reset_btn.setFont_(AppKit.NSFont.boldSystemFontOfSize_(12))
        reset_btn.setTarget_(self._button_target)
        reset_btn.setAction_(objc.selector(self._button_target.onReset_, signature=b"v@:@"))
        reset_btn.setToolTip_("Reset and keep listening")
        reset_btn.setAutoresizingMask_(AppKit.NSViewMinYMargin)
        content.addSubview_(reset_btn)

        # Target app label — below status
        self._target_field = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(_PADDING, _MIN_HEIGHT - 57, _WIDTH - 160, 18)
        )
        self._target_field.setStringValue_("")
        self._target_field.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        self._target_field.setTextColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.6, 0.6, 0.6, 1.0)
        )
        self._target_field.setBezeled_(False)
        self._target_field.setDrawsBackground_(False)
        self._target_field.setEditable_(False)
        self._target_field.setSelectable_(False)
        self._target_field.setAutoresizingMask_(AppKit.NSViewMinYMargin)
        content.addSubview_(self._target_field)

        # Scrollable text view for live transcription
        text_area_height = _MIN_HEIGHT - _HEADER_HEIGHT - _PADDING
        scroll_rect = AppKit.NSMakeRect(_PADDING, _PADDING, _WIDTH - 2 * _PADDING, text_area_height)

        self._scroll_view = AppKit.NSScrollView.alloc().initWithFrame_(scroll_rect)
        self._scroll_view.setHasVerticalScroller_(True)
        self._scroll_view.setHasHorizontalScroller_(False)
        self._scroll_view.setAutohidesScrollers_(True)
        self._scroll_view.setDrawsBackground_(False)
        self._scroll_view.setBorderType_(AppKit.NSNoBorder)
        self._scroll_view.setAutoresizingMask_(
            AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
        )

        text_view_rect = AppKit.NSMakeRect(0, 0, _WIDTH - 2 * _PADDING, text_area_height)
        self._text_view = AppKit.NSTextView.alloc().initWithFrame_(text_view_rect)
        self._text_view.setFont_(AppKit.NSFont.systemFontOfSize_(14))
        self._text_view.setTextColor_(AppKit.NSColor.whiteColor())
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

        if self._show_llm:
            # Separator + LLM label
            sep_y = _PADDING + text_area_height + 2
            self._llm_label = AppKit.NSTextField.alloc().initWithFrame_(
                AppKit.NSMakeRect(_PADDING, sep_y, _WIDTH - 2 * _PADDING, 16)
            )
            self._llm_label.setStringValue_("── Cleaned ──")
            self._llm_label.setFont_(AppKit.NSFont.systemFontOfSize_(10))
            self._llm_label.setTextColor_(
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.5, 0.5, 0.5, 1.0)
            )
            self._llm_label.setBezeled_(False)
            self._llm_label.setDrawsBackground_(False)
            self._llm_label.setEditable_(False)
            self._llm_label.setSelectable_(False)
            self._llm_label.setHidden_(True)
            content.addSubview_(self._llm_label)

            # LLM text view
            llm_rect = AppKit.NSMakeRect(_PADDING, _PADDING, _WIDTH - 2 * _PADDING, text_area_height)
            self._llm_scroll_view = AppKit.NSScrollView.alloc().initWithFrame_(llm_rect)
            self._llm_scroll_view.setHasVerticalScroller_(True)
            self._llm_scroll_view.setHasHorizontalScroller_(False)
            self._llm_scroll_view.setAutohidesScrollers_(True)
            self._llm_scroll_view.setDrawsBackground_(False)
            self._llm_scroll_view.setBorderType_(AppKit.NSNoBorder)

            llm_tv_rect = AppKit.NSMakeRect(0, 0, _WIDTH - 2 * _PADDING, text_area_height)
            self._llm_text_view = AppKit.NSTextView.alloc().initWithFrame_(llm_tv_rect)
            self._llm_text_view.setFont_(AppKit.NSFont.systemFontOfSize_(14))
            self._llm_text_view.setTextColor_(
                AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.6, 1.0, 0.6, 1.0)
            )
            self._llm_text_view.setBackgroundColor_(AppKit.NSColor.clearColor())
            self._llm_text_view.setDrawsBackground_(False)
            self._llm_text_view.setEditable_(False)
            self._llm_text_view.setSelectable_(False)
            self._llm_text_view.setRichText_(False)
            self._llm_text_view.textContainer().setWidthTracksTextView_(True)
            self._llm_text_view.setHorizontallyResizable_(False)
            self._llm_text_view.setVerticallyResizable_(True)
            self._llm_text_view.setMaxSize_(AppKit.NSMakeSize(_WIDTH, 1e7))

            self._llm_scroll_view.setDocumentView_(self._llm_text_view)
            self._llm_scroll_view.setHidden_(True)
            content.addSubview_(self._llm_scroll_view)

    def _resize_panel(self) -> None:
        """Grow panel height to fit text content, up to _MAX_HEIGHT."""
        layout_manager = self._text_view.layoutManager()
        text_container = self._text_view.textContainer()
        layout_manager.ensureLayoutForTextContainer_(text_container)
        text_rect = layout_manager.usedRectForTextContainer_(text_container)
        raw_height = text_rect.size.height + 10

        llm_height = 0
        if self._show_llm and self._llm_text_view and not self._llm_scroll_view.isHidden():
            lm = self._llm_text_view.layoutManager()
            tc = self._llm_text_view.textContainer()
            lm.ensureLayoutForTextContainer_(tc)
            lr = lm.usedRectForTextContainer_(tc)
            llm_height = lr.size.height + 10 + 20  # +20 for label + gap

        total = raw_height + llm_height + _HEADER_HEIGHT + _PADDING
        max_h = _MAX_HEIGHT + (200 if llm_height > 0 else 0)
        desired = int(min(max_h, max(_MIN_HEIGHT, total)))
        frame = self._panel.frame()

        if int(frame.size.height) != desired:
            delta = desired - frame.size.height
            new_frame = AppKit.NSMakeRect(
                frame.origin.x,
                frame.origin.y - delta,
                frame.size.width,
                desired,
            )
            self._panel.setFrame_display_animate_(new_frame, True, False)

        # Reposition raw text area in upper half, LLM in lower half
        if self._show_llm and self._llm_text_view and not self._llm_scroll_view.isHidden():
            content_h = desired - _HEADER_HEIGHT - _PADDING
            half = content_h // 2
            # LLM at bottom
            self._llm_scroll_view.setFrame_(
                AppKit.NSMakeRect(_PADDING, _PADDING, _WIDTH - 2 * _PADDING, half - 20)
            )
            # Label between
            self._llm_label.setFrame_(
                AppKit.NSMakeRect(_PADDING, _PADDING + half - 18, _WIDTH - 2 * _PADDING, 16)
            )
            # Raw text at top
            self._scroll_view.setFrame_(
                AppKit.NSMakeRect(_PADDING, _PADDING + half, _WIDTH - 2 * _PADDING, half)
            )

    def _update_target_app(self) -> None:
        front_app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        app_name = front_app.localizedName() if front_app else "Unknown"
        self._target_field.setStringValue_(f"Pasting to: {app_name}")

    def _start_target_timer(self) -> None:
        self._stop_target_timer()
        self._target_timer = AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            0.5, True, lambda timer: self._update_target_app()
        )

    def _stop_target_timer(self) -> None:
        if self._target_timer:
            self._target_timer.invalidate()
            self._target_timer = None

    def show(self) -> None:
        def _show():
            self._status_field.setStringValue_("Listening...")
            self._status_field.setTextColor_(AppKit.NSColor.systemRedColor())
            self._text_view.setString_("")
            if self._llm_text_view:
                self._llm_text_view.setString_("")
            if self._llm_label:
                self._llm_label.setHidden_(True)
            if self._llm_scroll_view:
                self._llm_scroll_view.setHidden_(True)
            self._update_target_app()
            self._start_target_timer()
            # Reset to min size
            screen = AppKit.NSScreen.mainScreen().frame()
            x = (screen.size.width - _WIDTH) / 2
            self._panel.setFrame_display_(
                AppKit.NSMakeRect(x, self._screen_y, _WIDTH, _MIN_HEIGHT), True
            )
            self._panel.orderFrontRegardless()

        AppHelper.callAfter(_show)

    def hide(self) -> None:
        def _hide():
            self._stop_target_timer()
            self._panel.orderOut_(None)

        AppHelper.callAfter(_hide)

    def reset(self) -> None:
        def _reset():
            self._status_field.setStringValue_("Listening...")
            self._status_field.setTextColor_(AppKit.NSColor.systemRedColor())
            self._text_view.setString_("")
            if self._llm_text_view:
                self._llm_text_view.setString_("")
            if self._llm_label:
                self._llm_label.setHidden_(True)
            if self._llm_scroll_view:
                self._llm_scroll_view.setHidden_(True)
            self._update_target_app()
            # Reset to min size
            frame = self._panel.frame()
            self._panel.setFrame_display_(
                AppKit.NSMakeRect(frame.origin.x, self._screen_y, _WIDTH, _MIN_HEIGHT), True
            )

        AppHelper.callAfter(_reset)

    def update_text(self, text: str) -> None:
        def _update():
            self._text_view.setString_(text)
            self._resize_panel()
            end = len(self._text_view.string())
            self._text_view.scrollRangeToVisible_(NSRange(end, 0))

        AppHelper.callAfter(_update)

    def update_llm_text(self, text: str) -> None:
        def _update():
            if not self._show_llm or not self._llm_text_view:
                return
            self._llm_label.setHidden_(False)
            self._llm_scroll_view.setHidden_(False)
            self._llm_text_view.setString_(text)
            self._resize_panel()
            end = len(self._llm_text_view.string())
            self._llm_text_view.scrollRangeToVisible_(NSRange(end, 0))

        AppHelper.callAfter(_update)

    def update_status(self, status: str) -> None:
        def _update():
            self._status_field.setStringValue_(status)
            if "listen" in status.lower():
                self._status_field.setTextColor_(AppKit.NSColor.systemRedColor())
            else:
                self._status_field.setTextColor_(AppKit.NSColor.systemYellowColor())

        AppHelper.callAfter(_update)

    def run_event_loop(self) -> None:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        AppHelper.runEventLoop()

    def stop_event_loop(self) -> None:
        AppHelper.stopEventLoop()
