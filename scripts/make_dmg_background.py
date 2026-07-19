"""Generate the DMG window background (1x + 2x) used by scripts/make-dmg.sh.

Run with the bundled interpreter (needs pyobjc):
    python scripts/make_dmg_background.py OUT_DIR
writes OUT_DIR/background.png and OUT_DIR/background@2x.png.
"""

from __future__ import annotations

import sys
from pathlib import Path

import AppKit

WIDTH, HEIGHT = 600, 400

# Icon centres, matching the positions make-dmg.sh gives Finder.
APP_CENTER = (150, 190)
APPS_CENTER = (450, 190)


def _draw(scale: int) -> AppKit.NSBitmapImageRep:
    px_w, px_h = WIDTH * scale, HEIGHT * scale
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px_w, px_h, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0
    )
    ctx = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    AppKit.NSGraphicsContext.saveGraphicsState()
    AppKit.NSGraphicsContext.setCurrentContext_(ctx)

    transform = AppKit.NSAffineTransform.transform()
    transform.scaleBy_(scale)
    transform.concat()

    # Soft vertical gradient, light enough for dark text in any Finder theme.
    gradient = AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.98, 0.98, 0.99, 1.0),
        AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.90, 0.91, 0.95, 1.0),
    )
    gradient.drawInRect_angle_(AppKit.NSMakeRect(0, 0, WIDTH, HEIGHT), 270.0)

    # Title
    title_attrs = {
        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_weight_(
            26, AppKit.NSFontWeightSemibold
        ),
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedWhite_alpha_(
            0.12, 1.0
        ),
    }
    title = AppKit.NSAttributedString.alloc().initWithString_attributes_("WhisperMe", title_attrs)
    size = title.size()
    title.drawAtPoint_(AppKit.NSMakePoint((WIDTH - size.width) / 2, HEIGHT - 72))

    # Instruction
    sub_attrs = {
        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(13),
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedWhite_alpha_(
            0.35, 1.0
        ),
    }
    sub = AppKit.NSAttributedString.alloc().initWithString_attributes_(
        "Drag WhisperMe into your Applications folder", sub_attrs
    )
    size = sub.size()
    sub.drawAtPoint_(AppKit.NSMakePoint((WIDTH - size.width) / 2, HEIGHT - 104))

    # Arrow between the two icons, stopping clear of both.
    arrow_color = AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.55, 1.0)
    arrow_color.setStroke()
    arrow_color.setFill()

    start_x = APP_CENTER[0] + 62
    end_x = APPS_CENTER[0] - 62
    y = APP_CENTER[1] + 4
    head = 13.0

    shaft = AppKit.NSBezierPath.bezierPath()
    shaft.setLineWidth_(3.0)
    shaft.setLineCapStyle_(AppKit.NSLineCapStyleRound)
    shaft.moveToPoint_(AppKit.NSMakePoint(start_x, y))
    shaft.lineToPoint_(AppKit.NSMakePoint(end_x - head + 2, y))
    shaft.stroke()

    head_path = AppKit.NSBezierPath.bezierPath()
    head_path.moveToPoint_(AppKit.NSMakePoint(end_x, y))
    head_path.lineToPoint_(AppKit.NSMakePoint(end_x - head, y + head * 0.62))
    head_path.lineToPoint_(AppKit.NSMakePoint(end_x - head, y - head * 0.62))
    head_path.closePath()
    head_path.fill()

    # Footnote
    note_attrs = {
        AppKit.NSFontAttributeName: AppKit.NSFont.systemFontOfSize_(11),
        AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithCalibratedWhite_alpha_(
            0.5, 1.0
        ),
    }
    note = AppKit.NSAttributedString.alloc().initWithString_attributes_(
        "Then launch it from Applications — WhisperMe lives in the menu bar.", note_attrs
    )
    size = note.size()
    note.drawAtPoint_(AppKit.NSMakePoint((WIDTH - size.width) / 2, 40))

    AppKit.NSGraphicsContext.restoreGraphicsState()
    return rep


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)

    for scale, name in ((1, "background.png"), (2, "background@2x.png")):
        rep = _draw(scale)
        data = rep.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
        path = out_dir / name
        data.writeToFile_atomically_(str(path), True)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
