#!/usr/bin/env python3
"""case_study_kit.py v2 - Fix It Case Study renderer (code-drawn, 0 AI generations).
  voice  CASE.json            voice every cue once (xai route: XAI_TTS_API_KEY, cost logged; piper route: free), cached in voice/
  stills CASE.json OUTDIR [--aspect 9x16|16x9|1x1]   key frames + contact sheet (approval gate)
  render CASE.json OUT.mp4 [--aspect ...]            voice + bed + SFX mix, kinetic captions, -14 LUFS, BT.709
  srt    CASE.json > captions.srt
Story beats: PROBLEM, CAUSE, FIX, RESULT, TRY, END. The acting Chalk Chef, the kitchen diagrams and the timings live in
CASE.json + the per-phase blocks below; for another business copy this file and change the phase blocks."""
import sys, os, json, math, hashlib, re
from pathlib import Path
VH = os.environ.get('VSTUDIO_HOME', '/workspace/vstudio')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path += [os.path.join(VH, 'kits')]
import numpy as np
from PIL import Image, ImageDraw
import vkit as v
from vkit import CH, ease, font
FPS = 30
L_ = {  # per-aspect layout: chip, label, head, strip/panel boxes, chef feet + scale, caption y/size, text floor (>= 5% of 1080),
        # k = diagram scale, ts = text scale on top of the floor, clock radius
 '9x16': dict(W=1080, H=1920, chip=(60, 150, 60), label=(60, 236, 76), head=(60, 318, 112, 960), strip=(60, 500, 960, 240),
              panel=(60, 800, 960, 400), chef=(290, 1880, .72), cap=(1250, 960, 74), floor=56, k=1.5, ts=1.4, clock_r=150),
 '16x9': dict(W=1920, H=1080, chip=(60, 34, 54), label=(60, 104, 70), head=(60, 178, 112, 1800), strip=(610, 318, 1250, 190),
              panel=(610, 525, 1250, 300), chef=(300, 900, .86), cap=(870, 1700, 62), floor=54, k=1.58, ts=1.0, clock_r=100),
 '1x1': dict(W=1080, H=1080, chip=(40, 28, 54), label=(40, 100, 64), head=(40, 166, 94, 1000), strip=(390, 292, 650, 170),
             panel=(390, 480, 650, 330), chef=(190, 840, .6), cap=(860, 1000, 58), floor=54, k=1.0, ts=1.0, clock_r=65)}
STATS = {'min_text_px': 999}
def tx(d, S, xy, text, size, col='white', kind='chalk', anchor='la', maxw=None, a=1.0):
    """Text that shrinks to fit maxw but never below the aspect's floor (asserts, so layout bugs fail loudly)."""
    f = font(kind, size)
    while maxw and d.textlength(text, font=f) > maxw and size > S['floor']: size -= 2; f = font(kind, size)
    assert not maxw or d.textlength(text, font=f) <= maxw + 2, ('text too wide at floor size', text, size)
    STATS['min_text_px'] = min(STATS['min_text_px'], size)
    c = CH.get(col, col) if isinstance(col, str) else col
    d.text(xy, text, font=f, fill=tuple(c[:3]) + (int(255 * max(0, min(1, a))),), anchor=anchor); return d.textlength(text, font=f), size
def drawon(L, p, fn):
    """Chalk draw-on reveal: fn draws onto a scratch layer, revealed left to right with a glowing chalk tip."""
    if p <= 0: return
    if p >= 1: fn(ImageDraw.Draw(L)); return
    tmp = Image.new('RGBA', L.size, (0, 0, 0, 0)); fn(ImageDraw.Draw(tmp)); bb = tmp.getbbox()
    if not bb: return
    x0, y0, x1, y1 = bb; cut = int(x0 + (x1 - x0) * ease(p) + 1)
    L.alpha_composite(tmp.crop((x0, y0, cut, y1)), (x0, y0))
    ImageDraw.Draw(L).ellipse([cut - 9, (y0 + y1) / 2 - 9, cut + 9, (y0 + y1) / 2 + 9], fill=(255, 255, 240, 230))
