#!/usr/bin/env python3
"""Grok Bot Video Studio: ffmpeg assembly.

  vstudio_assemble.py EDL.json OUT.mp4

EDL.json:
{ "aspect": "9x16"|"16x9"|"1x1", "fps": 30, "crossfade": 0.5,
  "clips": [{"path": "...", "in": 0.0, "out": 6.0}, ...],      # clip audio is dropped
  "voice": "narration.wav" | null,                              # optional
  "music": "song.mp3" | null, "music_role": "bed"|"lead",       # bed = ducked under voice; lead = music video
  "music_offset": 0.0, "loudness": -16, "fade": 0.5 }
Clips are scaled and center-cropped to the aspect natively, joined with crossfades,
then audio is mixed (music ducked under voice by sidechain compression) and loudness-normalized.
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vstudio_media import ASPECTS, probe, run


def validate_edl(e):
    errs = []
    if e.get("aspect") not in ASPECTS: errs.append("aspect must be one of " + ", ".join(sorted(ASPECTS)))
    clips = e.get("clips") or []
    if not clips: errs.append("clips must be non-empty")
    xf = float(e.get("crossfade", 0.5))
    for i, c in enumerate(clips):
        if not Path(c.get("path", "")).is_file(): errs.append(f"clip {i}: file missing"); continue
        dur = probe(c["path"])["duration"]
        a, b = float(c.get("in", 0)), float(c.get("out", dur))
        if not (0 <= a < b <= dur + 0.05): errs.append(f"clip {i}: in/out {a}-{b} outside 0-{dur:.2f}")
        elif len(clips) > 1 and b - a <= xf: errs.append(f"clip {i}: shorter than crossfade {xf}")
    for k in ("voice", "music"):
        if e.get(k) and not Path(e[k]).is_file(): errs.append(f"{k} file missing")
    if e.get("music_role", "bed") not in ("bed", "lead"): errs.append("music_role must be bed or lead")
    return errs


def build(e, out):
    errs = validate_edl(e)
    if errs: raise ValueError("; ".join(errs))
    w, h = ASPECTS[e["aspect"]]; fps = int(e.get("fps", 30)); xf = float(e.get("crossfade", 0.5))
    lufs = float(e.get("loudness", -16)); fade = float(e.get("fade", 0.5))
    args, fl = ["ffmpeg", "-y", "-v", "error"], []
    durs = []
    for i, c in enumerate(e["clips"]):
        dur = probe(c["path"])["duration"]; a = float(c.get("in", 0)); b = float(c.get("out", dur)); durs.append(b - a)
        args += ["-i", c["path"]]
        fl.append(f"[{i}:v]trim={a}:{b},setpts=PTS-STARTPTS,fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,"
                  f"crop={w}:{h},setsar=1,format=yuv420p[v{i}]")
    last = "v0"
    for i in range(1, len(durs)):
        offset = sum(durs[:i]) - xf * i
        fl.append(f"[{last}][v{i}]xfade=transition=fade:duration={xf}:offset={offset:.3f}[x{i}]"); last = f"x{i}"
    total = sum(durs) - xf * (len(durs) - 1)
    fl.append(f"[{last}]fade=t=in:d={fade},fade=t=out:st={max(total - fade, 0):.3f}:d={fade}[vout]")
    n = len(durs); aud = []
    if e.get("voice"):
        args += ["-i", e["voice"]]; vi = n; n += 1
        fl.append(f"[{vi}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{total:.3f}[voice]"); aud.append("voice")
    if e.get("music"):
        args += ["-ss", str(float(e.get("music_offset", 0))), "-i", e["music"]]; mi = n; n += 1
        fl.append(f"[{mi}:a]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{total:.3f},"
                  f"afade=t=out:st={max(total - 1.5, 0):.3f}:d=1.5[music]")
        if "voice" in aud and e.get("music_role", "bed") == "bed":
            fl.append("[voice]asplit=2[voicemix][voicekey]")
            fl.append("[music]volume=0.35[mq];[mq][voicekey]sidechaincompress=threshold=0.02:ratio=12:attack=20:release=400[ducked]")
            fl.append("[voicemix][ducked]amix=inputs=2:duration=first:normalize=0[mix]")
        elif "voice" in aud:
            fl.append("[voice][music]amix=inputs=2:duration=first:normalize=0[mix]")
        else:
            fl.append("[music]anull[mix]")
    elif aud:
        fl.append("[voice]anull[mix]")
    else:
        args += ["-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        fl.append(f"[{n}:a]anull[mix]"); n += 1
    if e.get("voice") or e.get("music"):
        fl.append(f"[mix]loudnorm=I={lufs}:TP=-1.5:LRA=11,aresample=48000,atrim=0:{total:.3f}[aout]")
    else:
        fl.append("[mix]atrim=0:%.3f[aout]" % total)
    args += ["-filter_complex", ";".join(fl), "-map", "[vout]", "-map", "[aout]", "-r", str(fps),
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.3f}", str(out)]
    run(args)
    return {"out": str(out), "duration": round(total, 3)}


def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("edl"); ap.add_argument("out"); a = ap.parse_args(argv)
    try:
        print(json.dumps(build(json.loads(Path(a.edl).read_text()), a.out)))
    except ValueError as ex:
        print(f"error: {ex}", file=sys.stderr); return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
