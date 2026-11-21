#!/usr/bin/env python3
"""Grok Bot Video Studio: finish a short music bed for a strobe ad (ffmpeg + numpy/librosa; no network).

  vstudio_audio_finish.py analyze SONG OUT.json [--bpm F] [--downbeat T] [--beats-per-bar 4]
      Tempo, a straight beat grid fitted to librosa beats, downbeats (the beat phase with the most
      low-end energy = the bass hits), per-downbeat bass strength, onsets and the last strong downbeat.

  vstudio_audio_finish.py finish SONG OUT.wav --info OUT.json
        [--bpm F --downbeat T]           override the analysed grid (seconds of the first downbeat)
        [--start-bar K] [--bars N]       body = N bars starting at downbeat K (default: as many full bars
                                         as the song has after downbeat K, up to its last strong downbeat)
        [--loop A:B --repeat R]          build the body from bars A..B (relative to the start) played R times,
                                         joined on the bar line (copy n runs 30 ms past the seam, copy n+1
                                         fades in over 8 ms); each join is masked (half-bar ratchet + sub boom)
        [--waves 3] [--risers waves|none|4,8,12]
                                         synthesized ratchet risers (accelerating clicks + rising noise) that end
                                         exactly on each wave downbeat (and on the final hit when ending=hit)
        [--ending hit|fade|loop]         hit: resolve on one final downbeat bass hit with its natural decay,
                                         a synthetic hall tail and a short squared fade (default);
                                         fade: fade out over the last --fade-bars bars, ending on the bar line;
                                         loop: end exactly on the bar line for a seamless video loop
        [--decay 0.28 --tail 1.75 --fade 0.5 --fade-bars 1]
        [--lufs -14 --tp -1.0]           two-pass loudnorm (linear), alimiter fallback; true peak <= --tp

The info JSON drives vstudio_strobe_cut.py: {"bar", "bpm", "bars", "offset", "final_hit", "duration",
"wave_starts", "downbeats", "seams", "risers", "ending", "lufs", "true_peak", "ending_db"}.
Risers and booms are code-synthesized SFX: record them as such in rights.md.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 48000
PRE = 0.006  # pre-roll before a downbeat so the transient attack is kept


class AudioError(ValueError):
    pass


# ---------------------------------------------------------------- io
def load(path, sr=SR):
    """Decode any audio/video file to float32 stereo (n, 2) at sr with ffmpeg."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "2", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise AudioError(f"could not decode {path}: {r.stderr.decode()[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def save(path, y, sr=SR, codec="pcm_s24le"):
    y = np.ascontiguousarray(np.clip(y, -1, 1), dtype=np.float32)
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ac", "2", "-ar", str(sr), "-i", "-",
                        "-c:a", codec, str(path)], input=y.tobytes(), capture_output=True)
    if r.returncode != 0:
        raise AudioError(r.stderr.decode()[-300:])
    return path


# ---------------------------------------------------------------- dsp (numpy only)
def fft_filter(x, sr, lo=None, hi=None, taper=0.15):
    """Zero-phase band filter in the frequency domain with cosine tapers (lo/hi in Hz; None = open)."""
    x = np.asarray(x, dtype=np.float64)
    n = x.shape[0]
    X = np.fft.rfft(x, axis=0)
    f = np.fft.rfftfreq(n, 1 / sr)
    g = np.ones_like(f)
    if lo:
        a, b = lo * (1 - taper), lo * (1 + taper)
        g *= np.clip((f - a) / max(b - a, 1e-9), 0, 1)
        g = np.where((f > a) & (f < b), 0.5 - 0.5 * np.cos(np.pi * (f - a) / (b - a)), g)
    if hi:
        a, b = hi * (1 - taper), hi * (1 + taper)
        h = np.clip((b - f) / max(b - a, 1e-9), 0, 1)
        h = np.where((f > a) & (f < b), 0.5 + 0.5 * np.cos(np.pi * (f - a) / (b - a)), h)
        g *= h
    if x.ndim == 2:
        g = g[:, None]
    return np.fft.irfft(X * g, n=n, axis=0)