def pop(t, t0, dur=.35):
    """Ease-out-back: 0 -> overshoot ~1.1 -> 1 over dur."""
    x = (t - t0) / dur
    if x <= 0: return 0.
    if x >= 1: return 1.
    c1 = 1.70158; return 1 + (c1 + 1) * (x - 1) ** 3 + c1 * (x - 1) ** 2
def check(c):
    assert c.get('client_permission') or not c.get('client_name'), 'No client names without written permission'
    if c.get('results'):
        assert c.get('owner_result_source') or c.get('example'), 'Real results must be sourced to the owner'
        if not c.get('owner_result_source'): assert c.get('example') and c.get('example_figures_label', True), 'Example result figures must carry the EXAMPLE FIGURES label'
    prev = 0
    for q in c['cues']:
        assert q['at'] >= prev - 1e-6, ('cue overlaps the previous one', q['caption'])
        prev = q['at']
    assert 35 <= c['duration'] <= 45, 'case study sample length target is 35-45 s'
    return c['duration']
def phase_at(c, t):
    for p in c['phases']:
        if p['start'] <= t < p['end']: return p
    return c['phases'][-1]
# ---------- the acting chef ----------
def chef_state(c, t):
    acts = c['acting']; cur = acts[0]
    for a in acts:
        if a['t'] <= t: cur = a
    dt = t - cur['t']; sq = .2 * math.exp(-dt * 9) * math.cos(dt * 22)        # squash pop on every pose swap
    hop = 0.
    if cur.get('hop'):
        ph = (dt % .62) / .62; hop = abs(math.sin(math.pi * ph)); sq += .16 * max(0, 1 - hop * 4) - .07 * hop
    blink = (t % 3.3) < .12
    bulb = ease((dt - .15) / .45) if cur['pose'] == 'idea' else 0
    return cur['pose'], cur.get('mood', 'smile'), sq, hop, blink, bulb
def put_chef(L, S, c, t):
    fx, fy, s = S['chef']; pose, mood, sq, hop, blink, bulb = chef_state(c, t)
    sp, ax, ay = v.chef_sprite(s, pose, t, mood, blink, bulb, sq)
    L.alpha_composite(sp, (int(fx - ax), int(fy - ay - hop * 70 * s)))
# ---------- per-phase blocks (kitchen sample) ----------
def tickets(d, x, y, w, n, hot, s, t, drop_times=None):
    tw, gap = int(84 * s), int(14 * s); n = int(n)
    d.line([(x, y), (x + w, y)], fill=CH['white'], width=int(8 * s))
    step = min(tw + gap, (w - tw - 20) / max(1, n - 1))
    for i in range(n):
        dy = 0
        if drop_times and i < len(drop_times):
            e = (t - drop_times[i]) / .28
            if e <= 0: continue
            dy = -(1 - ease(e)) * 120 * s + (math.sin(min(1, e) * math.pi) * 12 * s if e < 1 else 0)
        tx_ = x + 16 + i * step; col = CH['red'] if i < hot else CH['white']; ty = y + 8 + dy
        d.rectangle([tx_, ty, tx_ + tw, ty + 150 * s], fill=(34, 48, 43, 255), outline=col, width=int(5 * s))
        for k in range(3): d.line([(tx_ + 12, ty + (38 + 30 * k) * s), (tx_ + tw - 12, ty + (38 + 30 * k) * s)], fill=col, width=int(4 * s))
def clock(d, cx, cy, r, t, spin=6.0):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=CH['white'], width=7)
    for k in range(12):
        a = k * math.pi / 6; d.line([(cx + .82 * r * math.cos(a), cy + .82 * r * math.sin(a)), (cx + .95 * r * math.cos(a), cy + .95 * r * math.sin(a))], fill=CH['white'], width=4)
    a = -math.pi / 2 + t * spin; d.line([(cx, cy), (cx + .78 * r * math.cos(a), cy + .78 * r * math.sin(a))], fill=CH['red'], width=8)
    a2 = -math.pi / 2 + t * spin / 12; d.line([(cx, cy), (cx + .5 * r * math.cos(a2), cy + .5 * r * math.sin(a2))], fill=CH['white'], width=10)
