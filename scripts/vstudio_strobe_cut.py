#!/usr/bin/env python3
"""Grok Bot Video Studio: beat-synced strobe cutter (stills, short clips and code-drawn message frames).

  vstudio_strobe_cut.py plan MANIFEST.json MUSIC_INFO.json EDL.json [--cap 4] [--min-hold 1.3]
                             [--words-rate 0.1] [--balance-tol 0.15] [--ending resolved|loop] [--seed 1]
  vstudio_strobe_cut.py check MANIFEST.json MUSIC_INFO.json EDL.json
  vstudio_strobe_cut.py render MANIFEST.json EDL.json OUT.mp4 [--audio music.wav] [--scale 1.0]
                               [--crf 18] [--workers N] [--thumbnail thumb.jpg] [--max-mb 95]

MUSIC_INFO.json comes from `vstudio_audio_finish.py finish` (bar, bars, offset, wave_starts, final_hit,
duration). A plain `vstudio_audio_finish.py analyze` JSON also works (bars = whole bars in the song).

MANIFEST.json:
  {"brand": {"name": "NORTHWIND LAB", "accent": "#FF4D00", "mark": "square"},
   "aspect": "9:16", "fps": 25, "grain": 11, "halftone": 0.10, "label": "AI GENERATED",
   "include": ["frames/frames.json"],                 # assets written by vstudio_brand_frames.py frames
   "assets": [
     {"id": "STARE", "path": "stills/stare.jpg", "role": "hero", "opener": true, "clear_y": 0.54, "eyes_y": 0.35,
      "focus": [0.5, 0.4]},  # clear_y: lowest point to keep above the wordmark (nose tip); eyes_y: the eye line
                             # (always kept in frame and above the band); both 0-1 of the source height
     {"id": "HERO", "path": "stills/hero.jpg", "role": "hero", "end": true},
     {"id": "RACK", "path": "stills/rack.jpg", "role": "object"},
     {"id": "TURN", "path": "clips/turn.mp4", "kind": "clip", "role": "hero", "clip_in": 0.3, "clip_len": 0.6},
     {"id": "M06", "path": "frames/M06.png", "role": "message", "key": true, "words": 13, "text": true}]}
  Roles: hero (the character), object (generated object/texture stills), message (code-drawn copy frames),
  texture (code-drawn flashes, halftone, rings). Optional per asset: crop [x0,y0,x1,y1], zoom, focus.

How the cut is built (one unit = 1/8, 1/16 or 1/32 bar, picked so a unit is about 2-3 frames):
  * the bars split into N waves (the info's wave_starts); each wave opens fast (1-unit cells = 2-3 frames,
    2-unit cells on every downbeat/bass hit), slows in the middle (2-unit cells, 4-unit holds on downbeats)
    and ends on holds: key lines (hold >= max(--min-hold, --words-rate x words), beat-snapped), and clips;
  * every downbeat is a cut; holds never cross a bar line unless the line needs more than a bar to read;
  * the last bar before a resolved ending is a fast run into the final hit, then the end asset holds to the
    end of the audio with the AI label; --ending loop ends on the bar line for a seamless loop instead;
  * cells alternate image (hero/object) and message/texture; downbeat cells take a hero still;
  * each asset is used at most --cap times (default 4); past that the cutter makes free variants (mirror flip,
    punch-in crop, accent duotone; never a mirror flip of a text frame) before it ever breaks the cap;
  * wave cut counts are balanced (cells split in the quietest wave) to within --balance-tol.
The render overlays the fixed centred wordmark (auto black/white + halo) and a grain + halftone pass on every
frame, re-frames clear_y assets so the eye line stays off the wordmark in every aspect, and adds the AI label on
the end hold.
Needs Pillow, numpy and ffmpeg; imports vstudio_brand_frames.py from the same folder.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
from multiprocessing import Pool
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vstudio_brand_frames as BF  # noqa: E402

ROLES = ("hero", "object", "message", "texture")
GROUP_A = ("hero", "object")
GROUP_B = ("message", "texture")


class CutError(ValueError):
    pass


# ---------------------------------------------------------------- inputs
def load_manifest(path):
    path = Path(path)
    m = json.loads(path.read_text())
    base = path.parent
    assets = []
    for inc in m.get("include", []):
        ip = (base / inc) if not Path(inc).is_absolute() else Path(inc)
        for a in json.loads(ip.read_text()).get("assets", []):
            a = dict(a)
            if not Path(a["path"]).is_absolute():
                a["path"] = str((ip.parent / a["path"]).resolve())
            assets.append(a)
    for a in m.get("assets", []):
        a = dict(a)
        if not Path(a["path"]).is_absolute():
            a["path"] = str((base / a["path"]).resolve())
        assets.append(a)
    ids = set()
    for a in assets:
        a.setdefault("kind", "clip" if str(a["path"]).lower().endswith((".mp4", ".mov", ".webm")) else "still")
        a.setdefault("role", "hero")
        if a["role"] not in ROLES:
            raise CutError(f"asset {a.get('id')}: role must be one of {ROLES}")
        if not a.get("id") or "~" in a["id"] or a["id"] in ids:
            raise CutError(f"asset ids must be unique and must not contain '~' (got {a.get('id')!r})")
        ids.add(a["id"])
        a.setdefault("text", a["role"] == "message")
        a.setdefault("key", False)
        a.setdefault("words", 0)
    m["assets"] = assets
    m.setdefault("aspect", "9:16")
    m.setdefault("fps", 25)
    if "brand" not in m:
        raise CutError("manifest needs a brand {name, accent}")
    return m


def load_info(path):
    info = json.loads(Path(path).read_text())
    if "bars" not in info:  # an `analyze` result: use whole bars from the first strong downbeat
        d0 = info.get("first_strong_downbeat") or info["first_downbeat"]
        info = {"bar": info["bar"], "bars": int((info["duration"] - d0) / info["bar"]), "offset": 0.0,
                "wave_starts": None, "final_hit": None, "duration": None, "ending": "loop", "source_start": d0}
        info["duration"] = info["bars"] * info["bar"]
        info["body_end"] = info["duration"]
    info.setdefault("offset", 0.0)
    info.setdefault("body_end", info["offset"] + info["bars"] * info["bar"])
    return info


def choose_subdiv(bar, fps):
    """Units per bar so one unit is ~2.5 frames (never under 2 if a coarser grid allows it)."""
    best = None
    for n in (8, 16, 32):
        fr = bar / n * fps
        score = abs(fr - 2.5) + (5 if fr < 2 else 0)
        if best is None or score < best[0]:
            best = (score, n)
    return best[1]


def hold_units(words, unit, beat_units, min_hold=1.3, rate=0.1, fps=25.0):
    """Units for a key-line hold: max(min_hold, rate x words), snapped up to whole beats (within one frame)."""
    need = max(min_hold, rate * words)
    beats = math.ceil((need - 1.0 / fps) / (unit * beat_units) - 1e-9)
    return int(max(1, beats) * beat_units), need


# ---------------------------------------------------------------- planning
def _zone_map(L, n, final_run):
    """Per-unit zone for a wave of L units: fast (first half), medium, hold (last quarter), optional final fast bar."""
    Lb = max(1, L // n)
    fast_b = max(1, math.ceil(Lb * 0.5))
    hold_b = max(1, round(Lb * 0.25))
    z = []
    for u in range(L):
        b = u // n
        if final_run and Lb >= 3 and b == Lb - 1:
            z.append("fast")
        elif b < fast_b:
            z.append("fast")
        elif b >= Lb - hold_b - (1 if final_run and Lb >= 3 else 0):
            z.append("hold")
        else:
            z.append("medium")
    return z


def _place_events(events, L, n, zones, occupied):
    """Place events (len units) at bar lines/beats inside a wave. Returns [(start, len, ev)], dropped."""
    beat = n // 4
    Lb = L // n
    bars_by_zone = {k: [b for b in range(Lb) if zones[b * n] == k] for k in ("hold", "medium", "fast")}
    cands = ([b * n for b in reversed(bars_by_zone["hold"])] + [b * n for b in reversed(bars_by_zone["medium"])]
             + [b * n + k * beat for b in reversed(bars_by_zone["hold"] + bars_by_zone["medium"]) for k in (1, 2, 3)]
             + [b * n for b in bars_by_zone["fast"] if b > 0]
             + [u for u in range(beat, L, beat)])
    placed, dropped = [], []
    for ev in events:
        ln = ev["len"]
        ok = None
        for s in cands:
            e = s + ln
            if e > L or any(occupied[s:e]):
                continue
            crosses = (s // n) != ((e - 1) // n)
            if crosses and ln <= n:
                continue
            ok = s
            break
        if ok is None:
            dropped.append(ev)
            continue
        for u in range(ok, ok + ln):
            occupied[u] = True
        placed.append((ok, ln, ev))
    return placed, dropped


def _fill(L, n, zones, occupied):
    cells = []
    u = 0
    while u < L:
        if occupied[u]:
            u += 1
            continue
        nxt_bar = (u // n + 1) * n
        e = u
        while e < L and not occupied[e] and e < nxt_bar:
            e += 1
        while u < e:
            z = zones[u]
            if u % n == 0:
                ln = {"fast": 2, "medium": 4, "hold": 4}[z]
                kind = "hit"
            elif z == "fast":
                ln, kind = 1, "fast"
            else:
                ln, kind = 2, "fill"
            ln = min(ln, e - u)
            cells.append({"u": u, "len": ln, "kind": kind, "zone": z})
            u += ln
    return cells


def _balance(waves, n, tol, warnings):
    def counts():
        return [len(w["cells"]) for w in waves]
    for _ in range(4000):
        c = counts()
        if max(c) == 0 or (max(c) - min(c)) / max(c) <= tol:
            return
        lo = c.index(min(c))
        cells = waves[lo]["cells"]
        cand = [i for i, x in enumerate(cells) if x["kind"] in ("fill", "hit") and x["len"] >= 2]
        if cand:
            pri = sorted(cand, key=lambda i: ({"medium": 0, "hold": 1, "fast": 2}[cells[i]["zone"]], -cells[i]["u"]))
            i = pri[0]
            x = cells[i]
            h = x["len"] // 2
            a = dict(x, len=h)
            b = dict(x, u=x["u"] + h, len=x["len"] - h, kind="fast" if h == 1 else "fill")
            cells[i:i + 1] = [a, b]
            continue
        hi = c.index(max(c))
        cells = waves[hi]["cells"]
        merged = False
        for i in range(len(cells) - 1, 0, -1):
            x, y = cells[i - 1], cells[i]
            if (x["kind"] == "fast" and y["kind"] == "fast" and x["u"] + x["len"] == y["u"] and y["u"] % n != 0):
                cells[i - 1:i + 1] = [dict(x, len=x["len"] + y["len"], kind="fill")]
                merged = True
                break
        if not merged:
            warnings.append(f"wave cut counts could not be balanced within {tol:.0%}: {c}")
            return


class Picker:
    def __init__(self, assets, cap, seed):
        self.by_id = {a["id"]: a for a in assets}
        self.uses = {}
        self.cap = cap
        self.rng = random.Random(seed)
        self.recent = []
        self.warnings = []

    @staticmethod
    def variants_for(a):
        if a["kind"] == "clip":
            return []
        if a["role"] in GROUP_A:
            return ["flip", "zoom", "duo"]
        if a.get("text"):
            return ["duo"]
        return ["flip", "duo"]

    def use(self, vid, count=True):
        if count:
            self.uses[vid] = self.uses.get(vid, 0) + 1
        self.recent = (self.recent + [vid.split("~")[0]])[-2:]

    def pick(self, roles, prefer=None):
        pool = [a for a in self.by_id.values() if a["role"] in roles and a["kind"] == "still"]
        if prefer:
            pp = [a for a in pool if a["role"] in prefer]
            pool = pp or pool
        if not pool:
            return None
        ids = [a["id"] for a in pool]
        for allow in ([""], None):  # base ids first, then variants
            cands = []
            for a in pool:
                vs = [""] if allow else [""] + self.variants_for(a)
                for v in vs:
                    vid = a["id"] + (f"~{v}" if v else "")
                    if self.uses.get(vid, 0) < self.cap and a["id"] not in self.recent:
                        cands.append(vid)
            if cands:
                lo = min(self.uses.get(v, 0) for v in cands)
                best = sorted(v for v in cands if self.uses.get(v, 0) == lo)
                # prefer plain ids, then variants
                plain = [v for v in best if "~" not in v]
                vid = self.rng.choice(plain or best)
                self.use(vid)
                return vid
        cands = [i for i in ids if i not in self.recent] or ids
        vid = min(cands, key=lambda i: (self.uses.get(i, 0), i))
        w = f"use cap {self.cap} exceeded for {vid}: add more assets"
        if w not in self.warnings:
            self.warnings.append(w)
        self.use(vid)
        return vid


def plan(manifest, info, cap=4, min_hold=1.3, words_rate=0.1, balance_tol=0.15, ending=None, seed=1):
    fps = float(manifest["fps"])
    bar, bars, off = float(info["bar"]), int(info["bars"]), float(info.get("offset", 0.0))
    n = choose_subdiv(bar, fps)
    unit = bar / n
    beat_u = n // 4
    U = bars * n
    ending = ending or ("resolved" if info.get("final_hit") else "loop")
    if ending == "resolved" and not info.get("final_hit"):
        raise CutError("ending 'resolved' needs a final_hit in the music info (vstudio_audio_finish.py --ending hit)")
    assets = manifest["assets"]
    by_id = {a["id"]: a for a in assets}
    heroes = [a for a in assets if a["role"] == "hero" and a["kind"] == "still"]
    if not heroes:
        raise CutError("need at least one hero still")
    opener = next((a for a in assets if a.get("opener")), heroes[0])
    end_a = next((a for a in assets if a.get("end")), heroes[-1])
    keys = [a for a in assets if a.get("key")]
    clips = [a for a in assets if a["kind"] == "clip"]
    ws = info.get("wave_starts") or [off + bar * round(bars * i / 3) for i in range(3)]
    wave_u = sorted({min(U, max(0, int(round((t - off) / unit)))) for t in ws} | {0})
    wave_u = [w - (w % n) for w in wave_u]
    bounds = list(zip(wave_u, wave_u[1:] + [U]))
    W = len(bounds)
    warnings = []
    # events -> waves
    evs = [[] for _ in range(W)]
    for j, k in enumerate(keys):
        ln, need = hold_units(k.get("words", 0), unit, beat_u, min_hold, words_rate, fps)
        if ln > n:
            warnings.append(f"{k['id']}: {need:.2f} s hold is longer than a bar; it will cover a downbeat")
        evs[min(W - 1, j * W // max(1, len(keys)))].append({"id": k["id"], "len": ln, "kind": "key", "need": round(need, 3)})
    for j, c in enumerate(clips):
        cl = float(c.get("clip_len", 0.6))
        ln = max(2, int(round(cl / unit / 2)) * 2)
        evs[min(W - 1, j * W // max(1, len(clips)))].append({"id": c["id"], "len": min(ln, n), "kind": "clip"})
    waves = []
    for w, (s, e) in enumerate(bounds):
        L = e - s
        final_run = (w == W - 1 and ending == "resolved")
        zones = _zone_map(L, n, final_run)
        occ = [False] * L
        occ[0] = False
        placed, dropped = _place_events(sorted(evs[w], key=lambda x: (x["kind"] != "key", -x["len"])), L, n, zones, occ)
        for d in dropped:
            warnings.append(f"no room for {d['kind']} {d['id']} in wave {w + 1}; dropped")
        cells = _fill(L, n, zones, occ)
        for st, ln, ev in placed:
            cells.append({"u": st, "len": ln, "kind": ev["kind"], "zone": zones[st], "asset": ev["id"],
                          **({"need": ev["need"]} if "need" in ev else {})})
        cells.sort(key=lambda x: x["u"])
        waves.append({"start_u": s, "len_u": L, "cells": cells})
    _balance(waves, n, balance_tol, warnings)
    # assets
    pk = Picker(assets, cap, seed)
    # reserve the fixed uses first so fill picks can never push them over the cap
    pk.use(opener["id"])
    if ending == "resolved":
        pk.use(end_a["id"])
    for wv in waves:
        for c in wv["cells"]:
            if c.get("asset"):
                pk.use(c["asset"])
    pk.recent = []
    shots = []
    first = True
    toggle = 0
    for w, wv in enumerate(waves):
        for c in wv["cells"]:
            u = wv["start_u"] + c["u"]
            if first and not c.get("asset"):
                vid, kind = opener["id"], "opener"
                pk.use(vid, count=False)
                first = False
            elif c.get("asset"):
                vid, kind = c["asset"], c["kind"]
                pk.use(vid, count=False)
                first = False
            elif c["kind"] == "hit":
                vid, kind = pk.pick(GROUP_A, prefer=("hero",)), "hit"
                toggle = 1
            else:
                vid = pk.pick(GROUP_A if toggle == 0 else GROUP_B) or pk.pick(GROUP_A + GROUP_B)
                kind = c["kind"]
                toggle ^= 1
            shots.append({"u": u, "len": c["len"], "asset": vid, "kind": kind, "wave": w + 1})
    if ending == "loop" and len(shots) > 1 and shots[-1]["asset"].split("~")[0] == opener["id"]:
        shots[-1]["asset"] = pk.pick(GROUP_B) or shots[-1]["asset"]
    # timing (frames)
    if ending == "resolved":
        total = float(info["duration"])
    else:
        total = off + U * unit
    NF = int(round(total * fps))
    out = []
    for i, s in enumerate(shots):
        t0 = 0.0 if i == 0 else off + s["u"] * unit
        f0 = int(round(t0 * fps))
        out.append({"t": t0, "f": f0, **s})
    if ending == "resolved":
        th = float(info["final_hit"])
        out.append({"t": th, "f": int(round(th * fps)), "u": U, "len": None, "asset": end_a["id"], "kind": "end",
                    "wave": W})
    rows = []
    for i, s in enumerate(out):
        f1 = out[i + 1]["f"] if i + 1 < len(out) else NF
        if f1 <= s["f"]:
            warnings.append(f"shot {i} ({s['asset']}) rounds to 0 frames; merged")
            continue
        a = by_id[s["asset"].split("~")[0]]
        row = {"i": len(rows), "asset": s["asset"].split("~")[0], "variant": (s["asset"].split("~") + [None])[1],
               "role": a["role"], "kind": s["kind"], "wave": s["wave"], "start": round(s["f"] / fps, 4),
               "dur": round((f1 - s["f"]) / fps, 4), "frame": s["f"], "frames": f1 - s["f"]}
        if a["kind"] == "clip":
            row["clip_in"] = float(a.get("clip_in", 0.0))
        rows.append(row)
    warnings += pk.warnings
    edl = {"version": 1, "fps": fps, "aspect": manifest["aspect"], "bar": bar, "bars": bars, "offset": off,
           "units_per_bar": n, "unit": unit, "frames": NF, "duration": round(NF / fps, 4), "ending": ending,
           "cap": cap, "min_hold": min_hold, "words_rate": words_rate, "balance_tol": balance_tol, "seed": seed,
           "wave_bounds": [round(off + s * unit, 4) for s, _ in bounds], "shots": rows, "warnings": warnings}
    edl["stats"] = stats(edl)
    return edl


# ---------------------------------------------------------------- checks
def stats(edl):
    rows = edl["shots"]
    uses = {}
    base = {}
    for r in rows:
        vid = r["asset"] + (f"~{r['variant']}" if r.get("variant") else "")
        uses[vid] = uses.get(vid, 0) + 1
        base[r["asset"]] = base.get(r["asset"], 0) + 1
    nw = max([r["wave"] for r in rows] + [1])
    per_wave = [sum(1 for r in rows if r["wave"] == w and r["kind"] != "end") for w in range(1, nw + 1)]
    return {"shots": len(rows), "unique_assets": len(base), "unique_with_variants": len(uses),
            "cuts_per_wave": per_wave, "max_uses": max(uses.values()) if uses else 0,
            "most_used": sorted(uses.items(), key=lambda x: -x[1])[:5],
            "fast_cells": sum(1 for r in rows if r["frames"] <= 3), "holds_ge_0_4s": sum(1 for r in rows if r["dur"] >= 0.4),
            "longest": max((r["dur"] for r in rows), default=0), "uses": uses}


def check(manifest, info, edl):
    """Rule check for QA. Returns {ok, problems, checks}."""
    fps = edl["fps"]
    rows = edl["shots"]
    by_id = {a["id"]: a for a in manifest["assets"]}
    starts = [r["frame"] for r in rows]
    probs, ch = [], {}
    # every downbeat up to the body end is a cut (+-1 frame)
    db = info.get("downbeats") or [info.get("offset", 0) + k * info["bar"] for k in range(info["bars"] + 1)]
    lim = info.get("final_hit") or info.get("body_end")
    missing, covered = [], []
    for t in db:
        f = int(round(t * fps))
        if t > lim + 1e-6 or f >= edl["frames"] or any(abs(f - s) <= 1 for s in starts):
            continue
        r = next((r for r in rows if r["frame"] <= f < r["frame"] + r["frames"]), None)
        if r and r["kind"] == "key" and r["dur"] > info["bar"]:
            covered.append(round(t, 3))  # a key line that needs more than a bar to read
        else:
            missing.append(t)
    if covered:
        ch["downbeats_under_long_key_holds"] = covered
    n_db = len([t for t in db if t <= lim + 1e-6 and int(round(t * fps)) < edl["frames"]])
    ch["downbeats_on_cuts"] = f"{n_db - len(missing) - len(covered)}/{n_db}"
    if missing:
        probs.append(f"downbeats without a cut: {[round(t, 3) for t in missing]}")
    # key holds
    kh = {}
    for a in manifest["assets"]:
        if a.get("key"):
            need = max(edl["min_hold"], edl["words_rate"] * a.get("words", 0))
            best = max([r["dur"] for r in rows if r["asset"] == a["id"]] + [0])
            kh[a["id"]] = {"need": round(need, 3), "held": best}
            if best + 1.0 / fps < need:
                probs.append(f"key line {a['id']} holds {best:.2f} s < {need:.2f} s")
    ch["key_holds"] = kh
    st = stats(edl)
    ch["max_uses"] = st["max_uses"]
    if st["max_uses"] > edl["cap"]:
        probs.append(f"an asset is used {st['max_uses']} times (cap {edl['cap']}): {st['most_used'][0]}")
    pw = st["cuts_per_wave"]
    ch["cuts_per_wave"] = pw
    if pw and max(pw) and (max(pw) - min(pw)) / max(pw) > edl["balance_tol"] + 1e-9:
        probs.append(f"waves unbalanced: {pw}")
    op = next((a for a in manifest["assets"] if a.get("opener")), None)
    if op and rows and rows[0]["asset"] != op["id"]:
        probs.append(f"frame 0 is {rows[0]['asset']}, not the opener {op['id']}")
    ch["frame0"] = rows[0]["asset"] if rows else None
    adj = [r["i"] for p, r in zip(rows, rows[1:]) if p["asset"] == r["asset"]]
    if adj:
        probs.append(f"same asset back to back at shots {adj}")
    short = [r["i"] for r in rows if r["frames"] < 2 and r["kind"] != "end"]
    ch["one_frame_cells"] = len(short)
    if short:
        probs.append(f"{len(short)} cells are a single frame")
    if edl["ending"] == "resolved":
        last = rows[-1]
        ok = last["kind"] == "end" and abs(last["start"] - info["final_hit"]) <= 1.0 / fps + 1e-6
        ch["end_hold"] = round(last["dur"], 3)
        if not ok:
            probs.append("the end hold does not start on the final hit")
    missing_assets = [r["asset"] for r in rows if r["asset"] not in by_id]
    if missing_assets:
        probs.append(f"unknown assets in EDL: {sorted(set(missing_assets))}")
    return {"ok": not probs, "problems": probs, "checks": ch, "stats": {k: v for k, v in st.items() if k != "uses"}}


# ---------------------------------------------------------------- render
_S = {}


def _base(vid):
    c = _S["cache"]
    if vid in c:
        return c[vid]
    aid, _, var = vid.partition("~")
    a = _S["assets"][aid]
    br = _S["brand"]
    im, _ = BF.fit_clear(Image.open(a["path"]), br, tuple(a.get("focus", (0.5, 0.4))), a.get("crop"),
                         1.35 if var == "zoom" else float(a.get("zoom", 1.0)), a.get("clear_y"), eyes_y=a.get("eyes_y"))
    if var == "flip":
        im = ImageOps.mirror(im)
    elif var == "duo":
        im = BF.duotone(im, br)
    c[vid] = im
    return im


def _clip_frame(aid, k):
    br = _S["brand"]
    fs = _S["clipframes"][aid]
    a = _S["assets"][aid]
    p = fs[min(len(fs) - 1, max(0, k))]
    im, _ = BF.fit_clear(Image.open(p), br, tuple(a.get("focus", (0.5, 0.4))), a.get("crop"),
                         float(a.get("zoom", 1.0)), a.get("clear_y"), eyes_y=a.get("eyes_y"))
    return im


def _shot_index(f):
    return int(np.searchsorted(_S["starts"], f, side="right") - 1)


def render_frame(f):
    rows = _S["rows"]
    i = _shot_index(f)
    r = rows[i]
    if r.get("clip_in") is not None and _S["assets"][r["asset"]]["kind"] == "clip":
        im = _clip_frame(r["asset"], f - r["frame"])
    else:
        vid = r["asset"] + (f"~{r['variant']}" if r.get("variant") else "")
        im = _base(vid).copy()
    br = _S["brand"]
    out, _ = br.overlay(im)
    out = BF.halftone_grain(out, _S["halftone"], _S["grain"], seed=f)
    if r["kind"] == "end" or (_S["label_tail"] and f >= _S["nf"] - _S["label_tail"]):
        out = br.label(out, _S["label"])
    return out


def _work(f):
    return render_frame(f).tobytes()


def _extract_clips(manifest, rows, fps, tmp, scale_size):
    out = {}
    for a in manifest["assets"]:
        if a["kind"] != "clip":
            continue
        used = [r for r in rows if r["asset"] == a["id"]]
        if not used:
            continue
        d = Path(tmp) / a["id"]
        d.mkdir(parents=True, exist_ok=True)
        cin = float(a.get("clip_in", 0.0))
        dur = max(r["dur"] for r in used) + 0.2
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{cin:.3f}", "-i", a["path"], "-t", f"{dur:.3f}",
                        "-vf", f"fps={fps}", "-an", str(d / "%05d.png")], check=True)
        fs = sorted(str(p) for p in d.glob("*.png"))
        if not fs:
            raise CutError(f"clip {a['id']} produced no frames at clip_in {cin}")
        out[a["id"]] = fs
    return out


def render(manifest, edl, out, audio=None, scale=1.0, crf=18, workers=None, thumbnail=None):
    W, H = BF.parse_aspect(manifest["aspect"])
    W, H = int(round(W * scale / 2)) * 2, int(round(H * scale / 2)) * 2
    br = BF.Brand.from_spec(manifest, (W, H))
    fps = edl["fps"]
    nf = edl["frames"]
    tmp = tempfile.mkdtemp(prefix="strobe_")
    try:
        _S.clear()
        _S.update(brand=br, assets={a["id"]: a for a in manifest["assets"]}, rows=edl["shots"],
                  starts=np.array([r["frame"] for r in edl["shots"]]), cache={},
                  grain=float(manifest.get("grain", 11)) * (1 if scale >= 0.99 else max(0.4, scale)),
                  halftone=float(manifest.get("halftone", 0.10)), label=manifest.get("label", BF.LABEL_TEXT),
                  label_tail=int(round(fps)) if edl["ending"] == "loop" and manifest.get("label_on_loop", True) else 0,
                  nf=nf)
        _S["clipframes"] = _extract_clips(manifest, edl["shots"], fps, tmp, (W, H))
        fps_s = str(int(fps)) if float(fps).is_integer() else str(fps)
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", fps_s, "-i", "-"]
        if audio:
            cmd += ["-i", str(audio)]
        cmd += ["-t", f"{nf / fps:.4f}", "-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-colorspace", "bt709",
                "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
        cmd += (["-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if audio else ["-an"]) + ["-movflags", "+faststart", str(out)]
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        workers = workers or min(8, os.cpu_count() or 2)
        if workers > 1:
            with Pool(workers) as pool:
                for b in pool.imap(_work, range(nf), chunksize=6):
                    p.stdin.write(b)
        else:
            for f in range(nf):
                p.stdin.write(_work(f))
        p.stdin.close()
        if p.wait() != 0:
            raise CutError("ffmpeg failed")
        if thumbnail:
            render_frame(0).convert("RGB").save(thumbnail, quality=92)
        return nf
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fit_size(master, out, max_mb, audio_kbps=256):
    """Re-encode master to stay under max_mb (e.g. 95 for X's ~100 MB cap), BT.709 tags kept, audio re-encoded
    at audio_kbps. Lowers the bitrate 8% per retry until it fits."""
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(master)],
                               capture_output=True, text=True, check=True).stdout.strip())
    kbps = max_mb * 8000 * 0.96 / dur - audio_kbps
    for _ in range(6):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(master), "-c:v", "libx264", "-preset", "slow",
                        "-vf", "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                        "-b:v", f"{int(kbps)}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{int(kbps * 2)}k",
                        "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                        "-color_range", "tv", "-c:a", "aac", "-b:a", f"{audio_kbps}k", "-movflags", "+faststart", str(out)],
                       check=True)
        mb = Path(out).stat().st_size / 1e6
        if mb <= max_mb:
            return round(mb, 1)
        kbps *= 0.92
    raise CutError(f"could not fit {out} under {max_mb} MB")


# ---------------------------------------------------------------- cli
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("manifest"); p.add_argument("info"); p.add_argument("edl")
    p.add_argument("--cap", type=int, default=4); p.add_argument("--min-hold", type=float, default=1.3)
    p.add_argument("--words-rate", type=float, default=0.1); p.add_argument("--balance-tol", type=float, default=0.15)
    p.add_argument("--ending", choices=("resolved", "loop")); p.add_argument("--seed", type=int, default=1)
    c = sub.add_parser("check")
    c.add_argument("manifest"); c.add_argument("info"); c.add_argument("edl")
    r = sub.add_parser("render")
    r.add_argument("manifest"); r.add_argument("edl"); r.add_argument("out")
    r.add_argument("--audio"); r.add_argument("--scale", type=float, default=1.0); r.add_argument("--crf", type=int, default=18)
    r.add_argument("--workers", type=int); r.add_argument("--thumbnail")
    r.add_argument("--max-mb", type=float, help="also write OUT under this size (master kept as OUT.master.mp4)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "plan":
            m, info = load_manifest(a.manifest), load_info(a.info)
            e = plan(m, info, a.cap, a.min_hold, a.words_rate, a.balance_tol, a.ending, a.seed)
            e["check"] = check(m, info, e)
            Path(a.edl).write_text(json.dumps(e, indent=1))
            print(json.dumps({"shots": e["stats"]["shots"], "cuts_per_wave": e["stats"]["cuts_per_wave"],
                              "max_uses": e["stats"]["max_uses"], "ok": e["check"]["ok"],
                              "problems": e["check"]["problems"], "warnings": e["warnings"]}))
            return 0 if e["check"]["ok"] else 2
        if a.cmd == "check":
            res = check(load_manifest(a.manifest), load_info(a.info), json.loads(Path(a.edl).read_text()))
            print(json.dumps(res, indent=1))
            return 0 if res["ok"] else 2
        target = Path(a.out)
        master = target.with_suffix(".master.mp4") if a.max_mb else target
        n = render(load_manifest(a.manifest), json.loads(Path(a.edl).read_text()), master, a.audio, a.scale, a.crf,
                   a.workers, a.thumbnail)
        res = {"frames": n, "out": str(target)}
        if a.max_mb:
            res["mb"] = fit_size(master, target, a.max_mb)
            res["master"] = str(master)
        print(json.dumps(res))
        return 0
    except (CutError, BF.BrandError, OSError, KeyError, subprocess.CalledProcessError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