def rms_db(y, sr, t0, t1):
    a, b = max(0, int(t0 * sr)), min(len(y), int(t1 * sr))
    if b <= a:
        return -120.0
    r = float(np.sqrt(np.mean(np.square(y[a:b]))))
    return round(20 * np.log10(max(r, 1e-6)), 1)


def segment(y, sr, t0, t1):
    a, b = int(round(t0 * sr)), int(round(t1 * sr))
    if a < 0 or b > len(y) or b <= a:
        raise AudioError(f"segment {t0:.3f}-{t1:.3f} s is outside the audio (0-{len(y) / sr:.3f} s)")
    return y[a:b].copy()


# ---------------------------------------------------------------- analysis
def fit_grid(beats):
    """Least-squares straight grid through beat times -> (period, phase0)."""
    b = np.asarray(beats, dtype=np.float64)
    if len(b) < 4:
        raise AudioError("need at least 4 beats to fit a grid")
    d = np.diff(b)
    med = np.median(d)
    idx = np.concatenate([[0], np.cumsum(np.maximum(1, np.round(d / med)))])
    p, c = np.polyfit(idx, b, 1)
    return float(p), float(c)


def low_flux(y, sr, hop=0.005, cutoff=150.0):
    """Low-band (< cutoff Hz) energy flux envelope: rises at kick / bass hits."""
    m = fft_filter(y.mean(axis=1) if y.ndim == 2 else y, sr, hi=cutoff)
    h = int(hop * sr)
    n = len(m) // h
    e = np.sqrt(np.mean(np.square(m[:n * h].reshape(n, h)), axis=1))
    flux = np.maximum(0, np.diff(e, prepend=e[:1]))
    return flux, hop


def strength_at(flux, hop, t, win=0.04):
    a, b = max(0, int((t - win) / hop)), min(len(flux), int((t + win) / hop) + 1)
    return float(flux[a:b].max()) if b > a else 0.0


def refine_grid(onsets, period, phase, iters=3, tol=0.12):
    """Re-fit period and phase to the onsets that sit within tol*period of a grid beat (kicks/ticks)."""
    for _ in range(iters):
        k = np.round((onsets - phase) / period)
        dev = onsets - (phase + k * period)
        m = np.abs(dev) < tol * period
        if m.sum() < 8:
            break
        period, phase = (float(v) for v in np.polyfit(k[m], onsets[m], 1))
    return period, phase