def confetti(d, cx, cy, t0, t, n=46, spread=520, seed=3):
    e = t - t0
    if e < 0 or e > 2.2: return
    r = np.random.default_rng(seed); cols = [CH[k] for k in ('yellow', 'pink', 'blue', 'green', 'orange')]
    for i in range(n):
        a = r.uniform(-math.pi, 0); sp = r.uniform(.4, 1) * spread
        x = cx + math.cos(a) * sp * e; y = cy + math.sin(a) * sp * e * 1.3 + 520 * e * e
        ang = r.uniform(0, 6.3) + e * 8; L = 18
        d.line([(x, y), (x + L * math.cos(ang), y + L * math.sin(ang))], fill=cols[i % 5] + (int(255 * max(0, 1 - e / 2.2)),), width=7)
def draw_phase(L, S, c, p, t):
    d = ImageDraw.Draw(L); lt = t - p['start']; nm = p['name']; sx, sy, sw, sh = S['strip']; px, py, pw, ph = S['panel']
    col = {'PROBLEM': 'pink', 'CAUSE': 'orange', 'FIX': 'blue', 'RESULT': 'green', 'TRY': 'yellow', 'END': 'yellow'}[nm]
    lx, ly, ls = S['label']; hx, hy, hs, hw = S['head']; k = S['k']
    if nm != 'END': tx(d, S, (lx, ly), p['label'], ls, col)
    drawon(L, (lt + (.35 if nm == 'PROBLEM' else 0)) / .55, lambda dd: tx(dd, S, (hx, hy), p['head'], hs, 'white', maxw=hw))
    F = int(S['floor'] * S['ts'])
    if nm == 'PROBLEM':
        drops = [0.0, .2, .45, .7, .95, 1.25, 1.6, 2.0, 2.5, 3.1]
        hot = sum(1 for tt in (1.3, 2.0, 2.7, 3.4, 4.1) if lt > tt)
        tickets(d, sx, sy + 10, sw, len(drops), hot, k * .95, lt, drops)
        r = S['clock_r']; clock(d, px + r + 10, py + ph / 2, r, lt, 7)
        mins = int(14 + 8 * ease((lt - .6) / 3.2)); shake = 6 * math.sin(lt * 50) if mins >= 22 and lt < 4.6 else 0
        tx(d, S, (px + 2 * r + 30 + shake, py + ph / 2 - F * 1.15), 'TICKET TIME', F, 'white', 'sans', maxw=pw - 2 * r - 40)
        tx(d, S, (px + 2 * r + 30 + shake, py + ph / 2 - F * .05), f'{mins} MIN', int(F * 2.0), 'red' if mins >= 20 else 'yellow', 'sans', maxw=pw - 2 * r - 40)
    elif nm == 'CAUSE':
        v.stations(d, sx, sy + 10, sw, sh - 30, ['GRILL', 'FRY', 'EXPO'], hi=(0,) if (lt * 4) % 2 < 1.3 else (), s=k * .95)
        for i, (t0, txt, cc) in enumerate(((1.0, 'PREP RAN OUT', 'pink'), (3.0, 'NO HANDOFF OWNER', 'orange'))):
            yy = py + 20 + i * (ph / 2)
            if lt > t0:
                e = pop(lt, t0, .3); rr = F * .45 * e; cx_, cy_ = px + rr + 6, yy + F * .62
                d.line([(cx_ - rr, cy_ - rr), (cx_ + rr, cy_ + rr)], fill=CH[cc], width=9); d.line([(cx_ - rr, cy_ + rr), (cx_ + rr, cy_ - rr)], fill=CH[cc], width=9)
            drawon(L, (lt - t0) / .6, lambda dd, txt=txt, cc=cc, yy=yy: tx(dd, S, (px + F * 1.25, yy), txt, int(F * 1.2), cc, maxw=pw - F * 1.3))
    elif nm == 'FIX':
        checks = p['checks']; n_left = 10 - sum(3 for q in checks if lt > q['t'] + .3)
        tickets(d, sx, sy + 10, sw, max(1, n_left), 0, k * .95, lt)
        for i, q in enumerate(checks):
            yy = py + i * (ph / 3) + 6
            d.rectangle([px, yy + 8, px + F * .9, yy + 8 + F * .9], outline=CH['white'], width=5)
            if lt > q['t'] + .3:
                e = min(1, (lt - q['t'] - .3) / .2); bx = px
                d.line([(bx + 6, yy + 8 + F * .5), (bx + F * .35, yy + 8 + F * .85 * min(1, e * 2))], fill=CH['green'], width=10)
                if e > .5: d.line([(bx + F * .35, yy + 8 + F * .85), (bx + F * .35 + (F * .75) * (e - .5) * 2, yy + 8 - F * .1 * (e - .5) * 2)], fill=CH['green'], width=10)
            drawon(L, (lt - q['t'] + .5) / .6, lambda dd, q=q, yy=yy: tx(dd, S, (px + F * 1.3, yy - 4), q['text'], int(F * 1.15), 'white', maxw=pw - F * 1.4))
    elif nm == 'RESULT':
        ux, uy, uw, uh = sx, sy, sw, (py + ph) - sy
        for i, r_ in enumerate(c['results']):
            t0 = r_['t'] - p['start']; yy = uy + i * uh / 2 + 10
            if lt < t0: continue
            a = ease((lt - t0) / .3); e = ease((lt - t0 - .5) / 1.6)
            tx(d, S, (ux, yy), r_['label'], F, 'white', 'sans', maxw=uw, a=a)
            val = r_['before'] + (r_['after'] - r_['before']) * e; big = int(F * 2.2)
            fit = lambda b: sum(d.textlength(z, font=font('sans', b)) for z in (f"{r_['before']:g}", '->', f"{r_['after']:g} {r_['unit']}")) + 60
            while fit(big) > uw * .9 and big > S['floor']: big -= 4
            w0, _ = tx(d, S, (ux, yy + F * 1.15), f"{r_['before']:g}", big, (150, 150, 150), 'sans', a=a)
            if e > 0: d.line([(ux - 6, yy + F * 1.15 + big * .55), (ux + w0 + 6, yy + F * 1.15 + big * .5)], fill=CH['red'], width=9)
            ax_ = ux + w0 + 30; tx(d, S, (ax_, yy + F * 1.15), '->', big, 'white', 'sans', a=a)
            sc = .6 + .4 * pop(lt, t0 + 2.1, .4) if lt > t0 + 2.1 else 1
            tx(d, S, (ax_ + d.textlength('->', font=font('sans', big)) + 30, yy + F * 1.15 - (sc - 1) * big * .5), f"{int(round(val))} {r_['unit']}", int(big * sc), 'green', 'sans', a=a)
            confetti(d, ux + uw * .7, yy + F * 2, t0 + 2.1, lt)
    elif nm == 'TRY':
        for i, (t0, txt) in enumerate(((1.0, 'CHECKLIST'), (1.5, 'OPENING / CLOSING'), (2.0, 'PAR SHEET'))):
            if lt < t0: continue
            yy = py + i * ph / 3; e = pop(lt, t0, .3); hgt = F * 1.0
            d.rectangle([px, yy + 6, px + hgt * .78 * e, yy + 6 + hgt * e], outline=CH['yellow'], width=5)
            for kq in range(3): d.line([(px + 8, yy + 20 + kq * hgt * .25), (px + hgt * .78 * e - 8, yy + 20 + kq * hgt * .25)], fill=CH['yellow'], width=3)
            tx(d, S, (px + hgt * 1.1, yy), txt, int(F * 1.05), 'white', maxw=pw - hgt * 1.2)
        e = pop(lt, .3, .4); fs = int(F * 1.5 * e)
        if fs > 4: d.text((sx + sw / 2, sy + sh / 2), 'FREE PRINTABLE KIT', font=font('chalk', fs), fill=CH['yellow'], anchor='mm'); STATS['min_text_px'] = min(STATS['min_text_px'], max(F, int(F * 1.5)))
    else:  # END: owner-filled call to action
        cta = c['cta']; e = pop(lt, .3, .45)
        drawon(L, (lt - .1) / .5, lambda dd: tx(dd, S, (hx, hy), cta['headline'], hs, 'white', maxw=hw))
        bw, bh = sw * .78, sh * .8; bx, by = sx + (sw - bw) / 2, sy + (sh - bh) / 2; pul = 1 + .03 * math.sin(lt * 6)
        if e > 0:
            d.rounded_rectangle([bx + bw * (1 - pul * e) / 2, by + bh * (1 - pul * e) / 2, bx + bw * (1 + pul * e) / 2, by + bh * (1 + pul * e) / 2], radius=30, outline=CH['yellow'], width=9)
            if e > .6: tx(d, S, (bx + bw / 2, by + bh / 2), cta['action'], int(F * 1.5), 'yellow', anchor='mm', maxw=bw - 40)
        if lt > 1.0: tx(d, S, (px, py + 10), cta['contact'], int(F * 1.15), 'blue', 'sans', maxw=pw, a=ease((lt - 1.0) / .4))
        if lt > 1.6:
            for i, ln in enumerate(c['ai_label_lines']): tx(d, S, (px, py + 30 + F * 1.5 + i * F * 1.2), ln, int(F * 1.1), 'white', 'chalk', maxw=pw, a=ease((lt - 1.6) / .4))
