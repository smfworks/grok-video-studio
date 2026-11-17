#!/usr/bin/env python3
"""numbers_kit.py - Numbers on Screen renderer (code-drawn, 0 generations, sharp figures).
  check  numbers.json                       recompute every total and derived figure; exit 1 on any mismatch
  stills numbers.json OUTDIR [--aspect 9x16|16x9]   one PNG per scene midpoint + contact sheet (approval gate)
  render numbers.json OUT.mp4 [--aspect 9x16|16x9]  check, then render with promo_kit.music(); loudness is normalized to -14 LUFS
Scene types: breakdown (items + total [+ price]), annualize (points of price x volume x periods), compare (schedule before/after).
Needs vkit.py (vstudio-visual-kit) and promo_kit.py (vstudio-promo-kit)."""
import json, math, sys, os
from decimal import Decimal, ROUND_HALF_UP
VH = os.environ.get('VSTUDIO_HOME', '/workspace/vstudio')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.path += [os.path.join(VH, 'kits'), os.path.join(VH, 'promo-kit')]
import numpy as np
from PIL import Image, ImageDraw
import vkit
from vkit import font, ease
PAL = [(245, 160, 80), (130, 196, 240), (150, 220, 140), (240, 140, 160), (247, 214, 92), (190, 160, 250), (120, 220, 210), (230, 230, 230)]
INK, MUTED, OKC = (246, 246, 250), (160, 166, 184), (120, 220, 140)
def cash(v): return float(Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
def money(nj, v, d=2): return f"{nj.get('currency', '$')}{v:,.{d}f}"
def pct(v): return f'{v * 100:.1f}%'
# ---------- checks: every figure on screen is recomputed here ----------
def derive(nj):
    out = {}
    for k, sc in enumerate(nj['scenes']):
        if sc['type'] == 'breakdown':
            s = cash(sum(Decimal(str(i['value'])) for i in sc['items'])); out[k] = {'sum': s}
            if 'price' in sc: out[k]['pct'] = s / sc['price']['value']
        if sc['type'] == 'annualize':
            per = cash(Decimal(str(sc['price']['value'])) * Decimal(str(sc['points'])) / 100); wk = cash(Decimal(str(per)) * Decimal(str(sc['volume']['value'])))
            out[k] = {'per': per, 'per_period': wk, 'total': cash(Decimal(str(wk)) * Decimal(str(sc['periods'])))}
            if 'cost' in sc: out[k]['from'] = sc['cost']['value'] / sc['price']['value']
        if sc['type'] == 'compare':
            hb = sum(r['before'][1] - r['before'][0] for r in sc['rows']); ha = sum(r['after'][1] - r['after'][0] for r in sc['rows'])
            rate = sc.get('rate', {}).get('value'); out[k] = {'hb': hb, 'ha': ha, 'cb': rate and round(hb * rate, 2), 'ca': rate and round(ha * rate, 2)}
    return out
def check(nj):
    msgs, D = [], derive(nj)
    for k, sc in enumerate(nj['scenes']):
        d = D.get(k, {})
        if sc['type'] == 'breakdown':
            for i in sc['items']:
                if 'qty' in i and 'unit_cost' in i:
                    m = cash(Decimal(str(i['qty']['value'])) * Decimal(str(i['unit_cost']['value'])))
                    assert abs(m - i['value']) < 0.005, f"{i['label']}: qty x unit cost = {m}, sheet says {i['value']}"
            assert abs(d['sum'] - sc['total']['value']) <= 0.005, f"items add to {d['sum']}, sheet total says {sc['total']['value']}"
            msgs.append(f"{sc['title']}: {len(sc['items'])} items add to {money(nj, d['sum'])} = sheet total {sc['total'].get('cell', '')} OK")
            if 'pct' in d: msgs.append(f"cost / price = {pct(d['pct'])}")
        if sc['type'] == 'annualize':
            for key, lab in (('per', 'per_unit'), ('per_period', 'per_period'), ('total', 'total')):
                if lab in sc.get('stated', {}): assert abs(sc['stated'][lab]['value'] - d[key]) <= 0.01, f'{lab}: computed {d[key]}, sheet says {sc["stated"][lab]["value"]}'
            msgs.append(f"{sc['points']} points of {money(nj, sc['price']['value'])} = {money(nj, d['per'])}; x {sc['volume']['value']:g} = {money(nj, d['per_period'])}; x {sc['periods']} = {money(nj, d['total'])}")
        if sc['type'] == 'compare':
            for key in ('hb', 'ha', 'cb', 'ca'):
                if key in sc.get('stated', {}): assert abs(sc['stated'][key]['value'] - d[key]) <= 0.01, f'{key}: computed {d[key]}, sheet says {sc["stated"][key]["value"]}'
            msgs.append(f"{sc['title']}: {d['hb']:g} h before, {d['ha']:g} h after" + (f", {money(nj, d['cb'])} -> {money(nj, d['ca'])}" if d['cb'] else ''))
    return msgs
# ---------- layout ----------
class Lay:
    def __init__(s, aspect):
        s.W, s.H = (1080, 1920) if aspect == '9x16' else (1920, 1080)
        s.x0, s.y0, s.w, s.h = (70, 270, 940, 1150) if aspect == '9x16' else (330, 70, 1260, 900)   # 9:16 keeps clear of the top 250 / bottom 500 px
        s.k = 1.0 if aspect == '9x16' else 0.85
def bg(L, accent):
    y = np.linspace(0, 1, L.H)[:, None, None]; x = np.linspace(-1, 1, L.W)[None, :, None]
    base = np.array([20, 24, 36]) * (1 - y) + np.array([9, 11, 18]) * y
    glow = np.exp(-((x * 1.1) ** 2 + ((y - .35) * 2.2) ** 2)) * np.array(accent) * .16
    return Image.fromarray(np.clip(base + glow, 0, 255).astype(np.uint8)).convert('RGBA')
def fit(d, text, size, maxw, w=800):
    while d.textlength(text, font=font('sans', size, w)) > maxw and size > 18: size -= 2
    return font('sans', size, w)
def T(d, xy, text, size, col=INK, maxw=2000, anchor='la', w=800, a=1.0):
    f = fit(d, text, size, maxw, w); d.text(xy, text, font=f, fill=col + (int(255 * a),), anchor=anchor); return f
COUNTUP = True
def count(v, e): return v * ease(e) if COUNTUP else v
# ---------- scenes ----------
def sc_hook(d, L, nj, sc, t, dur):
    a = ease((t + .4) / .5); cy = L.y0 + L.h * .45;   # frame 0 is already a real image (platform thumbnail)
    px, py, R = L.W / 2, cy - 300 * L.k, 150 * L.k * (.85 + .15 * a)
    for r, wd in ((R, 10), (R * .68, 5)): d.ellipse([px - r, py - r, px + r, py + r], outline=PAL[0] + (int(255 * a),), width=int(wd * L.k))
    for sx in (-1, 1):
        x = px + sx * (R + 55 * L.k); d.line([(x, py - R * .9), (x, py + R * .9)], fill=INK + (int(220 * a),), width=int(9 * L.k))
    T(d, (L.W / 2, cy), nj['title'], int(84 * L.k), maxw=L.w, anchor='mm', w=850, a=a)
    if t > .3: T(d, (L.W / 2, cy + 120 * L.k), nj['hook'], int(52 * L.k), PAL[0], L.w, 'mm', 700, ease((t - .3) / .5))
def sc_breakdown(d, L, nj, sc, t, dur, k):
    n, per, acc = len(sc['items']), 1.5, PAL[0]; D = derive(nj)[k]; ttot = 1.0 + n * per + .2
    tp = ttot + 2.6 if 'price' in sc else 1e9; fade = 1 - ease((t - tp) / .5)
    T(d, (L.x0, L.y0 + 70 * L.k), sc['title'].upper(), int(40 * L.k), acc, L.w, w=850, a=fade)
    T(d, (L.x0, L.y0 + 120 * L.k), nj['title'], int(70 * L.k), INK, L.w, w=850, a=fade)
    top = L.y0 + 240 * L.k; rh = min(140 * L.k, (L.h - 410 * L.k) / n); mx = max(i['value'] for i in sc['items'])
    if fade > 0:
        for i, it in enumerate(sc['items']):
            e = (t - 1.0 - i * per) / .6
            if e <= 0: continue
            y = top + i * rh; a = ease(e) * fade; c = PAL[i % len(PAL)]
            f = T(d, (L.x0, y), it['label'], int(48 * L.k), INK, L.w * .62, w=700, a=a)
            if it.get('detail'): d.text((L.x0 + d.textlength(it['label'], font=f) + 14, y + 12 * L.k), it['detail'], font=font('sans', int(32 * L.k), 500), fill=MUTED + (int(255 * a),))
            T(d, (L.x0 + L.w, y), money(nj, count(it['value'], e)), int(50 * L.k), INK, 400, 'ra', 800, a)
            bw = L.w * it['value'] / mx * ease(e); d.rounded_rectangle([L.x0, y + 68 * L.k, L.x0 + max(bw, 18), y + 88 * L.k], radius=10, fill=c + (int(255 * a),))
        if t > ttot:
            e = (t - ttot) / .7; y = top + n * rh + 10; a = ease(e) * fade
            d.line([(L.x0, y), (L.x0 + L.w, y)], fill=MUTED + (int(200 * a),), width=3)
            T(d, (L.x0, y + 20), 'Total plate cost' if nj.get('kind', 'plate') == 'plate' else 'Total', int(48 * L.k), INK, L.w * .6, w=850, a=a)
            T(d, (L.x0 + L.w, y + 16), money(nj, count(sc['total']['value'], e)), int(60 * L.k), acc, 420, 'ra', 900, a)
            if e > 1.2: T(d, (L.x0 + L.w, y + 92 * L.k), f"matches the sheet total ({sc['total'].get('cell', 'file')})", int(28 * L.k), OKC, L.w, 'ra', 600, ease((e - 1.2) / .6) * fade)
    if t > tp:
        e = (t - tp - .3) / 1.4; cx, cy, R = L.W / 2, L.y0 + L.h * .42, 230 * L.k
        d.ellipse([cx - R, cy - R, cx + R, cy + R], outline=(60, 66, 84, 255), width=int(46 * L.k))
        if e > 0: d.arc([cx - R, cy - R, cx + R, cy + R], -90, -90 + 360 * D['pct'] * ease(e), fill=acc + (255,), width=int(46 * L.k))
        a = ease((t - tp) / .5)
        T(d, (cx, cy), pct(D['pct'] * ease(e)), int(96 * L.k), INK, 2 * R - 70, 'mm', 900, a)
        T(d, (cx, cy + 80 * L.k), 'food cost' if nj.get('kind', 'plate') == 'plate' else 'of price', int(36 * L.k), MUTED, 400, 'mm', 600, a)
        T(d, (cx, cy + R + 90 * L.k), f"{money(nj, sc['total']['value'])} cost / {money(nj, sc['price']['value'])} {sc['price'].get('label', 'price').lower()}", int(42 * L.k), INK, L.w, 'mm', 700, ease((t - tp - 1) / .5))
def bd_dur(sc): return 1.0 + len(sc['items']) * 1.5 + 2.8 + (4.6 if 'price' in sc else 0)
def sc_annualize(d, L, nj, sc, t, dur, k):
    D = derive(nj)[k]; acc = PAL[2]; y = L.y0 + 70 * L.k
    T(d, (L.x0, y), 'WHAT IF', int(40 * L.k), acc, L.w, w=850)
    T(d, (L.x0, y + 50 * L.k), sc['title'], int(60 * L.k), INK, L.w, w=850)
    if 'from' in D:
        e = ease((t - .6) / .6); f0, f1 = D['from'], D['from'] - sc['points'] / 100
        T(d, (L.W / 2, y + 190 * L.k), f"{pct(f0)}  ->  {pct(f0 + (f1 - f0) * ease((t - 1.0) / .8))}", int(92 * L.k), INK, L.w, 'ma', 900, e)
    lines = [(2.2, f"{sc['points']:g} points of {money(nj, sc['price']['value'])}", money(nj, D['per']) + ' each'),
             (4.0, f"x {sc['volume']['value']:g} {sc['volume'].get('label', 'a week')}", money(nj, D['per_period'])),
             (5.8, f"x {sc['periods']} {sc.get('period_label', 'weeks')}", None)]
    for i, (t0, lhs, rhs) in enumerate(lines):
        if t < t0: continue
        e = ease((t - t0) / .5); yy = y + (350 + i * 105) * L.k
        T(d, (L.x0, yy), lhs, int(48 * L.k), INK, L.w * .6, w=650, a=e)
        if rhs: T(d, (L.x0 + L.w, yy), '= ' + rhs, int(46 * L.k), INK, L.w * .4, 'ra', 850, e)
    if t > 5.8:
        e = (t - 5.8) / 2.0; yy = y + 690 * L.k
        T(d, (L.W / 2, yy), money(nj, count(D['total'], e), 2), int(120 * L.k), acc, L.w, 'ma', 900, ease((t - 5.8) / .4))
        T(d, (L.W / 2, yy + 145 * L.k), sc.get('total_label', 'a year, from this one item'), int(40 * L.k), MUTED, L.w, 'ma', 600, ease((t - 6.6) / .5))
        n = sc['periods']; cols = 13 if n == 52 else 12; rows = math.ceil(n / cols); cw = min(L.w / cols, 78); gx0 = L.W / 2 - cw * cols / 2; gy = yy + 215 * L.k
        lit = int(n * ease(e))
        for j in range(n):
            cx, cy = gx0 + (j % cols) * cw, gy + (j // cols) * cw * .6
            d.rounded_rectangle([cx + 4, cy, cx + cw - 4, cy + cw * .6 - 8], radius=6, fill=(acc if j < lit else (50, 56, 72)) + (255,))
def an_dur(sc): return 10.0
def sc_compare(d, L, nj, sc, t, dur, k):
    D = derive(nj)[k]; acc = PAL[1]; y = L.y0 + 70 * L.k; s0, s1 = sc['start'], sc['end']
    T(d, (L.x0, y), 'BEFORE AND AFTER', int(40 * L.k), acc, L.w, w=850); T(d, (L.x0, y + 50 * L.k), sc['title'], int(60 * L.k), INK, L.w, w=850)
    lab_w = L.w * .26; gx, gw = L.x0 + lab_w, L.w - lab_w; top = y + 230 * L.k; rh = min(96 * L.k, 560 * L.k / len(sc['rows']))
    X = lambda h: gx + (h - s0) / (s1 - s0) * gw
    for h in range(int(s0), int(s1) + 1, 2):
        d.line([(X(h), top - 10), (X(h), top + rh * len(sc['rows']))], fill=(60, 66, 84, 255), width=2)
        d.text((X(h), top - 18), f'{h % 12 or 12}{"a" if h % 24 < 12 else "p"}', font=font('sans', int(26 * L.k), 600), fill=MUTED, anchor='md')
    m = ease((t - 4.2) / 1.0)
    for i, r in enumerate(sc['rows']):
        e = ease((t - .4 - i * .25) / .5)
        if e <= 0: continue
        yy = top + i * rh; T(d, (L.x0, yy + rh * .2), r['label'], int(36 * L.k), INK, lab_w - 10, w=650, a=e)
        a0, a1 = [r['before'][j] + (r['after'][j] - r['before'][j]) * m for j in (0, 1)]
        col = tuple(int(c0 + (c1 - c0) * m) for c0, c1 in zip(MUTED, acc))
        if m > 0: d.rounded_rectangle([X(r['before'][0]), yy + rh * .18, X(r['before'][1]), yy + rh * .78], radius=10, outline=MUTED + (int(150 * m),), width=3)
        d.rounded_rectangle([X(a0), yy + rh * .18, X(a0) + (X(a1) - X(a0)) * e, yy + rh * .78], radius=10, fill=col + (255,))
    yy = top + rh * len(sc['rows']) + 60 * L.k; cost = lambda c: f" | {money(nj, c, 0)}" if c else ''
    if t > 2.6: T(d, (L.x0, yy), f"Before: {D['hb']:g} h{cost(D['cb'])}", int(46 * L.k), MUTED, L.w, w=800, a=ease((t - 2.6) / .5))
    if t > 5.6: T(d, (L.x0, yy + 80 * L.k), f"After: {D['ha']:g} h{cost(D['ca'])}", int(46 * L.k), acc, L.w, w=850, a=ease((t - 5.6) / .5))
    if t > 7.2:
        sv = f"{D['hb'] - D['ha']:g} hours" + (f" and {money(nj, D['cb'] - D['ca'], 0)}" if D['cb'] else '')
        T(d, (L.x0, yy + 190 * L.k), f"Saves {sv} {sc.get('per', 'a shift')}", int(54 * L.k), INK, L.w, w=900, a=ease((t - 7.2) / .5))
def cmp_dur(sc): return 10.5
def sc_end(d, L, nj, sc, t, dur):
    cy = L.y0 + L.h * .3; a = ease(t / .5)
    for i, ln in enumerate(nj.get('end', ['Your sheet. Your numbers.'])):
        T(d, (L.W / 2, cy + i * 90 * L.k), ln, int(64 * L.k), INK if i == 0 else PAL[0], L.w, 'mm', 850, ease((t - i * .5) / .5))
    yy = cy + 330 * L.k
    if nj.get('example'): T(d, (L.W / 2, yy), 'Example figures, not real data.', int(38 * L.k), MUTED, L.w, 'mm', 600, a)
    T(d, (L.W / 2, yy + 70 * L.k), nj.get('credit', 'AI-generated with Grok Bot Video Studio'), int(32 * L.k), MUTED, L.w, 'mm', 600, a)
# ---------- timeline + render ----------
def timeline(nj):
    tl, t = [('hook', None, 0, 3.0)], 3.0
    for k, sc in enumerate(nj['scenes']):
        dur = {'breakdown': bd_dur, 'annualize': an_dur, 'compare': cmp_dur}[sc['type']](sc)
        dur = math.ceil(dur * 2) / 2; tl.append((sc['type'], k, t, t + dur)); t += dur   # land scene changes on 120 BPM beats
    tl.append(('end', None, t, t + 4.5)); return tl
def make_frame(nj, aspect):
    L = Lay(aspect); B0 = bg(Lay(aspect), PAL[0]); tl = timeline(nj)
    BB = Image.new('RGBA', (L.W, L.H + 240)); BB.paste(B0.resize((L.W, L.H + 240)), (0, 0))
    def fn(t):
        im = BB.crop((0, int(120 + 110 * math.sin(t * .35)), L.W, int(120 + 110 * math.sin(t * .35)) + L.H))   # slow ambient glow drift; figures are never resampled
        im = im.copy(); ov = Image.new('RGBA', im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
        for typ, k, t0, t1 in tl:
            if t0 <= t < t1 or (typ == 'end' and t >= t0):
                lt = t - t0; sc = nj['scenes'][k] if k is not None else None
                fo = 1 - ease((lt - (t1 - t0 - .35)) / .35) if typ != 'end' else 1
                {'hook': lambda: sc_hook(d, L, nj, sc, lt, t1 - t0), 'end': lambda: sc_end(d, L, nj, sc, lt, t1 - t0),
                 'breakdown': lambda: sc_breakdown(d, L, nj, sc, lt, t1 - t0, k), 'annualize': lambda: sc_annualize(d, L, nj, sc, lt, t1 - t0, k),
                 'compare': lambda: sc_compare(d, L, nj, sc, lt, t1 - t0, k)}[typ]()
                if fo < 1: ov.putalpha(ov.split()[3].point(lambda v: int(v * max(fo, 0))))
        im.alpha_composite(ov)
        if nj.get('example'): vkit.chip(ImageDraw.Draw(im), L.x0, L.y0 - 10 if aspect == '9x16' else 30, 'EXAMPLE FIGURES', (247, 214, 92), int(36 * L.k))
        return im
    return fn, L, tl[-1][3]
def main(a):
    global COUNTUP
    nj = json.load(open(a[1])); COUNTUP = nj.get('countup', True); aspect = a[a.index('--aspect') + 1] if '--aspect' in a else '9x16'
    try: msgs = check(nj)
    except AssertionError as e: print('CHECK FAILED:', e); sys.exit(1)
    if a[0] == 'check': print('\n'.join(msgs)); return
    from pathlib import Path
    import numbers_extract as ne
    cp = Path(a[1]).resolve().parent / nj.get('cells_file', 'cells.json')
    assert cp.is_file(), f'Missing source cells: {cp}; extract and confirm the sheet first'
    txt, bad = ne.confirm(nj, json.load(open(cp)))
    assert bad == 0, 'Source cell MISMATCH or low-confidence CHECK: show confirm.md to the owner and resolve before drawing'
    fn, L, dur = make_frame(nj, aspect)
    if a[0] == 'stills':
        os.makedirs(a[2], exist_ok=True); tl = timeline(nj); ims = []
        for typ, k, t0, t1 in tl:
            for f in ((.85,) if typ in ('hook', 'end') else (.3, .62, .92)):
                tt = t0 + (t1 - t0) * f if typ != 'end' else t0 + 4.0
                p = f'{a[2]}/{len(ims):02d}_{typ}.png'; fn(tt).convert('RGB').save(p); ims.append(p)
        th = [Image.open(p).resize((L.W // 4, L.H // 4)) for p in ims]; cols = min(4, len(th)); rows = math.ceil(len(th) / cols)
        sh = Image.new('RGB', (cols * th[0].width, rows * th[0].height), 'white')
        for i, im in enumerate(th): sh.paste(im, ((i % cols) * im.width, (i // cols) * im.height))
        sh.save(f'{a[2]}/contact_sheet.jpg', quality=85); print(json.dumps({'stills': len(ims), 'duration': dur})); return
    import promo_kit
    wav = a[2] + '.music.wav'; promo_kit.music(wav, dur + .1)
    n = vkit.render_par(fn, L.W, L.H, dur, wav, a[2]); os.remove(wav)
    print(json.dumps({'out': a[2], 'frames': n, 'duration': dur, 'checks': msgs}))
if __name__ == '__main__': main(sys.argv[1:])
