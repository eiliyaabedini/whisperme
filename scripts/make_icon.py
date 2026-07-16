"""Generate WhisperMe.icns — a rounded-rect app icon with a mic symbol.

Run inside the project env (needs pyobjc): uv run python scripts/make_icon.py OUT.icns
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import AppKit

# (filename, pixel size) pairs required by iconutil
_ICONSET_SIZES = [
    ("icon_16x16.png", 16),
    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),
    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128),
    ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256),
    ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512),
    ("icon_512x512@2x.png", 1024),
]


def _white_symbol(name: str, point_size: float) -> AppKit.NSImage | None:
    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if image is None:
        return None
    config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        point_size, AppKit.NSFontWeightMedium
    )
    sized = image.imageWithSymbolConfiguration_(config)
    if sized is not None:
        image = sized
    size = image.size()
    tinted = AppKit.NSImage.alloc().initWithSize_(size)
    tinted.lockFocus()
    try:
        image.drawAtPoint_fromRect_operation_fraction_(
            (0, 0), AppKit.NSZeroRect, AppKit.NSCompositingOperationSourceOver, 1.0
        )
        AppKit.NSColor.whiteColor().set()
        AppKit.NSRectFillUsingOperation(
            AppKit.NSMakeRect(0, 0, size.width, size.height),
            AppKit.NSCompositingOperationSourceIn,
        )
    finally:
        tinted.unlockFocus()
    return tinted


def _render_master(size: int = 1024) -> AppKit.NSImage:
    image = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(size, size))
    image.lockFocus()
    try:
        # macOS icon grid: rounded rect inset from the canvas edges
        margin = size * 0.098
        rect = AppKit.NSMakeRect(margin, margin, size - 2 * margin, size - 2 * margin)
        radius = size * 0.18
        path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius)
        gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.11, 0.12, 0.16, 1.0),
            AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.25, 0.28, 0.38, 1.0),
        )
        gradient.drawInBezierPath_angle_(path, 90.0)

        symbol = _white_symbol("mic.fill", size * 0.42)
        if symbol is not None:
            ssize = symbol.size()
            symbol.drawAtPoint_fromRect_operation_fraction_(
                ((size - ssize.width) / 2, (size - ssize.height) / 2),
                AppKit.NSZeroRect,
                AppKit.NSCompositingOperationSourceOver,
                1.0,
            )
    finally:
        image.unlockFocus()
    return image


def _write_png(image: AppKit.NSImage, px: int, path: Path) -> None:
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0
    )
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    try:
        AppKit.NSGraphicsContext.setCurrentContext_(context)
        image.drawInRect_fromRect_operation_fraction_(
            AppKit.NSMakeRect(0, 0, px, px),
            AppKit.NSZeroRect,
            AppKit.NSCompositingOperationCopy,
            1.0,
        )
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    data.writeToFile_atomically_(str(path), True)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: make_icon.py OUTPUT.icns", file=sys.stderr)
        return 2
    output = Path(sys.argv[1]).resolve()
    master = _render_master()

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "WhisperMe.iconset"
        iconset.mkdir()
        for filename, px in _ICONSET_SIZES:
            _write_png(master, px, iconset / filename)
        subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(output)],
            check=True,
        )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