# ---------- kinetic captions ----------
def _norm(w): return re.sub(r"[^a-z0-9']", '', w.lower())
def align(c, root, model='small'):
    """Word-level timings per cue from local faster-whisper (free, cached in voice/align.json). Caption words are matched to the
    heard words in order (difflib); unmatched words (e.g. '22' vs 'twenty-two') are interpolated between matched neighbours."""
    vd = Path(root) / 'voice'; man = json.load(open(vd / 'manifest.json')); ap = vd / 'align.json'
    old = json.load(open(ap)) if ap.exists() else {}; out = {}
    todo = [m for m in man['cues'] if m['hash'] not in old]
    if todo:
        from faster_whisper import WhisperModel
        wm = WhisperModel(model, device='cpu', compute_type='int8')
    import difflib
    for q, m in zip(c['cues'], man['cues']):
        if m['hash'] in old: out[m['hash']] = old[m['hash']]; continue
        import subprocess
        pcm = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(vd / m['file']), '-ac', '1', '-ar', '16000', '-f', 'f32le', '-'], capture_output=True, check=True).stdout
        segs, _ = wm.transcribe(np.frombuffer(pcm, np.float32), language='en', word_timestamps=True, beam_size=5)
        heard = [(w.word.strip(), w.start, w.end) for sg in segs for w in sg.words]
        out[m['hash']] = {'caption': q['caption'], 'heard': heard}
    json.dump(out, open(ap, 'w')); return out
