"""vkit.py - Video Studio visual kit core (Pillow, numpy, ffmpeg; no downloads, 0 generations).
Chalkboard look, code-drawn chalk-chef mascot, kitchen shots, burned-in captions, parallel BT.709 render,
and a tiny vector-PDF writer for printable templates."""
import glob, math, subprocess, os
from multiprocessing import Pool
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
FPS = 30
FONT_DIRS = [d for d in (os.environ.get('VSTUDIO_FONTS'), '/usr/share/fonts', os.path.join(os.environ.get('VSTUDIO_HOME', '/workspace/vstudio'), 'fonts'),
                          os.path.expanduser('~/.local/share/fonts')) if d]
def _f(pat, fb='DejaVuSans-Bold.ttf'):
    """First match of pat across FONT_DIRS (install.sh puts pinned, sha256-checked copies in $VSTUDIO_HOME/fonts), else DejaVu."""
    for p in (pat, fb):
        for d in FONT_DIRS:
            g = sorted(glob.glob(f'{d}/**/{p}', recursive=True))
            if g: return g[0]
    raise FileNotFoundError(f'no font {pat} or {fb}')
CHALK_FONT, HAND_FONT, SANS_FONT = _f('CaveatBrush-Regular.ttf'), _f('Kalam-Bold.ttf'), _f('Inter-VariableFont_opsz,wght.ttf')
_fc = {}
def font(kind, size, weight=800):
    k = (kind, size, weight)
    if k not in _fc:
        f = ImageFont.truetype({'chalk': CHALK_FONT, 'hand': HAND_FONT, 'sans': SANS_FONT}[kind], size)
        if kind == 'sans':
            try: f.set_variation_by_axes([32, weight])
            except Exception: pass
        _fc[k] = f
    return _fc[k]
CH = {'white': (240, 238, 228), 'yellow': (247, 214, 92), 'pink': (240, 140, 160), 'blue': (130, 196, 240),
      'green': (150, 220, 140), 'orange': (245, 160, 80), 'red': (235, 95, 85)}
