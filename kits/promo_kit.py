"""promo_kit.py - code-drawn promo video kit (Pillow, numpy, ffmpeg; 0 generations). Style, text and card elements, original synthesized music(), render().
music(path, dur, bpm, seed) is shared by numbers_kit and case_study_kit; keep its signature stable."""
import os
import subprocess, math, wave, glob
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
FPS = 30
class Style:
    def __init__(s, w=1920, h=1080, accent="#6D3FF0", ink="#14111F", muted="#5B5670",
                 bg_top=(246,243,255), bg_bot=(255,255,255), font_path=None):
        s.W, s.H, s.accent, s.ink, s.muted, s.bg_top, s.bg_bot = w, h, accent, ink, muted, bg_top, bg_bot
        _fd = ['/usr/share/fonts', os.path.join(os.environ.get('VSTUDIO_HOME', '/workspace/vstudio'), 'fonts')]
        s.font_path = font_path or ([g for d in _fd for g in glob.glob(f'{d}/**/Inter*.ttf', recursive=True)] +
                      [g for d in _fd for g in glob.glob(f'{d}/**/DejaVuSans-Bold.ttf', recursive=True)])[0]
    def font(s, size, weight=800):
        f = ImageFont.truetype(s.font_path, size)
        try: f.set_variation_by_axes([32, weight])
        except Exception:
            try: f.set_variation_by_axes([weight])
            except Exception: pass
        return f
    def background(s):
        im = Image.new('RGB', (s.W, s.H)); d = ImageDraw.Draw(im)
        for y in range(s.H):
            t = y / s.H; d.line([(0, y), (s.W, y)], fill=tuple(int(s.bg_top[i]*(1-t) + s.bg_bot[i]*t) for i in range(3)))
        return im.convert('RGBA')
def ease(x): x = max(0, min(1, x)); return 1 - (1 - x) ** 3
def wrap(d, text, f, maxw):
    lines, cur = [], ''
    for w in text.split():
        t = (cur + ' ' + w).strip()
        if d.textlength(t, font=f) <= maxw: cur = t
        else: lines.append(cur); cur = w
    return lines + [cur]
def layer(st, fn):
    L = Image.new('RGBA', (st.W, st.H), (0, 0, 0, 0)); fn(ImageDraw.Draw(L), L); bb = L.getbbox()
    return (L.crop(bb), bb[0], bb[1])
def text_el(st, text, y, f, fill=None, x=None):
    def fn(d, L):
        w = d.textlength(text, font=f); assert w <= st.W - 120, ('text too wide', text, w)
        d.text(((st.W - w) / 2 if x is None else x, y), text, font=f, fill=fill or st.ink)
    return layer(st, fn)
def card_el(st, x, y, w, h, title, body, num, tf, bf):
    def fn(d, L):
        d.rounded_rectangle((x, y, x+w, y+h), radius=36, fill="white", outline=(222,214,250), width=3)
        d.ellipse((x+40, y+40, x+130, y+130), fill=st.accent); d.text((x+68, y+55), str(num), font=tf, fill="white")
        d.text((x+36, y+170), title, font=tf, fill=st.ink)
        for i, ln in enumerate(wrap(d, body, bf, w-72)): d.text((x+36, y+235+i*(bf.size+10)), ln, font=bf, fill=st.muted)
    return layer(st, fn)
def put(canvas, el, t, t0, dy=40, scale0=None, dur=0.4):
    img, x, y = el; e = ease((t - t0) / dur)
    if e <= 0: return
    im = img
    if scale0:
        s = scale0 + (1 - scale0) * e; w, h = int(img.width*s), int(img.height*s)
        im = img.resize((w, h), Image.LANCZOS); x += (img.width - w)//2; y += (img.height - h)//2; yy = y
    else: yy = y + (1 - e) * dy
    im = im.copy(); im.putalpha(im.split()[3].point(lambda v: int(v*e))); canvas.alpha_composite(im, (int(x), int(yy)))
def music(path, dur, bpm=120, seed=1):
    SR = 44100; beat = 60/bpm; n = int(SR*dur); out = np.zeros(n); rng = np.random.default_rng(seed)
    hz = lambda m: 440*2**((m-69)/12)
    def add(t0, sig, g=1.0):
        i = int(t0*SR)
        if i < n: s = sig[:n-i]; out[i:i+len(s)] += s*g
    def tt(d): return np.arange(int(SR*d))/SR
    def pluck(m): t = tt(.35); return sum(np.sin(2*np.pi*hz(m)*k*t)/k**1.3 for k in (1,2,3,4))*np.exp(-t*9)*.22
    def bass(m): t = tt(.28); f = hz(m); return (np.sin(2*np.pi*f*t)+.3*np.sin(4*np.pi*f*t))*np.exp(-t*5)*np.minimum(1, t*200)*.5
    def kick(): t = tt(.3); return np.sin(2*np.pi*np.cumsum(120*np.exp(-t*25)+45)/SR)*np.exp(-t*12)*.9
    def clap(): t = tt(.18); return rng.standard_normal(len(t))*np.exp(-t*30)*.28
    def hat(): t = tt(.05); return np.diff(rng.standard_normal(len(t)), prepend=0)*np.exp(-t*70)*.12
    def pad(ms, d): t = tt(d); return sum(np.sin(2*np.pi*hz(m)*t) for m in ms)/len(ms)*np.minimum(1, t*4)*np.minimum(1, (d-t)*6)*.16
    chords = [((48,52,55),(60,64,67,72)),((43,47,50),(59,62,67,71)),((45,48,52),(57,60,64,69)),((41,45,48),(57,60,65,69))]
    for b in range(int(dur/(4*beat))+1):
        ro, ch = chords[b%4]; t0 = b*4*beat; add(t0, pad(ch, 4*beat))
        for e in range(8):
            te = t0+e*beat/2
            if b >= 1: add(te, bass(ro[0] if e%2==0 or e==7 else ro[0]+12), .8 if b >= 2 else .5)
            add(te, pluck(ch[[0,1,2,3,2,1,2,3][e]]), 1.0)
            if b >= 2: add(te, hat())
        for q in range(4):
            if 2 <= b < 10: add(t0+q*beat, kick())
            if q in (1,3) and 3 <= b < 10: add(t0+q*beat, clap())
    fade = np.minimum(1, np.minimum(np.arange(n)/(SR*.3), (n-np.arange(n))/(SR*1.2)))
    out = out*fade; out = out/max(1e-9, np.abs(out).max())*.8
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes((out*32767).astype(np.int16).tobytes())
def render(st, frame_fn, dur, audio, out_mp4):
    p = subprocess.Popen(['ffmpeg','-y','-hide_banner','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{st.W}x{st.H}',
        '-r',str(FPS),'-i','-','-i',audio,'-t',str(dur),'-c:v','libx264','-crf','17','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',
        '-movflags','+faststart',out_mp4], stdin=subprocess.PIPE)
    for i in range(int(dur*FPS)): p.stdin.write(frame_fn(i/FPS).convert('RGB').tobytes())
    p.stdin.close(); p.wait()