def _match(cap_words, heard, s0, s1):
    import difflib
    a = [_norm(w) for w in cap_words]; b = [_norm(h[0]) for h in heard]; T = [None] * len(a)
    for blk in difflib.SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks():
        for k in range(blk.size): T[blk.a + k] = (heard[blk.b + k][1], heard[blk.b + k][2])
    known = [i for i, x in enumerate(T) if x]
    for i in range(len(a)):
        if T[i]: continue
        lo = max([j for j in known if j < i], default=None); hi = min([j for j in known if j > i], default=None)
        t0 = T[lo][1] if lo is not None else s0; t1 = T[hi][0] if hi is not None else s1
        gap = [j for j in range(len(a)) if (lo is None or j > lo) and (hi is None or j < hi)]; n = len(gap); r = gap.index(i)
        T[i] = (t0 + (t1 - t0) * r / n, t0 + (t1 - t0) * (r + 1) / n)
    return T, len(known) / max(1, len(a))
def load_man(root):
    vd = Path(root) / 'voice'; man = json.load(open(vd / 'manifest.json'))
    if (vd / 'align.json').exists(): man['align'] = json.load(open(vd / 'align.json'))
    return man
def words_for(c, man):
    """Per-word timings: each cue's words spread over its measured speech span, weighted by length."""
    out = []
    for q, m in zip(c['cues'], man['cues']):
        ws, inside = [], False                       # *multi word* spans -> each word tagged with a leading *
        for w in q['caption'].split():
            st = w.startswith('*'); inside = inside or st; ws.append(('*' if inside else '') + w.replace('*', ''))
            if w.rstrip('.,!?:;').endswith('*') or (w.endswith('*') and len(w) > 1): inside = False
        s0 = q['at'] + m['speech_start']; s1 = q['at'] + m['speech_end']
        al = man.get('align', {}).get(m['hash'])
        if al and al['heard']:
            T, _ = _match(ws, al['heard'], m['speech_start'], m['speech_end'])
            out.append([(w, q['at'] + a0, q['at'] + max(a1, a0 + .08)) for w, (a0, a1) in zip(ws, T)]); continue
        wt = np.cumsum([0] + [len(w.strip('*')) + 2 for w in ws]); wt = wt / wt[-1]
        out.append([(w, s0 + (s1 - s0) * wt[i], s0 + (s1 - s0) * wt[i + 1]) for i, w in enumerate(ws)])
    return out
