#!/usr/bin/env python3
"""Grok Bot Video Studio: technical QA gate (Q3) and format check.

  vstudio_qa_tech.py VIDEO.mp4 --aspect 9x16 [--expect-duration S] [--tolerance 0.5]
                     [--lufs -16] [--lufs-tol 1.5] [--max-true-peak -1.0] [--allow-silence]

Prints JSON {ok, checks:{...}, problems:[...]}; exit 0 on pass, 1 on fail.
"""
import argparse, json, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vstudio_media import ASPECTS, probe


def _filter_log(path, vf=None, af=None):
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path)]
    if vf: cmd += ["-vf", vf]
    if af: cmd += ["-af", af]
    if not vf: cmd += ["-vn"]
    if not af: cmd += ["-an"]
    cmd += ["-f", "null", "-"]
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def black_intervals(path, min_d=0.5):
    log = _filter_log(path, vf=f"blackdetect=d={min_d}:pix_th=0.10")
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]


def freeze_intervals(path, min_d=2.0):
    log = _filter_log(path, vf=f"freezedetect=n=0.003:d={min_d}")
    starts = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", log)]
    return list(zip(starts, ends + [None] * (len(starts) - len(ends))))


def silence_intervals(path, min_d=1.5, noise=-50):
    log = _filter_log(path, af=f"silencedetect=n={noise}dB:d={min_d}")
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    return list(zip(starts, ends + [None] * (len(starts) - len(ends))))


def loudness(path):
    log = _filter_log(path, af="ebur128=peak=true")
    summ = log[log.rfind("Summary:"):]
    i = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summ); tp = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", summ)
    f = lambda m: float("-inf") if m is None or m.group(1) == "-inf" else float(m.group(1))
    return {"integrated_lufs": f(i), "true_peak_dbfs": f(tp)}


def qa(path, aspect, expect=None, tol=0.5, lufs=-16.0, lufs_tol=1.5, max_tp=-1.0, allow_silence=False):
    info = probe(path); probs = []; checks = {"probe": info}
    w, h = ASPECTS[aspect]
    if not info["video"]: probs.append("no video stream")
    elif (info["video"]["width"], info["video"]["height"]) != (w, h): probs.append(f"size {info['video']['width']}x{info['video']['height']} != {w}x{h}")
    if not info["audio"]: probs.append("no audio stream")
    if expect is not None and abs(info["duration"] - expect) > tol: probs.append(f"duration {info['duration']:.2f}s vs expected {expect:.2f}s")
    b = [iv for iv in black_intervals(path) if iv[0] > 1.0 and iv[1] < info["duration"] - 1.0]
    checks["black"] = b
    if b: probs.append(f"black frames mid-video at {b}")
    fr = freeze_intervals(path); checks["freeze"] = fr
    if fr: probs.append(f"frozen video at {fr}")
    if info["audio"]:
        si = silence_intervals(path); checks["silence"] = si
        if si and not allow_silence: probs.append(f"silence at {si}")
        ld = loudness(path); checks["loudness"] = ld
        if not allow_silence:
            if abs(ld["integrated_lufs"] - lufs) > lufs_tol: probs.append(f"loudness {ld['integrated_lufs']} LUFS vs target {lufs}±{lufs_tol}")
        if ld["true_peak_dbfs"] > max_tp + 0.5: probs.append(f"true peak {ld['true_peak_dbfs']} dBFS > {max_tp}")
    return {"ok": not probs, "checks": checks, "problems": probs}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("video"); ap.add_argument("--aspect", choices=sorted(ASPECTS), required=True)
    ap.add_argument("--expect-duration", type=float); ap.add_argument("--tolerance", type=float, default=0.5)
    ap.add_argument("--lufs", type=float, default=-16.0); ap.add_argument("--lufs-tol", type=float, default=1.5)
    ap.add_argument("--max-true-peak", type=float, default=-1.0); ap.add_argument("--allow-silence", action="store_true")
    a = ap.parse_args(argv)
    r = qa(a.video, a.aspect, a.expect_duration, a.tolerance, a.lufs, a.lufs_tol, a.max_true_peak, a.allow_silence)
    print(json.dumps(r, indent=2, default=str)); return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
