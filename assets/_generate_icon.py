"""
Atlas Phase 3 — iOS app icon generator.

Renders a stylized globe + compass-needle design at 1024×1024 master, then
downsamples to the full iOS app icon set (1024 / 180 / 120 / 60 plus the
@2x / @3x variants for 20 / 29 / 40 / 60 notification + spotlight + settings
sizes that Xcode expects when no .xcassets / Contents.json is shipped).

Spec: SPEC.md §12.
  - 1024×1024 master, no transparency, no rounded corners
  - Background: deep navy #0F172A (subtle vertical gradient for depth)
  - Globe strokes: cyan/teal #22D3EE
  - Compass needle: white #F8FAFC (subtle, behind the globe)
  - 29×29 notification size must remain legible — single-tone, no busy detail
"""
from __future__ import annotations
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

# -- palette (from SPEC §12) --
NAVY_TOP   = (15, 23, 42)     # #0F172A — top of vertical gradient
NAVY_BOT   = (2, 6, 23)       # darker bottom for subtle depth
CYAN       = (34, 211, 238)   # #22D3EE — globe strokes
WHITE      = (248, 250, 252)  # #F8FAFC — compass needle

ASSETS = Path(__file__).parent
MASTER_SIZE = 1024

# -- canvas geometry (fractions of master, kept simple so the design scales) --
GLOBE_CX, GLOBE_CY = 0.50, 0.54
GLOBE_R = 0.32
GLOBE_STROKE = 0.018  # stroke width as fraction of master (= ~18px on 1024)

# Compass needle: behind the globe, rotated NE-SW (45°)
NEEDLE_LEN = 0.62  # tip-to-tip length
NEEDLE_WIDTH = 0.05
NEEDLE_ANGLE_DEG = 45  # NE (white tip) to SW (lighter white tip)


def _rgba(c, a=255):
    return (c[0], c[1], c[2], a)


def _draw_vertical_gradient(size: int) -> Image.Image:
    """Deep navy gradient (slightly lighter at top-center for depth)."""
    base = Image.new("RGB", (size, size), NAVY_TOP)
    top = Image.new("RGB", (size, size), NAVY_BOT)
    # vertical mask: 0 at top, 255 at bottom — base is the lighter top color
    mask = Image.linear_gradient("L").resize((1, size)).convert("L")
    mask = mask.resize((size, size))
    grad = Image.composite(top, base, mask)
    return grad


def _draw_globe(canvas: Image.Image):
    s = canvas.size[0]
    cx, cy = s * GLOBE_CX, s * GLOBE_CY
    r = s * GLOBE_R
    stroke = max(2, int(s * GLOBE_STROKE))
    d = ImageDraw.Draw(canvas, "RGBA")

    # outer circle
    d.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        outline=_rgba(CYAN, 255),
        width=stroke,
    )
    # equator (horizontal diameter)
    d.line([(cx - r, cy), (cx + r, cy)], fill=_rgba(CYAN, 255), width=stroke)

    # two longitude ellipses (tilted)
    # an ellipse with rx = r, ry = r * sin(tilt), rotated about the center
    tilt1 = math.radians(28)
    tilt2 = math.radians(-28)
    for tilt in (tilt1, tilt2):
        ry = r * math.sin(tilt)
        # rotate an unrotated vertical ellipse by `tilt` rad
        # Simpler: draw the ellipse with rx=r*cos(tilt), ry=r, then rotate
        # But PIL's ellipse bbox draws axis-aligned. Use the rotate trick:
        # draw onto a temp, paste-rotate.
        tmp = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        td = ImageDraw.Draw(tmp)
        rx = r * abs(math.cos(tilt))
        td.ellipse(
            [cx - rx, cy - r, cx + rx, cy + r],
            outline=_rgba(CYAN, 255),
            width=stroke,
        )
        # PIL rotates CCW; we want the tilt applied to the ellipse's vertical
        # axis. The ellipse's narrow axis is the rotated one.
        # The trick: the ellipse as drawn has its narrow axis along x. If
        # we rotate by `tilt` (positive = CCW), the narrow axis tilts up
        # to the right. For a globe this is fine — both directions read
        # the same visually.
        rotated = tmp.rotate(math.degrees(tilt), resample=Image.BICUBIC, center=(cx, cy))
        canvas.alpha_composite(rotated)