def chunks(cw, n=6):
    res = []
    for line in cw:
        k = math.ceil(len(line) / n); m = math.ceil(len(line) / k)        # balanced chunks: 7 words -> 4 + 3, not 6 + 1
        for i in range(0, len(line), m): res.append(line[i:i + m])
    return res
def kinetic(im, S, ch, t):
    W = S['W']; y, maxw, size = S['cap']; d = ImageDraw.Draw(im); f = font('sans', size, 900); sp = d.textlength(' ', font=f)
    lines, cur, wsum = [], [], 0
    for w in ch:
        ww = d.textlength(w[0].strip('*'), font=f)
        if cur and wsum + sp + ww > maxw: lines.append(cur); cur, wsum = [], 0
        cur.append((w, ww)); wsum += ww + (sp if len(cur) > 1 else 0)
    lines.append(cur); assert len(lines) <= 2, 'caption chunk wraps past 2 lines'
    lh = int(size * 1.22); bw = max(sum(ww for _, ww in ln) + sp * (len(ln) - 1) for ln in lines) + 60; bh = lh * len(lines) + 26
    ov = Image.new('RGBA', im.size, (0, 0, 0, 0)); od = ImageDraw.Draw(ov)
    od.rounded_rectangle([(W - bw) / 2, y, (W + bw) / 2, y + bh], radius=26, fill=(0, 0, 0, 185))
    for li, ln in enumerate(lines):
        x = (W - (sum(ww for _, ww in ln) + sp * (len(ln) - 1))) / 2; yy = y + 13 + li * lh
        for (w, t0, t1), ww in ln:
            if True:   # whole chunk readable; spoken word pops yellow, *key* words turn green once said
                key = w.startswith('*'); word = w.strip('*'); act = t0 - .04 <= t < t1 + .05; said = t >= t0 - .04
                colr = (255, 214, 92) if act else ((150, 230, 140) if key and said else ((255, 255, 255) if said else (255, 255, 255, 120)))
                sc = 1 + .12 * max(0, 1 - (t - t0 + .04) / .14) if act else 1
                ff = font('sans', int(size * sc), 900); dx = (d.textlength(word, font=ff) - ww) / 2
                od.text((x - dx, yy - (sc - 1) * size * .5), word, font=ff, fill=colr)
            x += ww + sp
    im.alpha_composite(ov); STATS['min_text_px'] = min(STATS['min_text_px'], size)
# ---------- frame function ----------
def make(c, aspect, root):
    S = L_[aspect]; W, H = S['W'], S['H']; B = v.board(W, H); dur = c['duration']
    man = load_man(root); ch = chunks(words_for(c, man))
    def fn(t):
        p = phase_at(c, t); lt = t - p['start']
        im = B.copy(); L = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        draw_phase(L, S, c, p, t); put_chef(L, S, c, t); v.chalkify(L); im.alpha_composite(L)
        z = 1 + .03 * ease(lt / (p['end'] - p['start']))
        if p['name'] == 'PROBLEM' and lt < .45: z += .02 * math.sin(lt * 60) * (1 - lt / .45)      # slam shake on the hook
        im = im.transform((W, H), Image.AFFINE, (1 / z, 0, W / 2 * (1 - 1 / z), 0, 1 / z, H * .45 * (1 - 1 / z)), Image.BICUBIC)
        dd = ImageDraw.Draw(im); cx, cy, cs = S['chip']
        if c.get('example'):
            cw_ = v.chip(dd, cx, cy, 'EXAMPLE SCENARIO', CH['yellow'], cs)
            if p['name'] == 'RESULT' and not c.get('owner_result_source'):   # second disclosure chip on the payoff
                w2 = dd.textlength('EXAMPLE FIGURES', font=font('sans', cs, 850)) + 36
                x2, y2 = (cx + cw_ + 16, cy) if cx + cw_ + 16 + w2 <= W - cx else (W - cx - w2, S['label'][1])
                v.chip(dd, int(x2), int(y2), 'EXAMPLE FIGURES', CH['green'], cs)
        for chn in ch:
            if chn[0][1] - .05 <= t < chn[-1][2] + .25: kinetic(im, S, chn, t); break
        return im
    return fn, W, H, dur
