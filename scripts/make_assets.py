"""
Generate the bundled binary assets (icons + sounds) without any external
artwork: Pillow draws the icons, the wave module synthesizes the chimes.

Run:  python scripts/make_assets.py
Output: ui/resources/app_icon.png, app_icon.ico, tray_*.png, sounds/*.wav
"""

from __future__ import annotations

import math
import os
import struct
import wave
from typing import Optional

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    raise SystemExit("Pillow is required:  pip install pillow")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "ui", "resources")
SOUNDS = os.path.join(RES, "sounds")
os.makedirs(SOUNDS, exist_ok=True)

ACCENT = (233, 69, 96)          # #e94560
BG_TOP = (22, 33, 62)           # #16213e
BG_BOTTOM = (15, 52, 96)        # #0f3460
WHITE = (255, 255, 255)
GREEN = (0, 184, 148)
AMBER = (253, 203, 110)


def _vertical_gradient(size: int, top, bottom) -> Image.Image:
    img = Image.new("RGBA", (size, size))
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        for x in range(size):
            px[x, y] = color + (255,)
    return img


def _rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return mask


def draw_app_icon(size: int) -> Image.Image:
    """Blue gradient tile + accent circle + white download arrow."""
    base = _vertical_gradient(size, BG_TOP, BG_BOTTOM)
    mask = _rounded_mask(size, int(size * 0.22))
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(base, (0, 0), mask)
    draw = ImageDraw.Draw(icon)

    cx = size / 2
    cy = size * 0.50
    r = size * 0.30
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT + (255,))

    # download arrow
    aw = size * 0.055                       # arrow shaft half-width
    ax = cx
    top = cy - r * 0.62
    bottom = cy + r * 0.30
    draw.rectangle([ax - aw, top, ax + aw, bottom], fill=WHITE + (255,))
    head_h = r * 0.52
    head_w = r * 0.62
    draw.polygon([
        (ax - head_w, bottom),
        (ax + head_w, bottom),
        (ax, cy + r * 0.78),
    ], fill=WHITE + (255,))
    # "tray" line under the arrow
    lw = max(2, int(size * 0.05))
    line_y = cy + r * 0.92
    draw.rounded_rectangle([cx - r * 0.52, line_y - lw / 2,
                            cx + r * 0.52, line_y + lw / 2],
                           radius=lw / 2, fill=WHITE + (255,))
    return icon


def draw_tray_icon(size: int, state: str) -> Image.Image:
    """Small tray glyph: circle + arrow (accent), pause bar (amber)."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2
    r = size * 0.44
    color = ACCENT + (255,)
    if state == "idle":
        color = (90, 130, 190, 255)
    elif state == "paused":
        color = AMBER + (255,)
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    if state == "paused":
        bw = size * 0.09
        bh = size * 0.30
        bx = cx - bw * 1.9
        draw.rounded_rectangle([bx, cy - bh / 2, bx + bw, cy + bh / 2],
                               radius=bw / 2, fill=WHITE + (255,))
        bx2 = cx + bw * 0.9
        draw.rounded_rectangle([bx2, cy - bh / 2, bx2 + bw, cy + bh / 2],
                               radius=bw / 2, fill=WHITE + (255,))
    else:
        aw = size * 0.075
        top = cy - r * 0.55
        bottom = cy + r * 0.25
        draw.rectangle([cx - aw, top, cx + aw, bottom], fill=WHITE + (255,))
        hw, hh = r * 0.55, r * 0.45
        draw.polygon([(cx - hw, bottom), (cx + hw, bottom),
                      (cx, cy + r * 0.68)], fill=WHITE + (255,))
    return img


def make_icon_family() -> None:
    # PNG (window + tray sources)
    app_256 = draw_app_icon(256)
    app_256.save(os.path.join(RES, "app_icon.png"))
    app_64 = draw_app_icon(64)
    app_64.save(os.path.join(RES, "app_icon_64.png"))

    # ICO with multiple sizes (PyInstaller --icon source)
    ico_sizes = [16, 24, 32, 48, 64, 128, 256]
    app_256.save(
        os.path.join(RES, "app_icon.ico"),
        format="ICO", sizes=[(s, s) for s in ico_sizes],
    )
    # also drop a copy in build/ for the NSIS installer & build scripts
    build_dir = os.path.join(ROOT, "build")
    os.makedirs(build_dir, exist_ok=True)
    import shutil
    shutil.copy(os.path.join(RES, "app_icon.ico"),
                os.path.join(build_dir, "app_icon.ico"))

    # tray frames
    for state in ("idle", "downloading", "paused"):
        draw_tray_icon(64, state).save(os.path.join(RES, f"tray_{state}.png"))
    print("icons written to", RES)


# ---------------------------------------------------------------------------
# Sounds (synthesized – no external audio files needed)
# ---------------------------------------------------------------------------

def _tone(freq: float, seconds: float, volume: float = 0.5,
          fade: float = 0.05) -> list[float]:
    sr = 44100
    n = int(sr * seconds)
    out = []
    for i in range(n):
        t = i / sr
        env = 1.0
        if t < fade:
            env = t / fade
        if t > seconds - fade:
            env = max(0.0, (seconds - t) / fade)
        sample = (math.sin(2 * math.pi * freq * t) * 0.7 +
                  math.sin(2 * math.pi * freq * 2 * t) * 0.25 +
                  math.sin(2 * math.pi * freq * 3 * t) * 0.05)
        out.append(sample * volume * env)
    return out


def _write_wav(path: str, samples: list[float], sr: int = 44100) -> None:
    with wave.open(path, "w") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sr)
        frames = bytearray()
        for s in samples:
            value = int(max(-1.0, min(1.0, s)) * 32767)
            frames += struct.pack("<h", value)
        wav.writeframes(bytes(frames))


def make_sounds() -> None:
    sr = 44100

    # completion: bright rising two-note chime (E5 → A5)
    complete = _tone(659.25, 0.14, 0.45) + _tone(880.0, 0.30, 0.45)
    _write_wav(os.path.join(SOUNDS, "complete.wav"), complete)

    # failure: low falling two-note (A4 → E4)
    fail = _tone(440.0, 0.16, 0.45) + _tone(329.63, 0.34, 0.45)
    _write_wav(os.path.join(SOUNDS, "fail.wav"), fail)

    # start: short neutral blip
    start = _tone(523.25, 0.09, 0.35)
    _write_wav(os.path.join(SOUNDS, "start.wav"), start)
    print("sounds written to", SOUNDS)


if __name__ == "__main__":
    make_icon_family()
    make_sounds()
    print("all assets generated")
