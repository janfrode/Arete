#!/usr/bin/env python3
import os
import sys

# ---------------------------------------------------------------------------
# Auto re-exec under the project venv if invoked with the wrong Python.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_VENV_PYTHON = os.path.join(_HERE, ".venv", "bin", "python3")
if os.path.abspath(sys.executable) != os.path.abspath(_VENV_PYTHON):
    if os.path.exists(_VENV_PYTHON):
        os.execv(_VENV_PYTHON, [_VENV_PYTHON] + sys.argv)
    else:
        print(
            "ERROR: .venv not found. Run ./build_dmg.sh once to set it up,\n"
            "       or activate the venv manually before running generate_icon.py.",
            file=sys.stderr,
        )
        sys.exit(1)

import subprocess
from AppKit import (
    NSImage, NSBezierPath, NSColor, NSGradient, NSGraphicsContext,
    NSRect, NSPoint, NSSize, NSPNGFileType, NSBitmapImageRep
)

def draw_icon():
    """Draw the Arete icon: three horizontal time bars (Gantt-style) on a dark navy squircle."""
    size = 1024
    img = NSImage.alloc().initWithSize_(NSSize(size, size))
    img.lockFocus()

    from AppKit import NSShadow
    ctx = NSGraphicsContext.currentContext()
    ctx.setShouldAntialias_(True)

    inset = 100
    cs    = size - 2 * inset   # 824
    rect  = NSRect(NSPoint(inset, inset), NSSize(cs, cs))
    cr    = cs * 0.225
    bg    = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, cr, cr)

    # Drop shadow
    ctx.saveGraphicsState()
    sh = NSShadow.alloc().init()
    sh.setShadowOffset_(NSSize(0, -15))
    sh.setShadowBlurRadius_(28.0)
    sh.setShadowColor_(NSColor.colorWithCalibratedWhite_alpha_(0.0, 0.45))
    sh.set()
    NSColor.whiteColor().set()
    bg.fill()
    ctx.restoreGraphicsState()

    # Background gradient — deep navy
    g = NSGradient.alloc().initWithStartingColor_endingColor_(
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.08, 0.11, 0.18, 1.0),
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.03, 0.04, 0.08, 1.0),
    )
    g.drawInBezierPath_angle_(bg, -80.0)

    # Arête mountain silhouette — sharp ridge behind the bars
    # Coordinate system: y increases upward (AppKit convention).
    # The squircle spans y = inset … inset+cs.  We want the peak near the top.
    mx      = size / 2.0
    top     = inset + cs              # top edge of squircle
    peak_y  = top  - cs * 0.10       # sharp summit: 10 % down from top
    floor_y = inset + cs * 0.38      # base: ~38 % up — bars sit above this
    left_x  = inset + cs * 0.00
    right_x = inset + cs * 1.00

    # Ridgeline: left base → foothills → sub-peak → SUMMIT → sub-peak → foothills → right base
    ridge = [
        (left_x,              floor_y),
        (inset + cs * 0.18,   inset + cs * 0.60),
        (inset + cs * 0.32,   inset + cs * 0.75),
        (mx    - cs * 0.07,   inset + cs * 0.82),
        (mx,                  peak_y),               # sharp summit
        (mx    + cs * 0.07,   inset + cs * 0.82),
        (inset + cs * 0.68,   inset + cs * 0.72),
        (inset + cs * 0.82,   inset + cs * 0.57),
        (right_x,             floor_y),
    ]

    mountain = NSBezierPath.bezierPath()
    mountain.moveToPoint_(NSPoint(ridge[0][0], ridge[0][1]))
    for (px, py) in ridge[1:]:
        mountain.lineToPoint_(NSPoint(px, py))
    mountain.closePath()

    # Fill: dark blue-slate, semi-transparent so gradient shows through
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.09, 0.13, 0.26, 0.65).set()
    mountain.fill()

    # Ridgeline stroke — soft blue-white highlight along the ridge
    ridgeline = NSBezierPath.bezierPath()
    ridgeline.moveToPoint_(NSPoint(ridge[0][0], ridge[0][1]))
    for (px, py) in ridge[1:]:
        ridgeline.lineToPoint_(NSPoint(px, py))
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.75, 1.00, 0.40).set()
    ridgeline.setLineWidth_(4.0)
    ridgeline.setLineJoinStyle_(0)   # NSMiterLineJoinStyle — keeps peaks sharp
    ridgeline.stroke()

    # Three horizontal bars
    cx     = size / 2.0
    cy     = size / 2.0
    bar_h  = cs * 0.085
    gap    = cs * 0.065
    radius = bar_h / 2.0
    left   = inset + cs * 0.12
    right  = inset + cs * 0.88

    bars = [
        (0.00, 0.82, (0.20, 0.55, 0.90, 1.0)),   # top    — nearly full, blue
        (0.18, 1.00, (0.15, 0.78, 0.72, 1.0)),   # middle — offset, teal
        (0.00, 0.52, (0.35, 0.70, 0.98, 1.0)),   # bottom — shorter, "live"
    ]
    bar_width = right - left
    centres   = [cy + bar_h + gap, cy, cy - bar_h - gap]

    for i, ((x0f, x1f, rgba), bar_cy) in enumerate(zip(bars, centres)):
        x0 = left + bar_width * x0f
        x1 = left + bar_width * x1f
        w  = x1 - x0
        r  = NSRect(NSPoint(x0, bar_cy - bar_h / 2), NSSize(w, bar_h))
        p  = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(r, radius, radius)
        NSColor.colorWithCalibratedRed_green_blue_alpha_(*rgba).set()
        p.fill()

        # Glowing live-indicator dot on the bottom bar
        if i == 2:
            dot_r = bar_h * 0.65
            dot = NSBezierPath.bezierPathWithOvalInRect_(NSRect(
                NSPoint(x1 - dot_r, bar_cy - dot_r), NSSize(dot_r * 2, dot_r * 2)
            ))
            ctx.saveGraphicsState()
            sh2 = NSShadow.alloc().init()
            sh2.setShadowOffset_(NSSize(0, 0))
            sh2.setShadowBlurRadius_(22.0)
            sh2.setShadowColor_(NSColor.colorWithCalibratedRed_green_blue_alpha_(0.35, 0.70, 0.98, 0.85))
            sh2.set()
            NSColor.whiteColor().set()
            dot.fill()
            ctx.restoreGraphicsState()

    img.unlockFocus()

    img_data = img.TIFFRepresentation()
    bitmap   = NSBitmapImageRep.imageRepWithData_(img_data)
    png_data = bitmap.representationUsingType_properties_(NSPNGFileType, None)
    png_data.writeToFile_atomically_("icon.png", True)
    print("Successfully generated high-resolution 1024x1024 icon.png!")

if __name__ == "__main__":
    draw_icon()

    # Build the .icns file from the generated icon.png
    import shutil
    ICONSET = "Arete.iconset"
    os.makedirs(ICONSET, exist_ok=True)

    # All required sizes and their retina equivalents
    sizes = [16, 32, 128, 256, 512]
    for s in sizes:
        subprocess.run(["sips", "-z", str(s), str(s), "icon.png", "--out", f"{ICONSET}/icon_{s}x{s}.png"], check=True, capture_output=True)
        subprocess.run(["sips", "-z", str(s * 2), str(s * 2), "icon.png", "--out", f"{ICONSET}/icon_{s}x{s}@2x.png"], check=True, capture_output=True)

    subprocess.run(["iconutil", "-c", "icns", ICONSET, "-o", "Arete.icns"], check=True)

    # Cleanup and downsample icon.png to 128x128 for README use
    shutil.rmtree(ICONSET)
    subprocess.run(["sips", "-z", "128", "128", "icon.png", "--out", "icon.png"], check=True, capture_output=True)

    print("Successfully generated Arete.icns and icon.png (128x128)!")
