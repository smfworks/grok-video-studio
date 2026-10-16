#!/usr/bin/env python3
"""Grok Bot Video Studio: captions.

  vstudio_captions.py srt WORDS.json OUT.srt [--max-chars 32] [--max-cps 20] [--max-dur 3.5]
  vstudio_captions.py burn IN.mp4 SUBS.srt OUT.mp4 --aspect 9x16 [--font "DejaVu Sans"] [--size N]
  vstudio_captions.py check SUBS.srt [--max-cps 20]
  vstudio_captions.py tag IN.mp4 OUT.mp4 --aspect 9x16 [--text "AI-generated"]

WORDS.json: [{"word": str, "start": s, "end": s}, ...] (faster-whisper word timestamps or TTS timestamps).
"""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vstudio_media import ASPECTS, SAFE_BOTTOM, run


def ts(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_ts(s):
    h, m, rest = s.split(":"); sec, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000


def group(words, max_chars=32, max_cps=20.0, max_dur=3.5):
    cues, cur = [], []
    def text(ws): return " ".join(w["word"].strip() for w in ws)
    for w in words:
        if not w["word"].strip():
            continue
        if w["end"] < w["start"]:
            raise ValueError(f"word {w['word']!r} ends before it starts")
        cand = cur + [w]
        if cur and (len(text(cand)) > max_chars or cand[-1]["end"] - cand[0]["start"] > max_dur
                    or cur[-1]["word"].strip()[-1:] in ".?!"):
            cues.append(cur); cur = [w]
        else:
            cur = cand
    if cur:
        cues.append(cur)
    out = []
    for i, c in enumerate(cues):
        start, end, t = c[0]["start"], c[-1]["end"], text(c)
        need = len(t) / max_cps
        nxt = cues[i + 1][0]["start"] if i + 1 < len(cues) else end + need
        end = max(end, min(start + need, nxt))  # extend for readability without overlapping
        out.append({"start": start, "end": end, "text": t})
    return out


def to_srt(cues):
    return "".join(f"{i}\n{ts(c['start'])} --> {ts(c['end'])}\n{c['text']}\n\n" for i, c in enumerate(cues, 1))


def read_srt(path):
    cues = []
    for block in Path(path).read_text().strip().split("\n\n"):
        lines = block.strip().splitlines()
        a, b = lines[1].split(" --> ")
        cues.append({"start": parse_ts(a), "end": parse_ts(b), "text": " ".join(lines[2:])})
    return cues


def check(cues, max_cps=20.0):
    problems = []
    for i, c in enumerate(cues, 1):
        d = c["end"] - c["start"]
        if d <= 0: problems.append(f"cue {i}: non-positive duration"); continue
        if len(c["text"]) / d > max_cps + 1e-6: problems.append(f"cue {i}: {len(c['text'])/d:.1f} cps > {max_cps}")
        if i > 1 and c["start"] < cues[i - 2]["end"] - 1e-6: problems.append(f"cue {i}: overlaps previous")
    return problems


def burn(inp, srt, out, aspect, font="DejaVu Sans", size=None):
    w, h = ASPECTS[aspect]
    size = size or int(h * (0.032 if aspect == "16x9" else 0.026))
    margin = int(h * SAFE_BOTTOM[aspect])
    style = (f"FontName={font},FontSize={size},PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
             f"BorderStyle=1,Outline=3,Shadow=0,Alignment=2,MarginV={margin}")
    srt_esc = str(Path(srt).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(inp), "-vf",
         f"subtitles='{srt_esc}':original_size={w}x{h}:force_style='{style}'",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)])
    return out


def find_font():
    out = run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"]).stdout.strip()
    if not out or not Path(out).is_file():
        raise RuntimeError("no font found via fc-match")
    return out


def tag(inp, out, text="AI-generated", aspect="9x16"):
    """Burn a small disclosure tag in the top-right corner for the whole video."""
    w, h = ASPECTS[aspect]
    size = max(18, int(h * 0.022)); pad = int(h * 0.03)
    t = text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = (f"drawtext=fontfile='{find_font()}':text='{t}':fontsize={size}:fontcolor=white@0.9:"
          f"box=1:boxcolor=black@0.45:boxborderw={size // 3}:x=w-tw-{pad}:y={pad}")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(inp), "-vf", vf, "-c:v", "libx264", "-preset", "medium",
         "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "copy", str(out)])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("srt"); s.add_argument("words"); s.add_argument("out")
    s.add_argument("--max-chars", type=int, default=32); s.add_argument("--max-cps", type=float, default=20.0); s.add_argument("--max-dur", type=float, default=3.5)
    b = sub.add_parser("burn"); b.add_argument("inp"); b.add_argument("srt"); b.add_argument("out")
    b.add_argument("--aspect", choices=sorted(ASPECTS), required=True); b.add_argument("--font", default="DejaVu Sans"); b.add_argument("--size", type=int)
    t = sub.add_parser("tag"); t.add_argument("inp"); t.add_argument("out"); t.add_argument("--text", default="AI-generated")
    t.add_argument("--aspect", choices=sorted(ASPECTS), required=True)
    c = sub.add_parser("check"); c.add_argument("srt"); c.add_argument("--max-cps", type=float, default=20.0)
    a = ap.parse_args(argv)
    if a.cmd == "srt":
        cues = group(json.loads(Path(a.words).read_text()), a.max_chars, a.max_cps, a.max_dur)
        Path(a.out).write_text(to_srt(cues)); print(json.dumps({"cues": len(cues)}))
    elif a.cmd == "tag":
        tag(a.inp, a.out, a.text, a.aspect); print(a.out)
    elif a.cmd == "burn":
        burn(a.inp, a.srt, a.out, a.aspect, a.font, a.size); print(a.out)
    else:
        p = check(read_srt(a.srt), a.max_cps); print(json.dumps({"ok": not p, "problems": p}, indent=2))
        return 0 if not p else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