def _draw_compass_needle(canvas: Image.Image):
    """Subtle NE/SW needle behind the globe. White north tip brighter, SW dimmer."""
    s = canvas.size[0]
    cx, cy = s * 0.50, s * 0.54  # aligned with globe center
    L = s * NEEDLE_LEN
    W = s * NEEDLE_WIDTH
    angle = math.radians(NEEDLE_ANGLE_DEG)

    # Needle endpoints in local coords (needle points up before rotation):
    # North tip at (0, -L/2), South tip at (0, +L/2).
    # We render as two triangles (top half = brighter, bottom half = dimmer).
    needle_layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    nd = ImageDraw.Draw(needle_layer)

    # Build needle in local space, centered, then rotate to the final angle.
    # North half (brighter, pure white)
    nd.polygon(
        [
            (cx, cy - L / 2),       # tip up
            (cx - W / 2, cy),       # base left
            (cx + W / 2, cy),       # base right
        ],
        fill=_rgba(WHITE, 230),
    )
    # South half (dimmer — uses a slightly desaturated white)
    nd.polygon(
        [
            (cx, cy + L / 2),       # tip down
            (cx - W / 2, cy),       # base left
            (cx + W / 2, cy),       # base right
        ],
        fill=_rgba((180, 200, 220), 160),  # soft cool gray, not pure white
    )
    # Rotate to final orientation (NE-SW axis)
    rotated = needle_layer.rotate(-NEEDLE_ANGLE_DEG, resample=Image.BICUBIC, center=(cx, cy))
    canvas.alpha_composite(rotated)


def render_master(size: int = MASTER_SIZE) -> Image.Image:
    """Render the full 1024×1024 master with no transparency, no rounded corners."""
    bg = _draw_vertical_gradient(size).convert("RGBA")
    _draw_compass_needle(bg)
    _draw_globe(bg)
    # iOS does its own rounding — leave corners square.
    return bg


# -- iOS icon set spec (effective sizes, source of truth) --
# Each tuple: (filename, pixel size, no transparency)
IOS_SIZES = [
    # Phase-3 deliverable: 60, 120, 180, 1024 (per task body)
    ("icon-60.png",     60),
    ("icon-60@2x.png",  120),
    ("icon-60@3x.png",  180),
    ("icon-1024.png",   1024),
    # Full iOS app icon set Xcode also wants when no Contents.json is shipped
    ("icon-20.png",     20),
    ("icon-20@2x.png",  40),
    ("icon-20@3x.png",  60),
    ("icon-29.png",     29),
    ("icon-29@2x.png",  58),
    ("icon-29@3x.png",  87),
    ("icon-40.png",     40),
    ("icon-40@2x.png",  80),
    ("icon-40@3x.png",  120),
]


def main():
    print(f"Rendering master {MASTER_SIZE}×{MASTER_SIZE}…")
    master = render_master(MASTER_SIZE)

    # 1) Drop the master at the path Expo's `icon` field references.
    master_path = ASSETS / "icon.png"
    master.save(master_path, "PNG", optimize=True)
    print(f"  → {master_path.relative_to(ASSETS.parent)}  ({master_path.stat().st_size:,} bytes)")

    # 2) Render the iOS app icon set into assets/icon-ios/
    icon_ios = ASSETS / "icon-ios"
    icon_ios.mkdir(exist_ok=True)
    for name, size in IOS_SIZES:
        out = icon_ios / name
        # Use LANCZOS for clean downsample; for sizes that match master
        # exactly, just copy.
        if size == MASTER_SIZE:
            rendered = master
        else:
            rendered = master.resize((size, size), Image.LANCZOS)
        rendered.save(out, "PNG", optimize=True)
        print(f"  → icon-ios/{name}  {size}×{size}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