def ease(x): x = max(0., min(1., x)); return 1 - (1 - x) ** 3
def board(W, H, seed=3):
    """Slate chalkboard with soft eraser smudges (build once per size)."""
    r = np.random.default_rng(seed)
    base = np.zeros((H, W, 3), np.float32) + np.array([34, 48, 43], np.float32)
    sm = Image.fromarray((r.random((H // 16, W // 16)) * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC).filter(ImageFilter.GaussianBlur(40))
    base += (np.asarray(sm, np.float32)[..., None] / 255 - .5) * 22
    base += r.normal(0, 3, (H, W, 1))
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).convert('RGBA')
_grain = {}
def chalkify(layer, seed=0):
    """Multiply a layer's alpha by a chalk grain so lines and letters read as chalk."""
    k = (layer.size, seed)
    if k not in _grain:
        r = np.random.default_rng(seed + 11)
        g = r.random((layer.size[1], layer.size[0])).astype(np.float32)
        _grain[k] = np.clip(.55 + .6 * g, 0, 1)
    a = np.asarray(layer.split()[3], np.float32) * _grain[k]
    layer.putalpha(Image.fromarray(a.astype(np.uint8))); return layer
def ctext(d, xy, text, size, col='white', kind='chalk', anchor='la', maxw=None):
    f = font(kind, size)
    if maxw:
        while d.textlength(text, font=f) > maxw and size > 20: size -= 4; f = font(kind, size)
    d.text(xy, text, font=f, fill=CH.get(col, col) if isinstance(col, str) else col, anchor=anchor)
    return d.textlength(text, font=f)
def chef(d, cx, cy, s=1.0, mouth=0.0, pose='idle', blink=False, wave=0.0):
    """Chalk chef mascot. cx,cy = chin centre; s = scale (1.0 is ~520 px tall); mouth 0..1 open;
    pose: idle | point | wave | thumbs. Draw on a transparent layer, then chalkify()."""
    W = lambda v: max(2, int(v * s)); P = lambda x, y: (cx + x * s, cy + y * s)
    w, y_, pk, bl = CH['white'], CH['yellow'], CH['pink'], CH['blue']
    for x, y, r in ((-70, -250, 55), (0, -285, 65), (70, -250, 55)):                       # toque puffs
        d.ellipse([*P(x - r, y - r), *P(x + r, y + r)], fill=(34, 48, 43, 255), outline=w, width=W(7))
    d.rectangle([*P(-95, -215), *P(95, -165)], fill=(34, 48, 43, 255), outline=w, width=W(7))
    d.ellipse([*P(-105, -175), *P(105, 20)], fill=(34, 48, 43, 255), outline=w, width=W(7))  # head
    for ex in (-42, 42):
        if blink: d.line([*P(ex - 14, -95), *P(ex + 14, -95)], fill=w, width=W(7))
        else: d.ellipse([*P(ex - 11, -108), *P(ex + 11, -84)], fill=w)
    for ex in (-66, 66): d.ellipse([*P(ex - 18, -78), *P(ex + 18, -58)], fill=pk + (150,))
    d.line([*P(0, -80), *P(-8, -55), *P(6, -52)], fill=w, width=W(5), joint='curve')        # nose
    for sg in (-1, 1): d.arc([*P(sg * 34 - 34, -52), *P(sg * 34 + 34, -22)], 190, 350, fill=y_, width=W(9))  # moustache
    if mouth > .08:
        h = 8 + 30 * mouth
        d.ellipse([*P(-22, -14), *P(22, -14 + h)], fill=(90, 30, 40, 255), outline=w, width=W(5))
    else: d.arc([*P(-28, -26), *P(28, 2)], 20, 160, fill=w, width=W(6))
    d.polygon([P(-55, 30), P(55, 30), P(140, 80), P(150, 260), P(-150, 260), P(-140, 80)], fill=(34, 48, 43, 255), outline=w)
    d.line([P(-55, 30), P(-140, 80), P(-150, 260), P(150, 260), P(140, 80), P(55, 30)], fill=w, width=W(7))
    d.polygon([P(-30, 30), P(0, 70), P(30, 30)], outline=bl, fill=bl)                         # neckerchief
    for by in (110, 160, 210):
        for bx in (-30, 30): d.ellipse([*P(bx - 7, by - 7), *P(bx + 7, by + 7)], fill=w)
    d.rectangle([*P(-90, 170), *P(90, 260)], outline=y_, width=W(6))                          # apron
    def arm(sx, ang1, ang2, L1=95, L2=85, hand=True):
        a1, a2 = math.radians(ang1), math.radians(ang2)
        ex, ey = sx + L1 * math.cos(a1), 95 + L1 * math.sin(a1)
        hx, hy = ex + L2 * math.cos(a2), ey + L2 * math.sin(a2)
        d.line([P(sx, 95), P(ex, ey), P(hx, hy)], fill=w, width=W(14), joint='curve')
        if hand: d.ellipse([*P(hx - 18, hy - 18), *P(hx + 18, hy + 18)], fill=w)
        return hx, hy
    arm(-140, 115, 95)                                                                         # left arm down
    if pose == 'point': hx, hy = arm(140, -20, -35); d.line([P(hx, hy), P(hx + 40, hy - 28)], fill=w, width=W(10))
    elif pose == 'wave': arm(140, -40, -80 + 25 * math.sin(wave * 2 * math.pi))
    elif pose == 'thumbs': hx, hy = arm(140, 20, -70); d.line([P(hx, hy - 10), P(hx, hy - 50)], fill=w, width=W(12))
    else: arm(140, 65, 95)
def ticket_rail(d, x, y, w, n, hot=0, s=1.0):
    """Ticket rail with n tickets; the last `hot` tickets are drawn red (late)."""
    d.line([(x, y), (x + w, y)], fill=CH['white'], width=int(8 * s))
    tw, gap = int(78 * s), int(14 * s)
    for i in range(n):
        tx = x + 20 + i * (tw + gap) // (1 if n * (tw + gap) < w else 2)
        col = CH['red'] if i >= n - hot else CH['white']
        d.rectangle([tx, y + 6, tx + tw, y + int(150 * s)], outline=col, width=int(5 * s))
        for k in range(3): d.line([(tx + 12, y + int((40 + 30 * k) * s)), (tx + tw - 12, y + int((40 + 30 * k) * s))], fill=col, width=int(3 * s))
def stations(d, x, y, w, h, names, hi=(), s=1.0):
    """A row of line stations (boxes with chalk labels); indexes in hi are drawn yellow."""
    n = len(names); bw = (w - (n - 1) * 20) / n
    for i, nm in enumerate(names):
        bx = x + i * (bw + 20); col = 'yellow' if i in hi else 'white'
        d.rounded_rectangle([bx, y, bx + bw, y + h], radius=int(18 * s), outline=CH[col], width=int(6 * s))
        ctext(d, (bx + bw / 2, y + h / 2), nm, int(54 * s), col, anchor='mm', maxw=bw - 20)
def walk_in(d, x, y, w, h, s=1.0):
    d.rectangle([x, y, x + w, y + h], outline=CH['blue'], width=int(7 * s))
    for k in range(1, 4): d.line([(x + 20, y + k * h / 4), (x + w * .6, y + k * h / 4)], fill=CH['white'], width=int(5 * s))
    d.rectangle([x + w * .66, y + 20, x + w - 20, y + h - 20], outline=CH['white'], width=int(5 * s))
    d.ellipse([x + w * .68, y + h / 2 - 12, x + w * .68 + 24, y + h / 2 + 12], fill=CH['white'])
    ctext(d, (x + w / 2, y - 14), 'WALK-IN', int(48 * s), 'blue', anchor='md')
def prep_list(d, x, y, w, items, s=1.0):
    """items = [(text, state)] with state '' | 'ok' | 'low'."""
    lh = int(64 * s); d.rounded_rectangle([x, y, x + w, y + 90 * s + lh * len(items)], radius=int(16 * s), outline=CH['white'], width=int(6 * s))
    ctext(d, (x + 24, y + 14), 'PREP LIST', int(50 * s), 'yellow')
    for i, (t, st) in enumerate(items):
        yy = y + 82 * s + i * lh; d.rectangle([x + 24, yy + 10, x + 24 + 34 * s, yy + 10 + 34 * s], outline=CH['white'], width=int(4 * s))
        if st == 'ok': d.line([(x + 30, yy + 28 * s), (x + 40 * s, yy + 40 * s), (x + 62 * s, yy + 6 * s)], fill=CH['green'], width=int(7 * s))
        ctext(d, (x + 76 * s, yy + 2), t, int(44 * s), 'red' if st == 'low' else 'white', maxw=w - 100 * s)
def caption(img, text, y, maxw, size=50):
    """Burned-in caption: white Inter on a dark rounded box, centred, max 2 lines."""
    d = ImageDraw.Draw(img); f = font('sans', size, 760); words, lines, cur = text.split(), [], ''
    for wd in words:
        t = (cur + ' ' + wd).strip()
        if d.textlength(t, font=f) <= maxw: cur = t
        else: lines.append(cur); cur = wd
    lines.append(cur); assert len(lines) <= 2, ('caption too long', text)
    lh = int(size * 1.28); bw = max(d.textlength(l, font=f) for l in lines) + 56; bh = lh * len(lines) + 30
    W = img.size[0]; ov = Image.new('RGBA', img.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    od.rounded_rectangle([(W - bw) / 2, y, (W + bw) / 2, y + bh], radius=22, fill=(0, 0, 0, 175))
    for i, l in enumerate(lines): od.text((W / 2, y + 15 + i * lh), l, font=f, fill=(255, 255, 255), anchor='ma')
    img.alpha_composite(ov)
def chip(d, x, y, text, col=(247, 214, 92), size=34, ink=(20, 20, 20)):
    f = font('sans', size, 850); w = d.textlength(text, font=f)
    d.rounded_rectangle([x, y, x + w + 36, y + size + 22], radius=(size + 22) // 2, fill=col); d.text((x + 18, y + 9), text, font=f, fill=ink)
    return w + 36
_FN = None
def Path_write(path, data):
    with open(path, 'wb') as fh: fh.write(data)
def _work(i): return _FN(i / FPS).convert('RGB').tobytes()
def render_par(frame_fn, W, H, dur, audio, out_mp4, workers=6, lufs=-14.0):
    """Render frame_fn(t)->RGBA in parallel (fork), pipe to ffmpeg, convert to real BT.709 and tag it.
    frame_fn must depend only on t. Then two-pass loudnorm to `lufs` (None = skip) into out_mp4."""
    global _FN; _FN = frame_fn; n = int(round(dur * FPS)); final, out_mp4 = out_mp4, out_mp4 + '.raw.mp4'
    workers = min(workers, os.cpu_count() or 2)
    p = subprocess.Popen(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}',
        '-r', str(FPS), '-i', '-', '-i', audio, '-t', f'{dur:.3f}', '-vf', 'scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv',
        '-c:v', 'libx264', '-crf', '23', '-preset', 'medium', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
        '-color_range', 'tv', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', out_mp4], stdin=subprocess.PIPE)
    with Pool(workers) as pool:
        for b in pool.imap(_work, range(n), chunksize=8): p.stdin.write(b)
    p.stdin.close(); assert p.wait() == 0, 'ffmpeg failed'
    if lufs is None: os.replace(out_mp4, final)
    else: normalize(out_mp4, final, lufs); os.remove(out_mp4)
    return n
class PDF:
    """Minimal vector PDF (Letter, Helvetica): text, lines, boxes. Coordinates in points from TOP-left."""
    def __init__(s): s.pages, s.ops = [], None
    def page(s): s.ops = []; s.pages.append(s.ops)
    def _y(s, y): return 792 - y
    def text(s, x, y, t, size=11, bold=False, gray=0):
        t = t.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        s.ops.append(f'{gray} g BT /{"F2" if bold else "F1"} {size} Tf {x:.1f} {s._y(y):.1f} Td ({t}) Tj ET')
    def line(s, x1, y1, x2, y2, w=0.8, gray=0): s.ops.append(f'{gray} G {w} w {x1:.1f} {s._y(y1):.1f} m {x2:.1f} {s._y(y2):.1f} l S')
    def box(s, x, y, w, h, lw=0.8, gray=0, fill=None):
        if fill is not None: s.ops.append(f'{fill} g {x:.1f} {s._y(y + h):.1f} {w:.1f} {h:.1f} re f')
        s.ops.append(f'{gray} G {lw} w {x:.1f} {s._y(y + h):.1f} {w:.1f} {h:.1f} re S')
    def save(s, path, title='Template'):
        objs = ['<< /Type /Catalog /Pages 2 0 R >>', None,
                '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>',
                '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>']
        kids = []
        for ops in s.pages:
            st = '\n'.join(ops).encode('latin-1', 'replace')
            objs.append(f'<< /Length {len(st)} >>\nstream\n'.encode('latin-1') + st + b'\nendstream')
            objs.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {len(objs)} 0 R /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> >>')
            kids.append(f'{len(objs)} 0 R')
        objs[1] = f'<< /Type /Pages /Kids [{" ".join(kids)}] /Count {len(kids)} >>'
        objs.append(f'<< /Title ({title}) /Producer (Grok Bot Video Studio vkit) >>')
        out, offs = bytearray(b'%PDF-1.4\n'), []
        for i, o in enumerate(objs, 1):
            offs.append(len(out)); out += f'{i} 0 obj\n'.encode() + (o if isinstance(o, bytes) else o.encode('latin-1')) + b'\nendobj\n'
        x = len(out); out += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode() + b''.join(f'{o:010d} 00000 n \n'.encode() for o in offs)
        out += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\nstartxref\n{x}\n%%EOF\n'.encode()
        Path_write(path, out)
def printable(path, title, subtitle, sections, footer):
    """Fill-in sheet (Letter, vector). sections = [(heading, columns, widths, rows, blank_rows)]; a cell '[ ]' draws a tick box.
    Widths are points and add to 516. A new page starts when a section would run off the page."""
    p = PDF(); p.page(); y = 64
    def head(y):
        p.text(48, y, title, 22, True); p.text(48, y + 22, subtitle, 10.5, gray=.35)
        p.text(48, y + 46, 'Date: ____________    Name: ________________________    Shift: __________', 10.5); return y + 64
    def foot(): p.line(48, 744, 564, 744, 0.4, .6); p.text(48, 760, footer, 8.5, gray=.4)
    y = head(y); rh = 22
    for heading, cols, widths, rows, blank in sections:
        allrows = rows + [[''] * len(cols)] * blank
        if y + rh * (len(allrows) + 2) > 730: foot(); p.page(); y = head(64)
        if heading: p.text(48, y + 14, heading, 13, True); y += 22
        p.box(48, y, 516, rh, 0.8, fill=.9); xx = 48
        for c, w in zip(cols, widths): p.text(xx + 6, y + 15, c, 9.5, True); xx += w
        for r in allrows:
            y += rh; xx = 48; p.box(48, y, 516, rh, 0.5)
            for c, w in zip(r, widths):
                if c == '[ ]': p.box(xx + 8, y + 5, 12, 12, 0.8)
                else: p.text(xx + 6, y + 15, c, 9.5)
                xx += w; p.line(xx, y, xx, y + rh, 0.4, .5)
        y += rh + 18
    foot(); p.save(path, title); return len(p.pages)
def _measure(path, lufs=-14.0, tp=-1.5):
    import json, re
    r = subprocess.run(['ffmpeg', '-hide_banner', '-i', path, '-af', f'loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json', '-f', 'null', '-'], capture_output=True, text=True).stderr
    return json.loads(re.findall(r'\{[^{}]+\}', r)[-1])
def normalize(src, dst, lufs=-14.0, tp=-1.5):
    """Two-pass ffmpeg loudnorm (linear) on the audio; video (already BT.709-tagged) is copied. If linear gain can't
    reach the target under the peak limit, fall back to gain + alimiter. Returns the measured result of dst."""
    m = _measure(src, lufs, tp)
    def run(af): subprocess.run(['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error', '-i', src, '-c:v', 'copy', '-af', af, '-ar', '48000',
                                 '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', dst], check=True)
    run(f"loudnorm=I={lufs}:TP={tp}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    o = _measure(dst, lufs, tp)
    if abs(float(o['input_i']) - lufs) > 0.7 or float(o['input_tp']) > tp + 0.4:
        g = lufs - float(m['input_i']); lim = 10 ** ((tp - 0.6) / 20)
        run(f'volume={g:.2f}dB,alimiter=limit={lim:.4f}:attack=5:release=60:level=0,aresample=48000'); o = _measure(dst, lufs, tp)
    return {'lufs': float(o['input_i']), 'true_peak': float(o['input_tp'])}
# ---------- expressive Chalk Chef (v2): IK arms, moods, props, squash & stretch ----------
def _ik(sx, sy, hx, hy, L1=95, L2=88, bend=1):
    dx, dy = hx - sx, hy - sy; D = max(1e-3, min(L1 + L2 - 1, math.hypot(dx, dy)))
    a = math.atan2(dy, dx); b = math.acos(max(-1, min(1, (L1 * L1 + D * D - L2 * L2) / (2 * L1 * D))))
    return sx + L1 * math.cos(a - bend * b), sy + L1 * math.sin(a - bend * b), sx + dx * D / math.hypot(dx, dy or 1e-3) if (dx or dy) else hx, sy + dy * D / math.hypot(dx or 1e-3, dy) if (dx or dy) else hy
POSES = {  # hand targets (left, right) relative to chin centre at s=1; shoulders at (-140,95) and (140,95)
    'idle': ((-150, 262), (150, 262)), 'point': ((-150, 262), (300, -10)), 'facepalm': ((-150, 262), (38, -78)),
    'idea': ((-150, 262), (205, -170)), 'work': ((-165, 175), (215, 60)), 'celebrate': ((-235, -175), (235, -175)),
    'wave': ((-150, 262), (250, -110)), 'thumbs': ((-150, 262), (215, 60)), 'shrug': ((-245, 40), (245, 40))}
def chef2(d, cx, cy, s=1.0, pose='idle', t=0.0, mood='smile', blink=False, bulb=0.0):
    """Expressive Chalk Chef. pose in POSES; mood smile|grin|frown|o|smirk; bulb 0..1 draws a lightbulb over the head.
    The mouth is a fixed expression (never flapped), so it is safe under voiceover."""
    W = lambda v_: max(2, int(v_ * s)); P = lambda x, y: (cx + x * s, cy + y * s); FILL = (34, 48, 43, 255)
    w, y_, pk, bl = CH['white'], CH['yellow'], CH['pink'], CH['blue']
    for x, y, r in ((-70, -250, 55), (0, -285, 65), (70, -250, 55)): d.ellipse([*P(x - r, y - r), *P(x + r, y + r)], fill=FILL, outline=w, width=W(7))
    d.rectangle([*P(-95, -215), *P(95, -165)], fill=FILL, outline=w, width=W(7))
    d.polygon([P(-55, 30), P(55, 30), P(140, 80), P(150, 260), P(-150, 260), P(-140, 80)], fill=FILL)
    d.line([P(-55, 30), P(-140, 80), P(-150, 260), P(150, 260), P(140, 80), P(55, 30)], fill=w, width=W(7))
    d.ellipse([*P(-105, -175), *P(105, 20)], fill=FILL, outline=w, width=W(7))
    for ex in (-42, 42):
        if blink or mood == 'frown' and pose == 'facepalm': d.line([*P(ex - 14, -95), *P(ex + 14, -95)], fill=w, width=W(7))
        elif mood == 'grin': d.arc([*P(ex - 16, -108), *P(ex + 16, -80)], 200, 340, fill=w, width=W(7))
        else: r = 14 if mood == 'o' else 11; d.ellipse([*P(ex - r, -96 - r - 1), *P(ex + r, -96 + r - 1)], fill=w)
    for ex in (-66, 66): d.ellipse([*P(ex - 18, -78), *P(ex + 18, -58)], fill=pk + (150,))
    d.line([*P(0, -80), *P(-8, -55), *P(6, -52)], fill=w, width=W(5), joint='curve')
    for sg in (-1, 1): d.arc([*P(sg * 34 - 34, -52), *P(sg * 34 + 34, -22)], 190, 350, fill=y_, width=W(9))
    if mood == 'grin': d.chord([*P(-34, -26), *P(34, 14)], 0, 180, fill=(90, 30, 40, 255), outline=w, width=W(5))
    elif mood == 'frown': d.arc([*P(-26, -10), *P(26, 18)], 200, 340, fill=w, width=W(6))
    elif mood == 'o': d.ellipse([*P(-12, -14), *P(12, 10)], outline=w, width=W(5))
    elif mood == 'smirk': d.arc([*P(-10, -26), *P(34, 0)], 20, 150, fill=w, width=W(6))
    else: d.arc([*P(-28, -26), *P(28, 2)], 20, 160, fill=w, width=W(6))
    d.polygon([P(-30, 30), P(0, 70), P(30, 30)], fill=bl)
    for by in (110, 160, 210):
        for bx in (-30, 30): d.ellipse([*P(bx - 7, by - 7), *P(bx + 7, by + 7)], fill=w)
    d.rectangle([*P(-90, 170), *P(90, 260)], outline=y_, width=W(6))
    (lx, ly), (rx, ry) = POSES.get(pose, POSES['idle'])
    if pose == 'work': rx += 38 * math.sin(t * 9); ry += 26 * math.cos(t * 9)
    if pose == 'wave': rx += 30 * math.sin(t * 8)
    if pose == 'celebrate': ly += 25 * math.sin(t * 10); ry += 25 * math.sin(t * 10 + 1.5)
    for sx, hx, hy, bend in ((-140, lx, ly, -1), (140, rx, ry, 1)):
        ex, ey, hx, hy = _ik(sx, 95, hx, hy, bend=bend)
        d.line([P(sx, 95), P(ex, ey), P(hx, hy)], fill=w, width=W(14), joint='curve')
        d.ellipse([*P(hx - 19, hy - 19), *P(hx + 19, hy + 19)], fill=w)
        if sx > 0 and pose in ('point', 'idea'):
            fx, fy = (hx + 42, hy - 24) if pose == 'point' else (hx + 4, hy - 46)
            d.line([P(hx, hy), P(fx, fy)], fill=w, width=W(10))
        if sx > 0 and pose == 'thumbs': d.line([P(hx, hy - 10), P(hx, hy - 52)], fill=w, width=W(12))
        if sx > 0 and pose == 'work': d.line([P(hx, hy), P(hx + 55, hy - 55)], fill=y_, width=W(9))   # chalk stick / spoon
    if bulb > 0:
        bx, by, r = 175, -400, 48 * min(1, bulb * 1.3)
        d.ellipse([*P(bx - r, by - r), *P(bx + r, by + r)], fill=CH['yellow'] + (255,), outline=w, width=W(5))
        d.rectangle([*P(bx - 18, by + r - 4), *P(bx + 18, by + r + 22)], outline=w, width=W(5))
        if bulb > .5:
            for k in range(8):
                a = k * math.pi / 4; r1, r2 = r + 14, r + 14 + 34 * min(1, (bulb - .5) * 3)
                d.line([P(bx + r1 * math.cos(a), by + r1 * math.sin(a)), P(bx + r2 * math.cos(a), by + r2 * math.sin(a))], fill=CH['yellow'], width=W(6))
def chef_sprite(s, pose, t, mood='smile', blink=False, bulb=0.0, squash=0.0):
    """Chef on its own transparent layer; squash > 0 = wider and shorter, < 0 = taller (stretch).
    Returns (img, foot_x, foot_y) so the caller pastes feet-anchored."""
    Wd, Hd = int(760 * s), int(800 * s); L = Image.new('RGBA', (Wd, Hd), (0, 0, 0, 0)); d = ImageDraw.Draw(L)
    chef2(d, Wd / 2, 500 * s, s, pose, t, mood, blink, bulb)
    if abs(squash) > 1e-3:
        nw, nh = int(Wd * (1 + squash)), int(Hd * (1 - squash)); L = L.resize((nw, nh), Image.BICUBIC)
    return L, L.width / 2, L.height - int(40 * s * (1 - squash))
# ---------- synthesized audio: upbeat bed + kitchen SFX (original, no samples) ----------
SR = 44100
def _t(dur): return np.arange(int(SR * dur)) / SR
def sfx(kind, dur=None, seed=0):
    r = np.random.default_rng(seed)
    if kind == 'printer':                       # ticket printer: fast dot-matrix chatter + tear
        t = _t(.42); x = np.sign(np.sin(2 * np.pi * 52 * t)) * r.standard_normal(len(t)) * .5
        x *= (np.sin(2 * np.pi * 26 * t) > -.2) * np.exp(-t * 2.5); x[-2600:] += r.standard_normal(2600) * np.linspace(.5, 0, 2600)
        return np.convolve(x, np.ones(6) / 6, 'same') * .55
    if kind == 'bell':                          # service bell
        t = _t(1.6); return sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * dcy) for f, a, dcy in ((2350, .5, 2.6), (5200, .22, 4), (7400, .12, 6), (1180, .14, 3))) * .7
    if kind == 'chalk':                         # chalk scratch, length dur
        t = _t(dur or .35); x = r.standard_normal(len(t)); x = np.diff(x, prepend=0) * (0.6 + .4 * np.sin(2 * np.pi * 17 * t) ** 2)
        return x * np.minimum(1, t * 40) * np.minimum(1, (t[-1] - t + 1e-3) * 30) * .22
    if kind == 'tick':
        t = _t(.04); return np.sin(2 * np.pi * 3100 * t) * np.exp(-t * 180) * .45
    if kind == 'pop':
        t = _t(.18); return np.sin(2 * np.pi * (600 + 900 * t / .18) * t) * np.exp(-t * 28) * .6
    if kind == 'ding':
        t = _t(.9); return (np.sin(2 * np.pi * 1568 * t) + .5 * np.sin(2 * np.pi * 2349 * t)) * np.exp(-t * 4) * .45
    if kind == 'whoosh':
        t = _t(.45); x = r.standard_normal(len(t)); k = np.exp(-((t - .28) / .12) ** 2)
        return np.convolve(x, np.ones(18) / 18, 'same') * k * .9
    if kind == 'riser':
        t = _t(dur or 1.5); return (np.sin(2 * np.pi * np.cumsum(300 + 900 * (t / t[-1]) ** 2) / SR) * .25 + r.standard_normal(len(t)) * .08) * (t / t[-1]) ** 2
    if kind == 'thump':
        t = _t(.3); return np.sin(2 * np.pi * np.cumsum(140 * np.exp(-t * 18) + 40) / SR) * np.exp(-t * 10) * .9
    raise ValueError(kind)
def bed(dur, bpm=124, drop=None, outro=None, seed=5):
    """Upbeat original bed: four-on-the-floor, offbeat hats, claps, bass, bright plucks.
    drop = time of a 0.5 s break + crash (the payoff); outro = time the drums thin out."""
    n = int(SR * dur); out = np.zeros(n); beat = 60 / bpm; r = np.random.default_rng(seed); hz = lambda m: 440 * 2 ** ((m - 69) / 12)
    def add(t0, x, g=1.):
        i = int(t0 * SR)
        if 0 <= i < n: x = x[:n - i]; out[i:i + len(x)] += x * g
    kick = np.sin(2 * np.pi * np.cumsum(150 * np.exp(-_t(.32) * 30) + 48) / SR) * np.exp(-_t(.32) * 9)
    hat = np.diff(r.standard_normal(len(_t(.06))), prepend=0) * np.exp(-_t(.06) * 60) * .16
    clap = r.standard_normal(len(_t(.2))) * np.exp(-_t(.2) * 22) * .3
    prog = [(57, (69, 72, 76)), (53, (69, 72, 77)), (60, (67, 72, 76)), (55, (67, 71, 74))]   # Am F C G
    for b in range(int(dur / beat) + 1):
        t0 = b * beat; bar = b // 4; root, ch = prog[bar % 4]
        brk = drop is not None and drop - .5 <= t0 < drop; thin = outro is not None and t0 >= outro
        if not brk:
            if not thin: add(t0, kick, .9)
            add(t0 + beat / 2, hat, 1.0)
            if b % 4 in (1, 3) and not thin: add(t0, clap, .8)
            bt = _t(beat * .45); add(t0 + beat / 2, (np.sin(2 * np.pi * hz(root - 12) * bt) + .4 * np.sin(4 * np.pi * hz(root - 12) * bt)) * np.exp(-bt * 6) * .45)
        for k in range(2):
            pt = _t(.22); m = ch[(b * 2 + k) % 3] + (12 if (drop and t0 >= drop and not thin) else 0)
            add(t0 + k * beat / 2, sum(np.sin(2 * np.pi * hz(m) * h * pt) / h ** 1.4 for h in (1, 2, 3)) * np.exp(-pt * 14) * .16)
    if drop: add(drop - 1.4, sfx('riser', 1.4), .7); add(drop, r.standard_normal(int(SR * 1.2)) * np.exp(-_t(1.2) * 3.5) * .25)
    fade = np.minimum(1, np.minimum(np.arange(n) / (SR * .05), (n - np.arange(n)) / (SR * 1.0)))
    return out * fade
def write_wav(path, x):
    import wave
    x = np.clip(x, -1, 1)
    with wave.open(path, 'wb') as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((x * 32767).astype(np.int16).tobytes())
def read_audio(path):
    """Any audio file -> mono float32 at SR (via ffmpeg)."""
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-ac', '1', '-ar', str(SR), '-f', 's16le', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32767
