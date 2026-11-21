#!/usr/bin/env python3
"""Grok Bot Video Studio: brand system for strobe ads (code-drawn, 0 generations).

Everything is driven by a brand name and ONE accent hex colour; nothing is brand-specific.

  vstudio_brand_frames.py frames SPEC.json OUTDIR [--preview]
      Render message/texture frames from a spec (layouts: swiss, terminal, type, statement,
      list, index, halftone, flash, rings). Writes OUTDIR/<id>.png (no wordmark; the strobe
      cutter overlays it) and OUTDIR/frames.json (manifest fragment with role, key, words).
      --preview also writes OUTDIR/preview/<id>.png with the wordmark + grain pass.
  vstudio_brand_frames.py composite IMAGE OUT.png --name NAME --accent HEX [--aspect 9:16]
                                     [--focus 0.5,0.4] [--clear-y 0.54] [--eyes-y 0.35] [--label]
      Gate-1 composite: fit a still to the frame, keep everything above --clear-y (a 0-1 fraction of the
      source height: the nose tip) and --eyes-y (the eye line) off the wordmark by moving the crop, then shifting,
      overlay the wordmark (auto black/white + halo), grain + halftone.
  vstudio_brand_frames.py contact OUT.jpg IMG [IMG ...] [--cols 4] [--width 1600]
      Labelled contact sheet for approval gates.

Spec (JSON):
  {"brand": {"name": "NORTHWIND LAB", "accent": "#FF4D00", "mark": "square"},
   "aspect": "9:16",
   "frames": [
     {"id": "M01", "layout": "swiss", "lines": ["A RESEARCH", "LAB"], "sub": ["SMALL BODY LINE"],
      "corner": ["NORTHWIND / NOTE", "N° 01"], "bg": "paper", "key": true},
     {"id": "M02", "layout": "terminal", "rows": [["CONTEXT", "1M TOKENS"]], "headline": "BIG LINE", "bar": "ACCENT BAR"},
     {"id": "M03", "layout": "type", "word": "OPEN", "sub": "TOOLS IN THE OPEN", "bg": "ink"},
     {"id": "M04", "layout": "statement", "lines": ["FINAL", "SAY"], "punch": ["STAYS", "HUMAN."], "bg": "ink", "key": true},
     {"id": "M05", "layout": "list", "title": "THREE TEAMS", "items": ["ALPHA", "BETA"], "footer": "ONE LINE"},
     {"id": "M06", "layout": "index", "items": ["A", "B", "C", "D"], "footer": "LINE"},
     {"id": "T1", "layout": "halftone", "bg": "paper", "fg": "ink"},
     {"id": "T2", "layout": "flash", "bg": "accent"},
     {"id": "T3", "layout": "rings"}]}
  Colours: "ink" (near-black), "paper" (off-white), "accent", or any "#RRGGBB".
  Words on message frames must come only from the owner's copy (the skill enforces this).

Layout rule: every text block sits in the TOP zone (above the wordmark band) or the BOTTOM zone
(below it, above the bottom platform-UI area); the centre band stays clear for the fixed wordmark.

Fonts (first found in $VSTUDIO_FONTS, /usr/share/fonts, $VSTUDIO_HOME/fonts, ~/.local/share/fonts):
display Anton > Archivo Black > Inter (900) > DejaVu Sans Bold; wordmark Archivo Black > Inter (900) >
DejaVu Sans Bold; mono IBM Plex Mono Bold > DejaVu Sans Mono Bold. Override with spec "fonts":
{"display": path, "wordmark": path, "mono": path}.
Uses Pillow and numpy only.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
INK = (12, 12, 12)
PAPER = (238, 236, 230)
GREY = (150, 156, 160)
RULE = (200, 198, 192)
MARKS = ("square", "dot", "asterisk", "none")
LABEL_TEXT = "AI GENERATED"

FONT_DIRS = [d for d in (os.environ.get("VSTUDIO_FONTS"), "/usr/share/fonts",
                         os.path.join(os.environ.get("VSTUDIO_HOME", "/workspace/vstudio"), "fonts"),
                         os.path.expanduser("~/.local/share/fonts")) if d]
FONT_CHAINS = {
    "display": [("Anton-Regular.ttf", None), ("ArchivoBlack-Regular.ttf", None),
                ("Inter-VariableFont_opsz,wght.ttf", 900), ("DejaVuSans-Bold.ttf", None)],
    "wordmark": [("ArchivoBlack-Regular.ttf", None), ("Inter-VariableFont_opsz,wght.ttf", 900),
                 ("DejaVuSans-Bold.ttf", None)],
    "mono": [("IBMPlexMono-Bold.ttf", None), ("DejaVuSansMono-Bold.ttf", None), ("DejaVuSans-Bold.ttf", None)],
    "monoreg": [("IBMPlexMono-Regular.ttf", None), ("DejaVuSansMono.ttf", None), ("DejaVuSans.ttf", None)],
}


class BrandError(ValueError):
    pass


# ---------------------------------------------------------------- colour + brand
def parse_hex(h):
    """'#FF4D00' / 'ff4d00' / '#f40' -> (r, g, b)."""
    s = str(h).strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", s):
        s = "".join(c * 2 for c in s)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", s):
        raise BrandError(f"accent must be a hex colour like #FF4D00 (got {h!r})")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def luminance(rgb):
    r, g, b = (c / 255.0 for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ink_on(rgb):
    """Readable text colour (INK or PAPER) on a solid background colour."""
    return INK if luminance(rgb) > 0.45 else PAPER


def parse_aspect(a):
    if isinstance(a, (tuple, list)):
        return int(a[0]), int(a[1])
    a = str(a).strip()
    if a in ASPECTS:
        return ASPECTS[a]
    m = re.fullmatch(r"(\d+)x(\d+)", a.lower())
    if m and int(m.group(1)) % 2 == 0 and int(m.group(2)) % 2 == 0:
        return int(m.group(1)), int(m.group(2))
    raise BrandError(f"aspect must be 9:16, 16:9, 1:1 or even WxH (got {a!r})")


class Brand:
    """A brand = name + one accent. size=(W, H). Wordmark is centred at (W/2, H*wm_y)."""

    def __init__(self, name, accent, size=(1080, 1920), mark="square", wm_y=0.5, wm_scale=1.0,
                 fonts=None, ink=INK, paper=PAPER):
        name = " ".join(str(name or "").split())
        if not name:
            raise BrandError("brand name is required")
        if mark not in MARKS:
            raise BrandError(f"mark must be one of {MARKS}")
        self.name, self.accent, self.mark = name.upper(), parse_hex(accent), mark
        self.W, self.H = parse_aspect(size)
        self.wm_y, self.wm_scale = float(wm_y), float(wm_scale)
        self.fonts = dict(fonts or {})
        self.ink, self.paper = tuple(ink), tuple(paper)
        self._wm_cache = {}

    @classmethod
    def from_spec(cls, spec, aspect=None):
        b = dict(spec.get("brand", spec))
        return cls(b.get("name"), b.get("accent"), parse_aspect(aspect or spec.get("aspect", "9:16")),
                   b.get("mark", "square"), b.get("wordmark_y", 0.5), b.get("wordmark_scale", 1.0),
                   spec.get("fonts") or b.get("fonts"))

    def color(self, c):
        if c in (None, "ink"):
            return self.ink
        if c == "paper":
            return self.paper
        if c == "accent":
            return self.accent
        if c == "grey":
            return GREY
        return parse_hex(c)

    # fonts ------------------------------------------------------------
    def font(self, kind, size):
        return load_font(kind, max(8, int(size)), self.fonts.get(kind))

    # wordmark ---------------------------------------------------------
    def wm_size(self):
        return max(16, int(round(104 * min(self.W, self.H) / 1080 * self.wm_scale)))

    def _wm_parts(self):
        words = self.name.split(" ")
        if self.mark == "none" or len(words) == 1:
            return [self.name], self.mark != "none"
        return [words[0], " ".join(words[1:])], True

    def wordmark_layer(self, color, mark_color=None):
        """RGBA layer with the centred wordmark; the mark is in the accent colour (or mark_color).
        Returns (layer, box) where box is the band (x0, y0, x1, y1) incl. a 20 px margin (scaled)."""
        key = (tuple(color), tuple(mark_color) if mark_color else None)
        if key in self._wm_cache:
            return self._wm_cache[key]
        W, H = self.W, self.H
        size = self.wm_size()
        parts, has_mark = self._wm_parts()
        L = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(L)
        while True:
            f = self.font("wordmark", size)
            gap, sq = size * 0.25, size * 0.29
            widths = [d.textlength(p, font=f) for p in parts]
            tot = sum(widths) + (2 * gap + sq if has_mark else 0) * (1 if len(parts) == 2 else 0)
            if has_mark and len(parts) == 1:
                tot += gap * 0.6 + sq
            if tot <= W * 0.86 or size <= 16:
                break
            size -= 2
        cy = H * self.wm_y
        bbox = f.getbbox("HMWX")
        th = bbox[3] - bbox[1]
        ty = cy - bbox[1] - th / 2
        x = (W - tot) / 2
        col = tuple(color) + (255,)
        acc = tuple(mark_color or self.accent) + (255,)

        def mark_at(mx):
            if self.mark == "square":
                d.rectangle([mx, cy - sq / 2 + size * 0.04, mx + sq, cy + sq / 2 + size * 0.04], fill=acc)
            elif self.mark == "dot":
                d.ellipse([mx, cy - sq / 2, mx + sq, cy + sq / 2], fill=acc)
            elif self.mark == "asterisk":
                c0, c1, r = mx + sq / 2, cy, sq * 0.62
                for k in range(6):
                    a = k * math.pi / 6
                    d.line([(c0 - r * math.cos(a), c1 - r * math.sin(a)), (c0 + r * math.cos(a), c1 + r * math.sin(a))],
                           fill=acc, width=max(3, int(size * 0.07)))

        if len(parts) == 2:
            d.text((x, ty), parts[0], font=f, fill=col)
            mx = x + widths[0] + gap
            mark_at(mx)
            d.text((mx + sq + gap, ty), parts[1], font=f, fill=col)
        else:
            d.text((x, ty), parts[0], font=f, fill=col)
            if has_mark:
                mark_at(x + widths[0] + gap * 0.6)
        m = 20 * min(W, H) / 1080
        box = (int(x - m), int(cy - th / 2 - m), int(x + tot + m), int(cy + th / 2 + m))
        self._wm_cache[key] = (L, box)
        return L, box

    def band(self):
        """(top, bottom) of the wordmark band in px."""
        _, b = self.wordmark_layer(self.ink)
        return b[1], b[3]

    def zones(self):
        """Text zones (y0, y1) above and below the band; the bottom zone stops above platform UI."""
        top, bot = self.band()
        m = 0.03 * self.H
        tz = (int(0.136 * self.H), int(top - m))
        bz = (int(bot + m), int(self.H * (0.74 if self.H > self.W else 0.86)))
        return tz, bz

    def overlay(self, img, force=None, halo_std=45.0):
        """Auto black/white wordmark from the luminance behind it; a soft opposite-colour halo when the
        background is busy (std > halo_std). Returns (image, info)."""
        img = fit_image(img, (self.W, self.H)) if img.size != (self.W, self.H) else img.convert("RGB")
        _, box = self.wordmark_layer(self.ink)
        crop = img.crop(box)
        g = np.asarray(crop.convert("L"), dtype=np.float32)
        lum, std = float(g.mean()), float(g.std())
        color = self.color(force) if force else (self.ink if lum > 128 else self.paper)
        # the accent mark would vanish on an accent-coloured background: draw it in the text colour
        mean_rgb = np.asarray(crop.convert("RGB"), dtype=np.float32).reshape(-1, 3).mean(0)
        on_accent = float(np.abs(mean_rgb - np.array(self.accent)).sum()) < 90
        L, _ = self.wordmark_layer(color, color if on_accent else None)
        out = img.convert("RGBA")
        halo = std > halo_std
        if halo:
            opp = self.paper if color == self.ink else self.ink
            S, _ = self.wordmark_layer(opp)
            S = S.filter(ImageFilter.GaussianBlur(9 * min(self.W, self.H) / 1080))
            a = np.asarray(S).copy()
            a[..., 3] = np.clip(a[..., 3].astype(np.float32) * 1.6, 0, 200).astype(np.uint8)
            out.alpha_composite(Image.fromarray(a))
        out.alpha_composite(L)
        return out.convert("RGB"), {"color": "black" if color == self.ink else "white",
                                    "lum": round(lum, 1), "std": round(std, 1), "halo": halo, "mark_on_accent": on_accent}

    # eye line ---------------------------------------------------------
    def safe_shift(self, clear_y_px, margin=None):
        """Pixels to move an image UP so everything down to clear_y_px (eyes, or nose tip) sits above the
        wordmark band with a margin. 0 when already clear. Never more than 30% of the frame."""
        top, _ = self.band()
        margin = 0.02 * self.H if margin is None else margin
        return int(max(0, min(0.3 * self.H, clear_y_px + margin - top)))

    def eye_line_clear(self, eyes_y_px, shift=0):
        top, bot = self.band()
        y = eyes_y_px - shift
        return not (top <= y <= bot)

    def label(self, img, text=LABEL_TEXT):
        """Small boxed 'AI GENERATED' label, right-aligned, above the platform-UI zone."""
        img = img.copy()
        d = ImageDraw.Draw(img)
        s = min(self.W, self.H) / 1080
        f = self.font("mono", 24 * s)
        w = d.textlength(text, font=f)
        x = self.W - 60 * s - w
        y = self.H * (1372 / 1920 if self.H > self.W else 0.86)
        lum = np.asarray(img.crop((int(x - 14 * s), int(y - 8 * s), int(x + w + 14 * s), int(y + 34 * s))).convert("L")).mean()
        col = self.ink if lum > 140 else self.paper
        d.rectangle([x - 14 * s, y - 8 * s, x + w + 14 * s, y + 34 * s], outline=col, width=max(1, int(2 * s)))
        d.text((x, y), text, font=f, fill=col)
        return img

    def label_box(self, text=LABEL_TEXT):
        s = min(self.W, self.H) / 1080
        f = self.font("mono", 24 * s)
        w = ImageDraw.Draw(Image.new("RGB", (8, 8))).textlength(text, font=f)
        x = self.W - 60 * s - w
        y = self.H * (1372 / 1920 if self.H > self.W else 0.86)
        return int(x - 14 * s), int(y - 8 * s), int(x + w + 14 * s), int(y + 34 * s)


_font_cache = {}


def _find(fname):
    for d in FONT_DIRS:
        g = sorted(glob.glob(f"{glob.escape(d)}/**/{glob.escape(fname)}", recursive=True))
        if g:
            return g[0]
    return None


def font_path(kind, override=None):
    if override:
        if not Path(override).exists():
            raise BrandError(f"font not found: {override}")
        return override, None
    for fname, weight in FONT_CHAINS[kind]:
        p = _find(fname)
        if p:
            return p, weight
    raise BrandError(f"no font for {kind}; install DejaVu (fonts-dejavu-core)")


def load_font(kind, size, override=None):
    k = (kind, size, override)
    if k not in _font_cache:
        p, weight = font_path(kind, override)
        f = ImageFont.truetype(p, size)
        if weight:
            try:
                f.set_variation_by_axes([32, weight] if "opsz" in os.path.basename(p) else [weight])
            except Exception:
                pass
        _font_cache[k] = f
    return _font_cache[k]


# ---------------------------------------------------------------- image passes
def fit_image(img, size, focus=(0.5, 0.4), crop=None, zoom=1.0):
    """Cover-fit to size around a focus point; optional source crop box and extra zoom."""
    im = img.convert("RGB")
    if crop:
        im = im.crop(tuple(int(v) for v in crop))
    if zoom and zoom > 1.0:
        im = _zoom_crop(im, focus, zoom)
    return ImageOps.fit(im, tuple(size), Image.LANCZOS, centering=tuple(focus))


def _zoom_crop(im, focus, zoom):
    """Punch-in crop (1/zoom of each side) around the focus point, at source resolution."""
    w, h = im.size
    cw, ch = w / zoom, h / zoom
    x0 = min(max(0, focus[0] * w - cw / 2), w - cw)
    y0 = min(max(0, focus[1] * h - ch / 2), h - ch)
    return im.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch)))


def fit_clear(img, brand, focus=(0.5, 0.4), crop=None, zoom=1.0, clear_y=None, margin=None, eyes_y=None):
    """Cover-fit like fit_image, keeping the face off the wordmark band. clear_y = lowest point to keep above the
    band (eyes, or the nose tip), eyes_y = the eye line; both are 0-1 fractions of the SOURCE height (after
    crop/zoom). The crop window moves first (when the source is taller than the frame), then the picture shifts up.
    With eyes_y, the eyes always stay in frame and above the band; clear_y is then best effort (a wide frame
    can't always fit eyes and nose above the centre band). Returns (image, info)."""
    im = img.convert("RGB")
    if crop:
        im = im.crop(tuple(int(v) for v in crop))
    if zoom and zoom > 1.0:
        im = _zoom_crop(im, focus, zoom)
    W, H = brand.W, brand.H
    w, h = im.size
    sc = max(W / w, H / h)
    cw, ch = W / sc, H / sc
    left = (w - cw) * focus[0]
    top = (h - ch) * focus[1]
    info = {"window_top": 0, "shift": 0, "clear_px": None, "eyes_px": None, "clear_ok": None}
    band_top, _ = brand.band()
    m = 0.02 * H if margin is None else margin
    keep = 0.06 * H  # eyes stay at least this far below the top edge
    if clear_y is not None:
        cy = float(clear_y) * h
        if (cy - top) * sc > band_top - m:
            top = max(top, min(h - ch, cy - (band_top - m) / sc))
    if eyes_y is not None:
        ey = float(eyes_y) * h
        top = min(top, max(0.0, ey - keep / sc))                   # eyes back in frame
        top = max(top, min(h - ch, ey - (band_top - m) / sc))      # eyes above the band
    out = im.resize((W, H), Image.LANCZOS, box=(left, top, left + cw, top + ch))
    if clear_y is not None or eyes_y is not None:
        target = float(clear_y if clear_y is not None else eyes_y) * h
        sh = brand.safe_shift((target - top) * sc, margin)
        if eyes_y is not None:
            efy = (float(eyes_y) * h - top) * sc
            sh = max(brand.safe_shift(efy, margin), min(sh, int(max(0, efy - keep))))
            info["eyes_px"] = round(efy - sh, 1)
        out = shift_up(out, sh)
        fy = (target - top) * sc - sh
        info.update(window_top=round(top, 1), shift=sh, clear_px=round(fy, 1), clear_ok=bool(fy <= band_top))
    return out, info


def shift_up(img, px):
    """Move the picture up by px; the freed bottom strip is a mirrored, darkening extension (no hard edge)."""
    if px <= 0:
        return img
    W, H = img.size
    c = Image.new("RGB", (W, H), (0, 0, 0))
    c.paste(img, (0, -px))
    strip = np.asarray(img.crop((0, H - px - 1, W, H)).transpose(Image.FLIP_TOP_BOTTOM), dtype=np.float32)
    g = np.linspace(1, 0.15, strip.shape[0])[:, None, None]
    c.paste(Image.fromarray((strip * g).astype("uint8")), (0, H - px - 1))
    return c


def duotone(img, brand, split=0.55):
    """Ink -> accent -> paper duotone (the one-accent look on a photo)."""
    a = np.asarray(img.convert("L"), dtype=np.float32) / 255
    ink, acc, pap = (np.array(c, np.float32) for c in (brand.ink, brand.accent, brand.paper))
    lo = a[..., None] < split
    t1 = (a / split)[..., None]
    t2 = ((a - split) / (1 - split))[..., None]
    out = np.where(lo, ink + (acc - ink) * t1, acc + (pap - acc) * t2)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def invert(img):
    return ImageOps.invert(img.convert("RGB"))


_screen_cache = {}


def _screen(h, w, period=7.0):
    k = (h, w, period)
    if k not in _screen_cache:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        u, v = (xx + yy) / period, (xx - yy) / period
        _screen_cache[k] = ((np.cos(u * np.pi) * np.cos(v * np.pi) + 1) / 2).astype(np.float32)
    return _screen_cache[k]


def halftone_grain(img, amount=0.10, grain=11, seed=1, dots=True):
    """Photocopy texture: a 45-degree halftone dot screen weighted to the shadows, plus mono film grain."""
    a = np.asarray(img.convert("RGB"), dtype=np.float32)
    if dots and amount > 0:
        lum = a.mean(axis=2) / 255.0
        a = a * (1 - amount * (1 - lum) * _screen(*a.shape[:2]))[..., None]
    if grain > 0:
        rng = np.random.default_rng(seed)
        a = a + rng.normal(0, grain, a.shape[:2]).astype(np.float32)[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


# ---------------------------------------------------------------- layouts
def _fit_font(d, brand, kind, text, maxw, start, minimum=10):
    s = int(start)
    while s > minimum and d.textlength(text, font=brand.font(kind, s)) > maxw:
        s -= 2
    return brand.font(kind, s)


def _stack(d, brand, lines, kind, x, y0, y1, maxw, col, start, lh=0.92):
    """Stack lines in [y0, y1]: one common size that fits every line's width and the zone height."""
    if not lines:
        return y0
    s = int(start)
    while s > 10:
        if all(d.textlength(t, font=brand.font(kind, s)) <= maxw for t in lines) and s * lh * len(lines) <= (y1 - y0):
            break
        s -= 2
    f = brand.font(kind, s)
    y = y0
    for t in lines:
        top = f.getbbox(t)[1] if t.strip() else 0
        d.text((x, y - top), t, font=f, fill=col)
        y += int(s * lh)
    return y


def _corners(d, brand, col, left, right):
    if not (left or right):
        return
    s = min(brand.W, brand.H) / 1080
    f = brand.font("monoreg", 20 * s)
    y = brand.H * 200 / 1920 if brand.H > brand.W else brand.H * 0.06
    m = 60 * s
    if left:
        d.text((m, y), left, font=f, fill=col)
    if right:
        d.text((brand.W - m - d.textlength(right, font=f), y), right, font=f, fill=col)


def _small_lines(d, brand, lines, y0, y1, col, start=34, x=None, bullet=False):
    s = min(brand.W, brand.H) / 1080
    x = 60 * s if x is None else x
    maxw = brand.W - x - 60 * s - (40 * s if bullet else 0)
    size = int(start * s)
    while size > 10:
        f = brand.font("mono", size)
        if all(d.textlength(t, font=f) <= maxw for t in lines) and len(lines) * size * 1.6 <= (y1 - y0):
            break
        size -= 2
    f = brand.font("mono", size)
    y = y0
    for t in lines:
        tx = x
        if bullet:
            d.rectangle([x, y + size * 0.25, x + size * 0.65, y + size * 0.9], fill=brand.accent)
            tx = x + size * 1.15
        d.text((tx, y), t, font=f, fill=col)
        y += int(size * 1.6)
    return y


def _canvas(brand, bg):
    im = Image.new("RGB", (brand.W, brand.H), brand.color(bg))
    return im, ImageDraw.Draw(im)


def layout_swiss(brand, fr):
    """Swiss poster: grid rules, corner labels, big stacked headline (top zone), small body (bottom zone)."""
    bg = fr.get("bg", "paper")
    im, d = _canvas(brand, bg)
    fg = ink_on(brand.color(bg))
    s = min(brand.W, brand.H) / 1080
    (t0, t1), (b0, b1) = brand.zones()
    rule = RULE if fg == INK else (50, 54, 58)
    for x in (60 * s, brand.W / 2, brand.W - 60 * s):
        d.line([(x, 0), (x, brand.H)], fill=rule, width=1)
    c = fr.get("corner") or [brand.name, ""]
    _corners(d, brand, fg, c[0], c[1] if len(c) > 1 else "")
    block = fr.get("accent_block", True) and fr.get("lines")
    _stack(d, brand, fr.get("lines", []), "display", 52 * s, t0, t1, brand.W - (192 if block else 112) * s, fg, 220 * s)
    if block:
        bs = 56 * s
        d.rectangle([brand.W - 60 * s - bs, t0, brand.W - 60 * s, t0 + bs], fill=brand.accent)
    if fr.get("sub"):
        _small_lines(d, brand, fr["sub"], b0, b1, fg, 30)
    return im


def layout_terminal(brand, fr):
    """Terminal / data readout: key-value rows (top zone), big headline + accent bar (bottom zone)."""
    im, d = _canvas(brand, fr.get("bg", "#06080A"))
    s = min(brand.W, brand.H) / 1080
    (t0, t1), (b0, b1) = brand.zones()
    for x in range(int(60 * s), brand.W, int(120 * s)):
        d.line([(x, 0), (x, brand.H)], fill=(24, 28, 32), width=1)
    for y in range(0, brand.H, int(120 * s)):
        d.line([(0, y), (brand.W, y)], fill=(24, 28, 32), width=1)
    hdr = fr.get("title", brand.name)
    hb = brand.font("mono", 24 * s)
    d.rectangle([60 * s, t0, 84 * s, t0 + 24 * s], fill=brand.accent)
    d.text((100 * s, t0 - 4 * s), hdr, font=hb, fill=brand.paper)
    rows = fr.get("rows", [])
    y = t0 + 60 * s
    if rows:
        rh = min(78 * s, (t1 - y) / max(1, len(rows)))
        fk = brand.font("monoreg", min(30 * s, rh * 0.42))
        fv = brand.font("mono", min(30 * s, rh * 0.42))
        hl = set(fr.get("highlight", []))
        for k, v in rows:
            d.text((60 * s, y), k, font=fk, fill=GREY)
            d.text((brand.W * 0.48, y), v, font=fv, fill=brand.accent if k in hl else brand.paper)
            d.line([(60 * s, y + rh * 0.8), (brand.W - 60 * s, y + rh * 0.8)], fill=(34, 38, 42), width=1)
            y += rh
    yb = b0
    if fr.get("headline"):
        hl_lines = fr["headline"] if isinstance(fr["headline"], list) else [fr["headline"]]
        yb = _stack(d, brand, hl_lines, "display", 56 * s, b0, b0 + (b1 - b0) * 0.6, brand.W - 120 * s, brand.paper, 200 * s, 1.0)
    if fr.get("bar"):
        bh = min(74 * s, max(30 * s, b1 - yb - 10 * s))
        d.rectangle([60 * s, yb + 8 * s, brand.W - 60 * s, yb + 8 * s + bh], fill=brand.accent)
        fz = _fit_font(d, brand, "wordmark", fr["bar"], brand.W - 160 * s, bh * 0.6)
        d.text((80 * s, yb + 8 * s + bh / 2), fr["bar"], font=fz, fill=ink_on(brand.accent), anchor="lm")
    return im


def layout_type(brand, fr):
    """Pure type: one huge word (top zone, centred) + optional small sub line (bottom zone)."""
    bg = fr.get("bg", "paper")
    im, d = _canvas(brand, bg)
    fg = brand.color(fr["fg"]) if fr.get("fg") else ink_on(brand.color(bg))
    if bg == "ink" and not fr.get("fg"):
        fg = brand.paper
    s = min(brand.W, brand.H) / 1080
    (t0, t1), (b0, b1) = brand.zones()
    word = fr.get("word", "")
    f = _fit_font(d, brand, "display", word, brand.W - 80 * s, 700 * s)
    while f.size > 12:
        bb = d.textbbox((0, 0), word, font=f)
        if bb[3] - bb[1] <= (t1 - t0):
            break
        f = brand.font("display", f.size - 4)
    bb = d.textbbox((0, 0), word, font=f)
    d.text(((brand.W - (bb[2] - bb[0])) / 2 - bb[0], (t0 + t1) / 2 - (bb[3] - bb[1]) / 2 - bb[1]), word, font=f, fill=fg)
    if fr.get("sub"):
        sc = brand.color(fr["sub_color"]) if fr.get("sub_color") else fg
        _small_lines(d, brand, [fr["sub"]] if isinstance(fr["sub"], str) else fr["sub"], b0, b1, sc, 30)
    return im


def layout_statement(brand, fr):
    """Big statement: stacked headline (top zone) and a punch line whose last line is in the accent (bottom)."""
    bg = fr.get("bg", "ink")
    im, d = _canvas(brand, bg)
    fg = brand.paper if bg == "ink" else ink_on(brand.color(bg))
    s = min(brand.W, brand.H) / 1080
    (t0, t1), (b0, b1) = brand.zones()
    c = fr.get("corner")
    if c:
        _corners(d, brand, GREY if bg == "ink" else fg, c[0], c[1] if len(c) > 1 else "")
    _stack(d, brand, fr.get("lines", []), "display", 56 * s, t0, t1, brand.W - 112 * s, fg, 300 * s, 0.95)
    punch = fr.get("punch", [])
    if punch:
        s2 = int(170 * s)
        while s2 > 10 and (any(d.textlength(t, font=brand.font("display", s2)) > brand.W - 120 * s for t in punch)
                           or s2 * 0.95 * len(punch) > (b1 - b0)):
            s2 -= 2
        f = brand.font("display", s2)
        y = b0
        acc = brand.accent if brand.accent != brand.color(bg) else fg
        for i, t in enumerate(punch):
            d.text((60 * s, y - f.getbbox(t)[1]), t, font=f, fill=acc if i == len(punch) - 1 else fg)
            y += int(s2 * 0.95)
    return im


def layout_list(brand, fr):
    """Title (top zone) + accent-bulleted items + footer (bottom zone)."""
    bg = fr.get("bg", "ink")
    im, d = _canvas(brand, bg)
    fg = brand.paper if bg == "ink" else ink_on(brand.color(bg))
    s = min(brand.W, brand.H) / 1080
    (t0, t1), (b0, b1) = brand.zones()
    c = fr.get("corner")
    if c:
        _corners(d, brand, GREY, c[0], c[1] if len(c) > 1 else "")
    items = fr.get("items", [])
    title = [fr["title"]] if fr.get("title") else []
    y = _stack(d, brand, title, "display", 60 * s, t0, t0 + (t1 - t0) * 0.35, brand.W - 120 * s, fg, 150 * s, 1.0)
    if items:
        avail = t1 - y - 10 * s
        rh = min(128 * s, avail / len(items))
        f = _fit_font(d, brand, "wordmark", max(items, key=len), brand.W - 240 * s, rh * 0.56)
        for t in items:
            d.rectangle([60 * s, y + rh * 0.18, 60 * s + rh * 0.27, y + rh * 0.45], fill=brand.accent)
            d.text((60 * s + rh * 0.47, y), t, font=f, fill=fg)
            y += rh
    if fr.get("footer"):
        foot = fr["footer"] if isinstance(fr["footer"], list) else [fr["footer"]]
        _small_lines(d, brand, foot, b0, b1, fg, 34, bullet=fr.get("bullets", False))
    return im


def layout_index(brand, fr):
    """UI index grid of boxed labels (top zone; 2 columns) + one footer line (bottom zone)."""
    bg = fr.get("bg", "paper")
    im, d = _canvas(brand, bg)
    fg = ink_on(brand.color(bg))
    s = min(brand.W, brand.H) / 1080
    (t0, t1), (b0, b1) = brand.zones()
    c = fr.get("corner") or [brand.name, ""]
    _corners(d, brand, fg, c[0], c[1] if len(c) > 1 else "")
    items = fr.get("items", [])
    rows = max(1, math.ceil(len(items) / 2))
    bw = (brand.W - 140 * s) / 2
    bh = min(130 * s, (t1 - t0) / rows - 20 * s)
    longest = max(items, key=len) if items else ""
    f = _fit_font(d, brand, "mono", longest, bw - 50 * s, min(28 * s, bh * 0.3))
    fn = brand.font("monoreg", max(10, int(min(18 * s, bh * 0.18))))
    hl = set(fr.get("highlight", []))
    for i, t in enumerate(items):
        cc, r = i % 2, i // 2
        x, y = 60 * s + cc * (bw + 20 * s), t0 + r * (bh + 20 * s)
        d.rectangle([x, y, x + bw, y + bh], outline=fg, width=2)
        if t in hl:
            d.rectangle([x, y, x + 16 * s, y + bh], fill=brand.accent)
        d.text((x + 30 * s, y + 8 * s), f"{i + 1:02d}", font=fn, fill=fg)
        d.text((x + 30 * s, y + bh * 0.42), t, font=f, fill=fg)
    if fr.get("footer"):
        _small_lines(d, brand, [fr["footer"]], b0, b1, fg, 36)
    return im


def layout_halftone(brand, fr):
    """Halftone dot field texture (no text)."""
    bg, fg = fr.get("bg", "paper"), fr.get("fg", "ink")
    im, d = _canvas(brand, bg)
    col = brand.color(fg)
    s = min(brand.W, brand.H) / 1080
    step = int(34 * s)
    for gy in range(0, brand.H, step):
        for gx in range(0, brand.W, step):
            r = 14 * s * (1 - abs(gy - brand.H / 2) / (brand.H / 2)) ** 1.5 * (0.5 + 0.5 * math.sin(gx / (90 * s)))
            if r > 1.2:
                d.ellipse([gx - r, gy - r, gx + r, gy + r], fill=col)
    return im


def layout_flash(brand, fr):
    """Solid flash frame (accent, paper or ink); optional tiny caption in the bottom zone."""
    bg = fr.get("bg", "accent")
    im, d = _canvas(brand, bg)
    if fr.get("caption"):
        (_, _), (b0, b1) = brand.zones()
        _small_lines(d, brand, [fr["caption"]], b0, b1, ink_on(brand.color(bg)), 20)
    return im


def layout_rings(brand, fr):
    """Concentric alternating rings (no text)."""
    a, b = brand.color(fr.get("fg", "ink")), brand.color(fr.get("bg", "paper"))
    im, d = _canvas(brand, fr.get("bg", "paper"))
    cx, cy = brand.W // 2, brand.H // 2
    step = int(70 * min(brand.W, brand.H) / 1080)
    for i, r in enumerate(range(int(max(brand.W, brand.H) * 0.6), 0, -step)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=a if i % 2 == 0 else b)
    return im


LAYOUTS = {"swiss": layout_swiss, "terminal": layout_terminal, "type": layout_type, "statement": layout_statement,
           "list": layout_list, "index": layout_index, "halftone": layout_halftone, "flash": layout_flash,
           "rings": layout_rings}
TEXTURE_LAYOUTS = {"halftone", "flash", "rings"}


READ_KEYS = {"swiss": ("lines",), "terminal": ("headline", "bar"), "type": ("word",), "statement": ("lines", "punch"),
             "list": ("title", "items"), "index": ("items",)}


def frame_words(fr):
    """Words a viewer must read on this frame (drives the key-line hold): the big text only, not the small
    print. A spec "words" value overrides the count."""
    if "words" in fr:
        return int(fr["words"])
    parts = []
    for k in READ_KEYS.get(fr.get("layout"), ()):
        v = fr.get(k)
        if isinstance(v, str):
            parts.append(v)
        elif isinstance(v, list):
            parts += [x if isinstance(x, str) else " ".join(x) for x in v]
    return len(re.findall(r"[\w][\w,.'’\-–]*", " ".join(parts)))


def render_frame(brand, fr):
    lay = fr.get("layout")
    if lay not in LAYOUTS:
        raise BrandError(f"unknown layout {lay!r}; use one of {sorted(LAYOUTS)}")
    return LAYOUTS[lay](brand, fr)


def render_spec(spec, outdir, preview=False, aspect=None):
    brand = Brand.from_spec(spec, aspect)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    entries = []
    ids = set()
    for fr in spec.get("frames", []):
        fid = str(fr.get("id", ""))
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", fid) or fid in ids:
            raise BrandError(f"frame id must be unique [A-Za-z0-9_-] (got {fid!r})")
        ids.add(fid)
        im = render_frame(brand, fr)
        p = out / f"{fid}.png"
        im.save(p)
        tex = fr["layout"] in TEXTURE_LAYOUTS
        words = 0 if tex else frame_words(fr)
        entries.append({"id": fid, "path": p.name, "kind": "still", "role": "texture" if tex else "message",
                        "text": not tex and fr["layout"] != "flash", "key": bool(fr.get("key", False)), "words": words})
        if preview:
            (out / "preview").mkdir(exist_ok=True)
            pv, _ = brand.overlay(im)
            halftone_grain(pv, 0.06, 9, seed=len(fid)).save(out / "preview" / f"{fid}.png")
    (out / "frames.json").write_text(json.dumps({"note": "asset paths are relative to this file", "brand": {"name": brand.name, "accent": "#%02X%02X%02X" % brand.accent,
                                                           "mark": brand.mark},
                                                 "size": [brand.W, brand.H], "assets": entries}, indent=1))
    return entries


def composite(src, out, brand, focus=(0.5, 0.4), clear_y=None, label=False, seed=1, eyes_y=None):
    im, fi = fit_clear(Image.open(src), brand, focus, clear_y=clear_y, eyes_y=eyes_y)
    sh = fi["shift"]
    o, info = brand.overlay(im)
    o = halftone_grain(o, 0.10, 12, seed=seed)
    if label:
        o = brand.label(o)
    o.save(out)
    info["shift"] = sh
    info.update(clear_px=fi["clear_px"], eyes_px=fi["eyes_px"], clear_ok=fi["clear_ok"])
    return info


def contact(out, paths, cols=4, width=1600):
    ims = [Image.open(p).convert("RGB") for p in paths]
    if not ims:
        raise BrandError("no images")
    cw = width // cols
    ch = int(cw * ims[0].size[1] / ims[0].size[0])
    rows = math.ceil(len(ims) / cols)
    sheet = Image.new("RGB", (cw * cols, (ch + 28) * rows), (20, 20, 20))
    d = ImageDraw.Draw(sheet)
    f = load_font("monoreg", 16)
    for i, (p, im) in enumerate(zip(paths, ims)):
        x, y = (i % cols) * cw, (i // cols) * (ch + 28)
        sheet.paste(ImageOps.fit(im, (cw - 6, ch), Image.LANCZOS), (x + 3, y))
        d.text((x + 6, y + ch + 5), Path(p).stem[:40], font=f, fill=(230, 230, 230))
    sheet.save(out, quality=90)
    return out


def _pair(s):
    a, b = (float(v) for v in s.split(","))
    return a, b


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("frames")
    f.add_argument("spec"); f.add_argument("outdir"); f.add_argument("--preview", action="store_true")
    f.add_argument("--aspect")
    c = sub.add_parser("composite")
    c.add_argument("image"); c.add_argument("out")
    c.add_argument("--name", required=True); c.add_argument("--accent", required=True)
    c.add_argument("--mark", default="square", choices=MARKS); c.add_argument("--aspect", default="9:16")
    c.add_argument("--focus", type=_pair, default=(0.5, 0.4)); c.add_argument("--clear-y", type=float); c.add_argument("--eyes-y", type=float)
    c.add_argument("--label", action="store_true")
    k = sub.add_parser("contact")
    k.add_argument("out"); k.add_argument("images", nargs="+")
    k.add_argument("--cols", type=int, default=4); k.add_argument("--width", type=int, default=1600)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "frames":
            e = render_spec(json.loads(Path(a.spec).read_text()), a.outdir, a.preview, a.aspect)
            print(json.dumps({"frames": len(e), "key": [x["id"] for x in e if x["key"]]}))
        elif a.cmd == "composite":
            b = Brand(a.name, a.accent, parse_aspect(a.aspect), a.mark)
            print(json.dumps(composite(a.image, a.out, b, a.focus, a.clear_y, a.label, eyes_y=a.eyes_y)))
        else:
            print(contact(a.out, a.images, a.cols, a.width))
    except (BrandError, OSError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