def analyze(y, sr=SR, bpm=None, downbeat=None, beats_per_bar=4, tempo_range=(90.0, 180.0)):
    dur = len(y) / sr
    flux, hop = low_flux(y, sr)
    onsets = []
    if bpm is None or downbeat is None:
        import librosa
        mono = librosa.resample(y.mean(axis=1).astype(np.float32), orig_sr=sr, target_sr=22050)
        tempo, frames = librosa.beat.beat_track(y=mono, sr=22050)
        beats = librosa.frames_to_time(frames, sr=22050)
        on = librosa.onset.onset_detect(y=mono, sr=22050, units="time")
        onsets = [round(float(t), 3) for t in on]
        period, phase = fit_grid(beats)
        if bpm:
            period = 60.0 / bpm
        else:
            lo, hi = tempo_range
            while 60.0 / period < lo:
                period /= 2
            while 60.0 / period > hi:
                period *= 2
            period, phase = refine_grid(np.asarray(on, dtype=np.float64), period, phase)
    else:
        period, phase = 60.0 / bpm, downbeat
    if bpm and downbeat is not None:
        first_db = float(downbeat)
    else:
        # grid beats, then choose the bar phase with the most low-end energy
        k0 = int(np.floor(-phase / period))
        grid = [phase + k * period for k in range(k0, int((dur - phase) / period) + 1)]
        grid = [t for t in grid if 0 <= t < dur]
        st = np.array([strength_at(flux, hop, t) for t in grid])
        best = max(range(beats_per_bar), key=lambda p: st[p::beats_per_bar].mean() if len(st[p::beats_per_bar]) else 0)
        first_db = grid[best]
        if downbeat is not None:
            first_db = float(downbeat)
    bar = period * beats_per_bar
    k_back = int(np.floor(first_db / bar))
    first_db -= k_back * bar
    downbeats = [first_db + k * bar for k in range(int((dur - first_db) / bar) + 1)]
    downbeats = [t for t in downbeats if t < dur - 0.05]
    strengths = [strength_at(flux, hop, t) for t in downbeats]
    top = sorted(strengths)[-max(1, len(strengths) // 2):]
    thr = 0.5 * float(np.median(top)) if top else 0
    strong = [t for t, s in zip(downbeats, strengths) if s >= thr]
    return {"duration": round(dur, 3), "bpm": round(60.0 / period, 3), "beat": period, "bar": bar,
            "beats_per_bar": beats_per_bar, "first_downbeat": round(downbeats[0], 4) if downbeats else 0.0,
            "first_strong_downbeat": round(strong[0], 4) if strong else None,
            "downbeats": [round(t, 4) for t in downbeats], "downbeat_strength": [round(s, 5) for s in strengths],
            "strong_downbeats": [round(t, 4) for t in strong],
            "last_strong_downbeat": round(strong[-1], 4) if strong else None, "onsets": onsets}


# ---------------------------------------------------------------- synthesis
def _click(sr, f1, f2, rng):
    L = int(0.018 * sr)
    nz = rng.standard_normal(L) * np.exp(-np.arange(L) / (0.004 * sr))
    a = fft_filter(nz, sr, f1, f2, taper=0.3)
    return a / (np.abs(a).max() or 1)


def ratchet(n, sr, t_end, dur, g0=0.04, g1=0.22, seed=7, swell=0.10):
    """Accelerating metallic clicks + a rising band-noise swell, ending exactly at t_end (s)."""
    rng = np.random.default_rng(seed)
    out = np.zeros((n, 2))
    t0 = t_end - dur
    t, i = max(0.0, t0), 0
    while t < t_end - 0.012:
        p = (t - t0) / dur
        c = _click(sr, 2200 + 2500 * p, 7000, rng) * (g0 + (g1 - g0) * p ** 1.5)
        s = int(t * sr)
        m = min(len(c), n - s)
        if m <= 0:
            break
        pan = (0.8, 1.0) if i % 2 else (1.0, 0.8)
        out[s:s + m, 0] += c[:m] * pan[0]
        out[s:s + m, 1] += c[:m] * pan[1]
        t += 0.11 * (1 - p) ** 1.6 + 0.022
        i += 1
    S, E = max(0, int(t0 * sr)), min(n, int(t_end * sr))
    if E > S:
        nz = fft_filter(rng.standard_normal((E - S, 2)), sr, 1500, 9000)
        nz /= (np.abs(nz).max() or 1)
        out[S:E] += nz * (np.linspace(0, 1, E - S) ** 2.5)[:, None] * swell
    return out


def boom(n, sr, t, g=0.5):
    """Sub boom: 48 Hz sine with a fast pitch drop and 0.18 s decay at t."""
    out = np.zeros((n, 2))
    L = int(0.6 * sr)
    tt = np.arange(L) / sr
    s = np.sin(2 * np.pi * (48 * tt + 40 * 0.04 * (1 - np.exp(-tt / 0.04)))) * np.exp(-tt / 0.18) * g
    i = int(t * sr)
    m = min(L, n - i)
    if m > 0:
        out[i:i + m] += s[:m, None]
    return out


def hall_ir(sr, length=1.9, rt=0.45, lp=2500, seed=7):
    rng = np.random.default_rng(seed)
    L = int(length * sr)
    ir = rng.standard_normal((L, 2)) * np.exp(-np.linspace(0, length, L) / rt)[:, None]
    ir = fft_filter(ir, sr, hi=lp)
    return ir / (np.abs(ir).sum(0).max() / 6)


def convolve(x, ir):
    n = len(x) + len(ir) - 1
    N = 1 << (n - 1).bit_length()
    return np.fft.irfft(np.fft.rfft(x, N, axis=0) * np.fft.rfft(ir, N, axis=0), N, axis=0)[:n]


# ---------------------------------------------------------------- building blocks
def loop_body(sec, sr, repeat, run_past=0.03, fade_in=0.008):
    """Repeat a bar-aligned section; each copy starts exactly on its bar line (no drift)."""
    N = len(sec)
    body = np.zeros((N * repeat, 2))
    nin = int(fade_in * sr)
    for r in range(repeat):
        c = sec.copy()
        if r > 0:
            c[:nin] *= np.linspace(0, 1, nin)[:, None]
        body[r * N:(r + 1) * N] += c
    return body


def resolve_hit(hit_src, sr, decay=0.28, tail=1.75, fade=0.5, wet=0.35, hold=0.12):
    """Final hit: the source downbeat with a held attack, exponential decay, synthetic hall tail and a
    squared fade to silence over the last `fade` seconds. Returns tail-length stereo audio."""
    L = len(hit_src)
    env = np.exp(-np.arange(L) / sr / decay)
    env[:int(hold * sr)] = 1.0
    env[int(hold * sr):] = np.exp(-(np.arange(L - int(hold * sr))) / sr / decay)
    hit = hit_src * env[:, None]
    wetsig = convolve(hit, hall_ir(sr))
    n = int(tail * sr)
    end = np.zeros((n, 2))
    m = min(n, L)
    end[:m] += hit[:m]
    end += wetsig[:n] * wet if len(wetsig) >= n else np.pad(wetsig, ((0, n - len(wetsig)), (0, 0))) * wet
    k = min(n, int(fade * sr))
    fo = np.ones(n)
    fo[n - k:] = np.linspace(1, 0, k) ** 2
    return end * fo[:, None]


def wave_bars(bars, waves):
    """Bar numbers where waves 2..N start (evenly split, rounded to whole bars)."""
    return [int(round(bars * i / waves)) for i in range(1, waves)]


def loudness(path, lufs=-14.0, tp=-1.5):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-af",
                        f"loudnorm=I={lufs}:TP={tp}:LRA=11:print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True).stderr
    return json.loads(re.findall(r"\{[^{}]+\}", r)[-1])


def normalize(src, dst, lufs=-14.0, tp=-1.0):
    """Two-pass linear loudnorm to lufs with true peak <= tp (aims 0.5 dB under); alimiter fallback."""
    aim = tp - 0.5
    m = loudness(src, lufs, aim)

    def run(af):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", af, "-ar", str(SR), "-c:a", "pcm_s24le",
                        str(dst)], check=True)
    run(f"loudnorm=I={lufs}:TP={aim}:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
        f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true")
    o = loudness(dst, lufs, aim)
    g = lufs - float(m["input_i"])
    for _ in range(4):  # peaky material: gain + limiter, nudging the gain until the level lands
        if abs(float(o["input_i"]) - lufs) <= 0.5 and float(o["input_tp"]) <= tp:
            break
        lim = 10 ** ((aim - 0.6) / 20)
        run(f"volume={g:.2f}dB,alimiter=limit={lim:.4f}:attack=5:release=60:level=0,aresample={SR}")
        o = loudness(dst, lufs, aim)
        g += lufs - float(o["input_i"])
    return {"lufs": float(o["input_i"]), "true_peak": float(o["input_tp"])}


def _parse_risers(spec, bars, waves, ending):
    if spec in (None, "", "none"):
        return []
    if spec == "waves":
        r = wave_bars(bars, waves)
        if ending == "hit":
            r.append(bars)
        return r
    out = [int(x) for x in str(spec).split(",") if x.strip()]
    for b in out:
        if not 0 < b <= bars:
            raise AudioError(f"riser bar {b} is outside 1..{bars}")
    return out


def finish(src, out_wav, info_path=None, bpm=None, downbeat=None, start_bar=0, bars=None, loop=None, repeat=1,
           waves=3, risers="waves", ending="hit", decay=0.28, tail=1.75, fade=0.5, fade_bars=1, lufs=-14.0, tp=-1.0,
           seam_mask=True, seed=7):
    sr = SR
    y = load(src, sr)
    an = analyze(y, sr, bpm, downbeat)
    bar = an["bar"]
    if not an["downbeats"]:
        raise AudioError("no downbeats found")
    first = (an["strong_downbeats"] or an["downbeats"])[0] if downbeat is None else float(downbeat)
    d0 = first + start_bar * bar
    if loop:
        a, b = loop
        if not 0 <= a < b:
            raise AudioError("--loop A:B needs 0 <= A < B")
        sec = segment(y, sr, d0 + a * bar - PRE, d0 + b * bar - PRE)
        body = np.concatenate([segment(y, sr, d0 - PRE, d0 + a * bar - PRE), loop_body(sec, sr, repeat)]) if a else loop_body(sec, sr, repeat)
        seams = [a + (b - a) * r for r in range(1, repeat)]
        nb = a + (b - a) * repeat
        if bars:
            if bars > nb:
                raise AudioError(f"--bars {bars} is longer than the looped body ({nb} bars)")
            body = body[:int(round(bars * bar * sr))]
            seams = [s for s in seams if s < bars]
            nb = bars
        hit_t = d0 + a * bar  # the downbeat that musically follows the loop's last bar
    else:
        avail = int((len(y) / sr - (d0 - PRE)) / bar)
        last = an["last_strong_downbeat"]
        if bars is None:
            nb = int(round((last - d0) / bar)) if last and last > d0 else avail
            nb = max(1, min(nb, avail))
        else:
            nb = bars
            if nb > avail:
                raise AudioError(f"the song has only {avail} full bars after the start; use --loop or fewer --bars")
        body = segment(y, sr, d0 - PRE, d0 + nb * bar - PRE)
        seams = []
        nxt = d0 + nb * bar
        if nxt + 0.9 <= len(y) / sr:
            hit_t = nxt
        else:  # the song ends here: borrow its strongest downbeat inside the body
            pairs = [(s_, t) for t, s_ in zip(an["downbeats"], an["downbeat_strength"]) if d0 - 1e-6 <= t < nxt]
            hit_t = max(pairs)[1] if pairs else d0
    T = nb * bar  # body length (s); the final hit / bar line sits at T (+PRE offset)
    if ending == "hit":
        hit_src = segment(y, sr, hit_t - PRE, min(len(y) / sr, hit_t - PRE + 0.9))
        m = int(0.02 * sr)
        body[-m:] *= np.linspace(1, 0.3, m)[:, None]
        mix = np.concatenate([body, resolve_hit(hit_src, sr, decay, tail, fade)])
    elif ending == "fade":
        k = min(len(body), int(fade_bars * bar * sr))
        g = np.ones(len(body))
        g[-k:] = np.cos(np.linspace(0, np.pi / 2, k)) ** 2
        mix = body * g[:, None]
    elif ending == "loop":
        mix = body.copy()
        e = int(0.004 * sr)
        mix[:e] *= np.linspace(0, 1, e)[:, None]
        mix[-e:] *= np.linspace(1, 0, e)[:, None]
    else:
        raise AudioError("ending must be hit, fade or loop")
    n = len(mix)
    sfx = np.zeros_like(mix)
    rb = _parse_risers(risers, nb, waves, ending)
    for b in rb:
        sfx += ratchet(n, sr, PRE + b * bar, bar, seed=seed + b)
    if seam_mask:
        for s in seams:
            sfx += ratchet(n, sr, PRE + s * bar, bar / 2, 0.03, 0.14, seed=seed + 100 + s) + boom(n, sr, PRE + s * bar, 0.35)
    if ending == "hit":
        sfx += boom(n, sr, PRE + T, 0.55)
    mix = mix + sfx
    mix /= (np.abs(mix).max() or 1) / 0.89
    raw = Path(out_wav).with_suffix(".raw.wav")
    save(raw, mix, sr)
    lv = normalize(raw, out_wav, lufs, tp)
    raw.unlink()
    fin = load(out_wav, sr)
    dur = len(fin) / sr
    hit_at = PRE + T
    info = {"source": str(src), "bpm": an["bpm"], "bar": bar, "bars": nb, "offset": PRE,
            "downbeats": [round(PRE + k * bar, 4) for k in range(nb + (1 if ending == "hit" else 0))],
            "wave_starts": [round(PRE + b * bar, 4) for b in [0] + wave_bars(nb, waves)], "waves": waves,
            "final_hit": round(hit_at, 4) if ending == "hit" else None, "body_end": round(PRE + T, 4),
            "duration": round(dur, 4), "seams": [round(PRE + s * bar, 4) for s in seams],
            "risers": [round(PRE + b * bar, 4) for b in rb], "ending": ending,
            "source_start": round(d0 - PRE, 4), "loop": list(loop) if loop else None, "repeat": repeat,
            "lufs": round(lv["lufs"], 2), "true_peak": round(lv["true_peak"], 2)}
    if ending == "hit":
        info["ending_db"] = {"hit": rms_db(fin, sr, hit_at, hit_at + 0.1), "+0.5s": rms_db(fin, sr, hit_at + 0.45, hit_at + 0.55),
                             "+0.9s": rms_db(fin, sr, hit_at + 0.85, hit_at + 0.95), "last_50ms": rms_db(fin, sr, dur - 0.05, dur)}
    else:
        info["ending_db"] = {"last_50ms": rms_db(fin, sr, dur - 0.05, dur)}
    if info_path:
        Path(info_path).write_text(json.dumps(info, indent=1))
    return info


def _loop(s):
    a, b = s.split(":")
    return int(a), int(b)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("analyze")
    a1.add_argument("song"); a1.add_argument("out")
    a1.add_argument("--bpm", type=float); a1.add_argument("--downbeat", type=float)
    a1.add_argument("--beats-per-bar", type=int, default=4)
    a1.add_argument("--tempo-range", default="90:180", help="fold the detected tempo into this BPM range")
    f = sub.add_parser("finish")
    f.add_argument("song"); f.add_argument("out")
    f.add_argument("--info", required=True)
    f.add_argument("--bpm", type=float); f.add_argument("--downbeat", type=float)
    f.add_argument("--start-bar", type=int, default=0); f.add_argument("--bars", type=int)
    f.add_argument("--loop", type=_loop); f.add_argument("--repeat", type=int, default=2)
    f.add_argument("--waves", type=int, default=3); f.add_argument("--risers", default="waves")
    f.add_argument("--ending", choices=("hit", "fade", "loop"), default="hit")
    f.add_argument("--decay", type=float, default=0.28); f.add_argument("--tail", type=float, default=1.75)
    f.add_argument("--fade", type=float, default=0.5); f.add_argument("--fade-bars", type=float, default=1)
    f.add_argument("--lufs", type=float, default=-14.0); f.add_argument("--tp", type=float, default=-1.0)
    f.add_argument("--no-seam-mask", action="store_true"); f.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "analyze":
            r = analyze(load(a.song), SR, a.bpm, a.downbeat, a.beats_per_bar,
                        tuple(float(v) for v in a.tempo_range.split(":")))
            Path(a.out).write_text(json.dumps(r, indent=1))
            print(json.dumps({k: r[k] for k in ("bpm", "first_downbeat", "last_strong_downbeat")}))
        else:
            r = finish(a.song, a.out, a.info, a.bpm, a.downbeat, a.start_bar, a.bars, a.loop,
                       a.repeat if a.loop else 1, a.waves, a.risers, a.ending, a.decay, a.tail, a.fade, a.fade_bars,
                       a.lufs, a.tp, not a.no_seam_mask, a.seed)
            print(json.dumps({k: r[k] for k in ("bars", "bar", "duration", "final_hit", "lufs", "true_peak", "ending_db")}))
    except (AudioError, OSError, subprocess.CalledProcessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