# ---------- voice (cached; billed once) + mix ----------
def speech_span(x, thr=.02):
    env = np.convolve(np.abs(x), np.ones(441) / 441, 'same'); idx = np.where(env > thr * env.max())[0]
    return (idx[0] / v.SR, idx[-1] / v.SR) if len(idx) else (0, len(x) / v.SR)
def voice(c, root):
    root = Path(root); vd = root / 'voice'; vd.mkdir(exist_ok=True); mp = vd / 'manifest.json'
    man = json.load(open(mp)) if mp.exists() else {'cues': [], 'usd': 0, 'chars': 0}
    old = {m['hash']: m for m in man['cues']}; new = []; usd = 0; chars = 0
    for i, q in enumerate(c['cues']):
        key = hashlib.sha256(json.dumps([c.get('voice_route'), c.get('voice_id'), q['say'], q.get('speed', 1)]).encode()).hexdigest()[:16]
        f = vd / f'cue_{i:02d}_{key}.mp3'
        if key not in old or not f.exists():
            if c.get('voice_route') == 'xai':
                import requests
                body = {'text': q['say'], 'voice_id': c['voice_id'], 'language': 'en', 'output_format': {'codec': 'mp3', 'sample_rate': 44100, 'bit_rate': 192000}}
                if q.get('speed', 1) != 1: body['speed'] = q['speed']
                r = requests.post('https://api.x.ai/v1/tts', headers={'Authorization': 'Bearer ' + os.environ['XAI_TTS_API_KEY']}, json=body, timeout=180)
                r.raise_for_status(); f.write_bytes(r.content); u = len(q['say']) * 15 / 1e6; usd += u; chars += len(q['say'])
                with open(os.path.join(VH, 'ledger.jsonl'), 'a') as lg:
                    import datetime
                    lg.write(json.dumps({'generations': 0, 'images': 0, 'model': 'tts', 'note': f"tts {c['voice_id']} cue {i}", 'project': c.get('project', 'case-study'),
                                         'provider': 'xai', 'chars': len(q['say']), 'ts': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'), 'usd': round(u, 6)}) + '\n')
            else:
                import subprocess, tempfile
                w = str(f) + '.wav'; subprocess.run([sys.executable, os.path.join(VH, 'bin', 'vstudio_voice.py'), 'local', '/dev/stdin', w, '--rate', str(q.get('speed', 1))],
                                                    input=re.sub(r'<[^>]+>|\[pause\]', '', q['say']), text=True, check=True, capture_output=True)
                subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', w, '-b:a', '160k', str(f)], check=True); os.remove(w)
        x = v.read_audio(str(f)); s0, s1 = speech_span(x)
        new.append({'hash': key, 'file': f.name, 'len': len(x) / v.SR, 'speech_start': s0, 'speech_end': s1})
    for g in vd.glob('cue_*.mp3'):
        if g.name not in {m['file'] for m in new}: g.unlink()
    man = {'cues': new, 'usd': round(man.get('usd', 0) + usd, 6), 'chars': man.get('chars', 0) + chars, 'route': c.get('voice_route'), 'voice_id': c.get('voice_id')}
    json.dump(man, open(mp, 'w'), indent=1)
    for q, m in zip(c['cues'], new):
        nxt = [qq['at'] for qq in c['cues'] if qq['at'] > q['at']]
        assert not nxt or q['at'] + m['speech_end'] <= min(nxt) + .02, f"cue '{q['caption']}' runs {q['at'] + m['speech_end'] - min(nxt):.2f} s into the next cue"
    return man
def mix(c, root, man, out_wav):
    dur = c['duration']; n = int(v.SR * dur); vo = np.zeros(n); fx = np.zeros(n)
    for q, m in zip(c['cues'], man['cues']):
        x = v.read_audio(str(Path(root) / 'voice' / m['file'])); i = int(q['at'] * v.SR); x = x[:n - i]; vo[i:i + len(x)] += x
    vo = vo / max(1e-9, np.abs(vo).max()) * .9
    for e in c['sfx']:
        x = v.sfx(e['type'], e.get('dur'), seed=int(e['t'] * 10)); i = int(e['t'] * v.SR)
        if i < n: x = x[:n - i]; fx[i:i + len(x)] += x * e.get('gain', 1)
    b = v.bed(dur, c.get('bpm', 124), c.get('drop'), c.get('outro')); b = b / max(1e-9, np.abs(b).max())
    env = np.convolve(np.abs(vo), np.ones(2205) / 2205, 'same'); duck = 1 - .62 * np.clip(env * 10, 0, 1)
    duck = np.convolve(duck, np.ones(2205) / 2205, 'same')
    y = b * .42 * duck + fx * .55 * (1 - .4 * np.clip(env * 10, 0, 1)) + vo
    v.write_wav(out_wav, y / max(1e-9, np.abs(y).max()) * .95)
def srt(c, root):
    man = load_man(root)
    tm = lambda t: f'{int(t // 3600):02}:{int(t // 60) % 60:02}:{int(t) % 60:02},{int(round((t % 1) * 1000)) % 1000:03}'
    cs = chunks(words_for(c, man), n=10); out = []          # .srt: longer, calmer chunks than the on-screen kinetic ones
    for i, ch in enumerate(cs):
        end = ch[-1][2] + .25 if i + 1 == len(cs) else min(ch[-1][2] + .25, cs[i + 1][0][1] - .03)
        out.append(f"{i + 1}\n{tm(ch[0][1])} --> {tm(end)}\n{' '.join(w.strip('*') for w, _, _ in ch)}")
    return '\n\n'.join(out) + '\n'
def main(a):
    c = json.load(open(a[1])); root = Path(a[1]).resolve().parent; check(c); aspect = a[a.index('--aspect') + 1] if '--aspect' in a else '9x16'
    if a[0] == 'voice': print(json.dumps({k: v_ for k, v_ in voice(c, root).items() if k != 'cues'})); align(c, root); return
    if a[0] == 'srt': print(srt(c, root), end=''); return
    voice(c, root); align(c, root); fn, W, H, dur = make(c, aspect, root)
    if a[0] == 'stills':
        out = Path(a[2]); out.mkdir(parents=True, exist_ok=True); th = []
        for tt in c.get('still_times', [p['start'] + (p['end'] - p['start']) * .7 for p in c['phases']]):
            im = fn(tt).convert('RGB'); im.save(out / f't{tt:05.2f}.jpg', quality=88); th.append(im.resize((W // 4, H // 4)))
        cols = 6 if aspect == '9x16' else 4; sh = Image.new('RGB', (cols * th[0].width, math.ceil(len(th) / cols) * th[0].height))
        for i, im in enumerate(th): sh.paste(im, ((i % cols) * im.width, (i // cols) * im.height))
        sh.save(out / 'contact_sheet.jpg', quality=88); print(json.dumps({'stills': len(th), 'min_text_px': STATS['min_text_px']})); return
    for k_ in range(int(dur * 2)): fn(k_ / 2)          # sample in the parent so min_text_px is real (workers do not report back)
    wav = a[2] + '.mix.wav'; mix(c, root, json.load(open(root / 'voice' / 'manifest.json')), wav)
    nfr = v.render_par(fn, W, H, dur, wav, a[2]); os.remove(wav)
    print(json.dumps({'out': a[2], 'frames': nfr, 'duration': dur, 'generation_count': 0, 'min_text_px': STATS['min_text_px']}))
if __name__ == '__main__': main(sys.argv[1:])
