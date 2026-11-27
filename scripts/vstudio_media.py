#!/usr/bin/env python3
"""Shared ffmpeg/ffprobe helpers, plus the single-clip normalize and loudness commands.

  vstudio_media.py normalize IN.mp4 OUT.mp4 --aspect 9x16
  vstudio_media.py loudness IN.mp4 OUT.mp4 [--lufs -14] [--tp -1.5] [--bed] [--bed-db -34]

normalize keeps the first real video stream (skips an attached cover image) and the first
audio stream, scales and center-crops to the aspect, and writes 48 kHz stereo AAC.
loudness runs two-pass linear loudnorm on that clip. --bed mixes a quiet noise bed so sparse
sound effects are not silence. Leave --bed off for a clip that is supposed to contain silence,
then pass --allow-silence to vstudio_qa_tech.py.

Single-clip order: normalize, then loudness, then captions, then QA.
"""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path

import vstudio_audio_finish as AF

ASPECTS = {"16x9": (1920, 1080), "9x16": (1080, 1920), "1x1": (1080, 1080)}
SAFE_BOTTOM = {"16x9": 0.08, "9x16": 0.22, "1x1": 0.10}  # caption margin as fraction of height (clears platform UI)


def run(cmd, strict_stderr=False):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0 or (strict_stderr and p.stderr.strip()):
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr[-2000:]}")
    return p


def ffmpeg_filter_path(path):
    """Escape a path for a single-quoted ffmpeg filter argument."""
    return str(Path(path).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _attached_pic(stream):
    return bool((stream.get("disposition") or {}).get("attached_pic"))


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate,disposition",
               "-of", "json", str(path)]).stdout
    j = json.loads(out)
    info = {"duration": float(j["format"]["duration"]), "video": None, "audio": None}
    for s in j.get("streams", []):
        if s["codec_type"] == "video" and info["video"] is None and not _attached_pic(s):
            num, den = s["r_frame_rate"].split("/")
            info["video"] = {"width": s["width"], "height": s["height"], "fps": float(num) / float(den)}
        elif s["codec_type"] == "audio" and info["audio"] is None:
            info["audio"] = {"sample_rate": int(s["sample_rate"])}
    return info


def normalize(src, dst, aspect):
    """Map the main video and first audio, scale/crop to the aspect, 48 kHz stereo AAC."""
    if aspect not in ASPECTS:
        raise ValueError("aspect must be one of " + ", ".join(sorted(ASPECTS)))
    info = probe(src)
    if not info["video"]:
        raise RuntimeError(f"no video stream in {src}")
    if not info["audio"]:
        raise RuntimeError(f"no audio stream in {src}")
    w, h = ASPECTS[aspect]
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"
    run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error", "-i", str(src),
         "-map", "0:V:0", "-map", "0:a:0", "-vf", vf,
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k",
         "-movflags", "+faststart", str(dst)])
    return dst


def loudness(src, dst, lufs=-14.0, tp=-1.5, bed=False, bed_db=-34.0):
    """Two-pass linear loudnorm for one clip, reusing vstudio_audio_finish.normalize.

    Video is copied. Audio is 48 kHz stereo AAC. --bed mixes brown noise at bed_db (dBFS,
    before loudnorm) so gaps in sparse sound effects stay above the silence gate.
    """
    info = probe(src)
    if not info["video"]:
        raise RuntimeError(f"no video stream in {src}")
    if not info["audio"]:
        raise RuntimeError(f"no audio stream in {src}")
    dur = info["duration"]
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        raw = td / "raw.wav"
        run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error", "-i", str(src), "-vn",
             "-af", f"apad=whole_dur={dur:.3f},atrim=0:{dur:.3f}",
             "-ac", "2", "-ar", str(AF.SR), "-c:a", "pcm_s24le", str(raw)])
        src_wav = raw
        if bed:
            amp = 10 ** (bed_db / 20.0)
            mixed = td / "bed.wav"
            run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error", "-i", str(raw),
                 "-f", "lavfi", "-i",
                 f"anoisesrc=color=brown:sample_rate={AF.SR}:amplitude={amp:.6f}:duration={dur:.3f}:seed=1",
                 "-filter_complex",
                 "[1:a]aformat=channel_layouts=stereo[bed];"
                 "[0:a][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0",
                 "-ar", str(AF.SR), "-c:a", "pcm_s24le", str(mixed)])
            src_wav = mixed
        norm = td / "norm.wav"
        AF.normalize(str(src_wav), str(norm), lufs, tp)
        run(["ffmpeg", "-y", "-hide_banner", "-nostats", "-v", "error", "-i", str(src), "-i", str(norm),
             "-map", "0:V:0", "-map", "1:a:0", "-c:v", "copy",
             "-c:a", "aac", "-b:a", "192k", "-ar", str(AF.SR), "-ac", "2",
             "-movflags", "+faststart", str(dst)])
    measured = AF.loudness(dst, lufs, tp)
    out_info = probe(dst)
    return {"lufs": float(measured["input_i"]), "true_peak": float(measured["input_tp"]),
            "sample_rate": out_info["audio"]["sample_rate"], "bed": bool(bed)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Normalize a clip to an aspect, or set its loudness.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("normalize", help="scale and center-crop the main video and audio to an aspect")
    n.add_argument("inp"); n.add_argument("out"); n.add_argument("--aspect", choices=sorted(ASPECTS), required=True)
    l = sub.add_parser("loudness", help="two-pass loudnorm for one clip; --bed fills sparse gaps")
    l.add_argument("inp"); l.add_argument("out")
    l.add_argument("--lufs", type=float, default=-14.0)
    l.add_argument("--tp", type=float, default=-1.5)
    l.add_argument("--bed", action="store_true", help="mix a quiet brown-noise bed under the clip audio")
    l.add_argument("--bed-db", type=float, default=-34.0, help="bed level in dBFS before loudnorm (default -34)")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "normalize":
            normalize(a.inp, a.out, a.aspect)
            info = probe(a.out)
            print(json.dumps({"out": str(a.out), "width": info["video"]["width"], "height": info["video"]["height"],
                              "sample_rate": info["audio"]["sample_rate"], "duration": round(info["duration"], 3)}))
        elif a.cmd == "loudness":
            print(json.dumps(loudness(a.inp, a.out, a.lufs, a.tp, a.bed, a.bed_db)))
        else:
            raise AssertionError(f"unhandled command {a.cmd}")
    except (RuntimeError, ValueError, AF.AudioError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
