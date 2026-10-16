#!/usr/bin/env python3
"""Grok Bot Video Studio: beat grid and shot slots for music videos.

  vstudio_beats.py SONG.(mp3|wav) OUT.json [--target-shot 8.0] [--min-shot 3.0]

Writes {"duration","tempo","beats":[s...],"slots":[{"index","start","end"}...]}: shots cut on beats,
each close to --target-shot seconds, covering the whole song with no gaps.
"""
import argparse, json, sys
from pathlib import Path


def beat_grid(path):
    import librosa, numpy as np
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    dur = float(len(y) / sr)
    tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
    beats = [float(t) for t in librosa.frames_to_time(frames, sr=sr)]
    return dur, float(np.atleast_1d(tempo)[0]), beats


def slots(duration, beats, target=8.0, min_shot=3.0):
    if duration <= 0: raise ValueError("duration must be positive")
    cuts, last = [0.0], 0.0
    cands = [b for b in beats if 0 < b < duration]
    while duration - last > target + min_shot:
        want = last + target
        pool = [b for b in cands if b - last >= min_shot and duration - b >= min_shot]
        nxt = min(pool, key=lambda b: abs(b - want)) if pool else want
        if nxt <= last: nxt = want
        cuts.append(round(nxt, 3)); last = nxt
    cuts.append(round(duration, 3))
    return [{"index": i, "start": cuts[i], "end": cuts[i + 1]} for i in range(len(cuts) - 1)]


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("song"); ap.add_argument("out")
    ap.add_argument("--target-shot", type=float, default=8.0); ap.add_argument("--min-shot", type=float, default=3.0)
    a = ap.parse_args(argv)
    dur, tempo, beats = beat_grid(a.song)
    res = {"duration": round(dur, 3), "tempo": round(tempo, 2), "beats": [round(b, 3) for b in beats],
           "slots": slots(dur, beats, a.target_shot, a.min_shot)}
    Path(a.out).write_text(json.dumps(res, indent=2)); print(json.dumps({"shots": len(res["slots"]), "tempo": res["tempo"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
