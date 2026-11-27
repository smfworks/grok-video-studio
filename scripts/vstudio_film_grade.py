#!/usr/bin/env python3
"""Film-look grade for Grok Bot Video Studio (light / medium / strong).

Print an ffmpeg -filter_complex string (input [0:v] -> output [v]):
  vstudio_film_grade.py medium
  vstudio_film_grade.py filter medium --size 1920x1080 --fps 24 --yoff 138 --no-letterbox

Apply the grade to a file, copying audio untouched:
  vstudio_film_grade.py apply in.mp4 out.mp4 --preset medium [--yoff N] [--size WxH] [--fps N]
                                           [--letterbox|--no-letterbox]

Side-by-side still (ungraded | light | medium | strong) at a frame time, for review:
  vstudio_film_grade.py compare in.mp4 out.jpg --time 4.0 [--yoff N] [--size WxH] [--fps N]
                                              [--letterbox|--no-letterbox]

Letterbox head-room check: full frame with crop lines, plus top and bottom strips
so heads are not cut by the 2.39:1 window:
  vstudio_film_grade.py headroom in.mp4 outdir --yoff 20 --time 4.0 [--size WxH]

Options:
  --preset light|medium|strong  Grade strength (apply defaults to medium)
  --size WxH                    Output size (default 1920x1080). 2.39:1 letterbox
                                defaults on for landscape and off for vertical or
                                square (bars on a tall frame usually make no sense);
                                pass --letterbox to force it.
  --fps N                       Output frame rate (default 24)
  --letterbox / --no-letterbox  Force 2.39:1 letterbox on or off
  --yoff N                      Top of the 2.39:1 window in the output frame
                                (default: centred). Clamped to 0..(height-active).
  --time SEC                    Frame time for compare and headroom (default 0)

Uses only the Python standard library and ffmpeg. Medium at 1920x1080, 24 fps,
with default letterbox, is filter-string identical to the original grade script.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PRESETS = {
    #        sat   warm  blur  halo  grain  vig        weave  lift   roll
    "light":  (0.90, 0.6, 0.35, 0.22,  0.10, "PI/9",   0.6,  0.025, 0.95),
    "medium": (0.82, 1.0, 0.55, 0.35,  0.16, "PI/7.5",   1.1,  0.040, 0.92),
    "strong": (0.74, 1.4, 0.85, 0.50,  0.24, "PI/6", 1.8,  0.060, 0.89),
}
GAMMA = {"light": 1.05, "medium": 1.07, "strong": 1.09}
DEFAULT_SIZE = (1920, 1080)
DEFAULT_FPS = 24
ASPECT_239 = 2.39
COMMANDS = ("filter", "apply", "compare", "headroom")


class GradeError(ValueError):
    pass


def parse_size(spec):
    """Parse 'WxH' into even positive ints."""
    try:
        w_s, h_s = str(spec).lower().replace(" ", "").split("x")
        w, h = int(w_s), int(h_s)
    except (ValueError, AttributeError) as e:
        raise GradeError(f"size must be WxH (got {spec!r})") from e
    if w <= 0 or h <= 0:
        raise GradeError(f"size must be positive (got {w}x{h})")
    if w % 2 or h % 2:
        raise GradeError(f"size must be even for yuv420p (got {w}x{h})")
    return w, h


def fps_token(fps):
    """Format fps so 24 stays '24', not '24.0' (keeps the original filter string)."""
    f = float(fps)
    if f <= 0:
        raise GradeError(f"fps must be positive (got {fps})")
    if f == int(f):
        return str(int(f))
    return str(f)


def letterbox_default(width, height):
    """True for landscape frames; False for vertical or square."""
    return width > height


def letterbox_geometry(width, height, yoff=None):
    """Return (active_height, centred_bar, y) for a 2.39:1 window in a WxH frame.

    active_height is even. y is the top of the window, defaulting to the centred
    bar and clamped to 0..(height-active_height). For 1920x1080 this is
    active=804, bar=138, matching the original script.
    """
    ah = round(width / ASPECT_239 / 2) * 2
    ah = min(ah, height)
    if ah % 2:
        ah -= 1
    if ah < 2:
        raise GradeError(f"frame {width}x{height} is too small for a 2.39:1 window")
    bar = (height - ah) // 2
    y = bar if yoff is None else max(0, min(height - ah, int(yoff)))
    return ah, bar, y


def build(preset, letterbox=True, yoff=None, size=DEFAULT_SIZE, fps=DEFAULT_FPS,
          src="0:v", dst="v"):
    """Return an ffmpeg -filter_complex string: input [src] -> output [dst].

    Default arguments (medium, letterbox on, centred yoff, 1920x1080, 24 fps)
    produce the same string as the original film_grade.py.
    """
    if preset not in PRESETS:
        raise GradeError(f"preset must be light, medium or strong (got {preset!r})")
    sat, warm, blur, halo, grain, vig, weave, lift, roll = PRESETS[preset]
    W, H = size
    pad = int(round(weave * 2)) + 4
    fp = fps_token(fps)
    # tone: lifted blacks, soft highlight shoulder (roll-off), slight mid warmth
    curves = (f"curves=all='0/{lift} 0.25/{0.22 + lift * 0.6:.3f} 0.5/0.49 "
              f"0.75/{0.74 - 0.01 * warm:.3f} 0.9/{0.86 - 0.01 * warm:.3f} 1/{roll}'")
    cb = (f"colorbalance=rs={0.015 * warm:.3f}:gs={0.004 * warm:.3f}:bs={-0.03 * warm:.3f}:"
          f"rm={0.025 * warm:.3f}:gm={0.005 * warm:.3f}:bm={-0.025 * warm:.3f}:"
          f"rh={0.02 * warm:.3f}:gh={0.008 * warm:.3f}:bh={-0.03 * warm:.3f}")
    base = (f"[{src}]fps={fp},scale={W + 2 * pad}:{H + 2 * pad}:flags=lanczos,setsar=1,"
            # gate weave: slow pseudo-random sub-pixel-ish drift via crop offsets
            f"crop={W}:{H}:x='{pad}+{weave}*sin(t*2.3)+{weave * 0.5:.2f}*sin(t*7.7+1.3)':"
            f"y='{pad}+{weave * 0.8:.2f}*sin(t*1.7+0.5)+{weave * 0.4:.2f}*sin(t*9.1)',"
            f"gblur=sigma={blur},format=gbrp,{curves},{cb},"
            f"eq=saturation={sat}:contrast=0.98:gamma={GAMMA[preset]},format=gbrp,split[b0][b1];")
    halation = (f"[b1]curves=all='0/0 0.72/0 1/1',gblur=sigma=22,"
                f"colorchannelmixer=rr=1:gg=0.38:bb=0.12[h];"
                f"[b0][h]blend=all_mode=screen:all_opacity={halo}[hb];")
    grainl = (f"color=c=0x808080:s={W}x{H}:r={fp},format=gbrp,noise=alls=60:allf=t,"
              f"gblur=sigma=0.9,eq=saturation=0.15[g];"
              f"[hb][g]blend=all_mode=overlay:all_opacity={grain}:shortest=1,")
    tail = f"vignette=a={vig},format=yuv420p"
    if letterbox:
        ah, bar, y = letterbox_geometry(W, H, yoff)
        tail += f",crop={W}:{ah}:0:{y},pad={W}:{H}:0:{bar}:black"
    return base + halation + grainl + tail + f"[{dst}]"


def resolve_letterbox(flag, width, height):
    """flag is True/False to force, or None to use the landscape-only default."""
    return letterbox_default(width, height) if flag is None else bool(flag)


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "").strip()
        raise GradeError(f"ffmpeg failed ({p.returncode}): {err[-2000:]}")
    return p


def _has_audio(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return p.returncode == 0 and bool(p.stdout.strip())


def apply_grade(src, dst, preset, size=DEFAULT_SIZE, fps=DEFAULT_FPS,
                letterbox=True, yoff=None):
    """Grade src into dst. Video is re-encoded; audio is stream-copied."""
    fc = build(preset, letterbox, yoff, size, fps)
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src),
           "-filter_complex", fc, "-map", "[v]"]
    if _has_audio(src):
        cmd += ["-map", "0:a", "-c:a", "copy"]
    else:
        cmd += ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "14",
            "-pix_fmt", "yuv420p",
            "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-movflags", "+faststart", str(dst)]
    _run(cmd)


def _frame_cmd(src, time_s, extra, dst):
    return ["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", str(time_s),
            "-i", str(src), *extra, "-frames:v", "1", str(dst)]


def compare_stills(src, dst, time_s=0.0, size=DEFAULT_SIZE, fps=DEFAULT_FPS,
                   letterbox=True, yoff=None):
    """Write a side-by-side still: ungraded | light | medium | strong."""
    W, H = size
    # Keep the contact sheet modest so it is easy to open and attach.
    pw = 640
    ph = max(2, round(pw * H / W / 2) * 2)
    labels = [("ORIGINAL", None), ("LIGHT", "light"), ("MEDIUM", "medium"), ("STRONG", "strong")]
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        parts = []
        for name, preset in labels:
            out = td / f"{name.lower()}.png"
            if preset is None:
                vf = f"scale={W}:{H}:flags=lanczos,setsar=1,format=yuv420p"
                _run(_frame_cmd(src, time_s, ["-vf", vf], out))
            else:
                fc = build(preset, letterbox, yoff, size, fps)
                _run(_frame_cmd(src, time_s, ["-filter_complex", fc, "-map", "[v]"], out))
            parts.append(out)
        # Label each panel, then hstack. Fall back to an unlabeled row if drawtext fails.
        labeled = []
        font = "Sans"
        for (name, _), p in zip(labels, parts):
            lp = td / f"lbl_{p.name}"
            dt = (f"drawtext=expansion=none:text='{name}':font={font}:fontsize=28:"
                  f"fontcolor=white:box=1:boxcolor=black@0.55:x=12:y=12")
            try:
                _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(p),
                      "-vf", f"scale={pw}:{ph}:flags=lanczos,{dt}", str(lp)])
            except GradeError:
                _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(p),
                      "-vf", f"scale={pw}:{ph}:flags=lanczos", str(lp)])
            labeled.append(lp)
        ins = []
        for i, p in enumerate(labeled):
            ins += ["-i", str(p)]
        filt = "".join(f"[{i}:v]" for i in range(4)) + "hstack=4[v]"
        _run(["ffmpeg", "-nostdin", "-y", "-v", "error", *ins,
              "-filter_complex", filt, "-map", "[v]", "-q:v", "3", str(dst)])


def headroom_stills(src, outdir, yoff, time_s=0.0, size=DEFAULT_SIZE):
    """Export a marked full frame plus top and bottom letterbox-edge strips."""
    W, H = size
    ah, bar, y = letterbox_geometry(W, H, yoff)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    full = outdir / "headroom_full.png"
    # Scale to the working size, then draw the 2.39:1 window edges.
    vf = (f"scale={W}:{H}:flags=lanczos,setsar=1,"
          f"drawbox=x=0:y={y}:w={W}:h=2:color=red:t=fill,"
          f"drawbox=x=0:y={y + ah - 2}:w={W}:h=2:color=red:t=fill")
    _run(_frame_cmd(src, time_s, ["-vf", vf], full))
    # ~200 px strips centred on each crop line, clamped to the frame.
    strip = 200
    top_y0 = max(0, y - strip // 2)
    top_h = min(strip, H - top_y0)
    if top_h % 2:
        top_h -= 1
    bot_y = y + ah
    bot_y0 = max(0, bot_y - strip // 2)
    bot_h = min(strip, H - bot_y0)
    if bot_h % 2:
        bot_h -= 1
    top = outdir / "headroom_top.png"
    bot = outdir / "headroom_bottom.png"
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(full),
          "-vf", f"crop={W}:{top_h}:0:{top_y0}", str(top)])
    _run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(full),
          "-vf", f"crop={W}:{bot_h}:0:{bot_y0}", str(bot)])
    return {"active_height": ah, "bar": bar, "yoff": y, "window": (y, y + ah),
            "full": str(full), "top": str(top), "bottom": str(bot)}


def _add_shared(p):
    p.add_argument("--size", default="1920x1080", help="output WxH (default 1920x1080)")
    p.add_argument("--fps", type=float, default=DEFAULT_FPS, help="output fps (default 24)")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--letterbox", action="store_true", default=None,
                   help="force 2.39:1 letterbox on")
    g.add_argument("--no-letterbox", action="store_true",
                   help="force 2.39:1 letterbox off")
    p.add_argument("--yoff", type=int, default=None,
                   help="top of the 2.39:1 window (default: centred)")
    return p


def _letterbox_flag(args):
    if getattr(args, "no_letterbox", False):
        return False
    if getattr(args, "letterbox", None) is True:
        return True
    return None  # auto


def _parser():
    p = argparse.ArgumentParser(
        prog="vstudio_film_grade.py",
        description="Film-look grade (light/medium/strong) with optional 2.39:1 letterbox.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run with no arguments or -h for usage. Filter mode prints the filter string.",
    )
    sub = p.add_subparsers(dest="cmd")

    pf = sub.add_parser("filter", help="print an ffmpeg -filter_complex string")
    pf.add_argument("preset", choices=list(PRESETS), nargs="?", default="medium")
    _add_shared(pf)

    pa = sub.add_parser("apply", help="grade a file, copying audio untouched")
    pa.add_argument("src"); pa.add_argument("dst")
    pa.add_argument("--preset", choices=list(PRESETS), default="medium")
    _add_shared(pa)

    pc = sub.add_parser("compare", help="side-by-side still: ungraded vs light/medium/strong")
    pc.add_argument("src"); pc.add_argument("dst")
    pc.add_argument("--time", type=float, default=0.0)
    pc.add_argument("--preset", choices=list(PRESETS), default="medium",
                    help=argparse.SUPPRESS)  # accepted but unused; all three strengths are shown
    _add_shared(pc)

    ph = sub.add_parser("headroom", help="export top/bottom letterbox-edge stills")
    ph.add_argument("src"); ph.add_argument("outdir")
    ph.add_argument("--time", type=float, default=0.0)
    ph.add_argument("--yoff", type=int, default=None,
                    help="top of the 2.39:1 window (default: centred)")
    ph.add_argument("--size", default="1920x1080")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip(), file=sys.stderr if argv and argv[0] in ("-h", "--help") else sys.stdout)
        return 0
    # Bare preset (and optional flags) is filter mode, matching the original script.
    if argv[0] in PRESETS:
        argv = ["filter", *argv]
    try:
        args = _parser().parse_args(argv)
        size = parse_size(args.size) if hasattr(args, "size") else DEFAULT_SIZE
        fps = getattr(args, "fps", DEFAULT_FPS)
        yoff = getattr(args, "yoff", None)
        lb_flag = _letterbox_flag(args) if args.cmd in ("filter", "apply", "compare") else True
        letterbox = resolve_letterbox(lb_flag, *size) if args.cmd != "headroom" else True

        if args.cmd == "filter":
            print(build(args.preset, letterbox, yoff, size, fps))
            return 0
        if args.cmd == "apply":
            apply_grade(args.src, args.dst, args.preset, size, fps, letterbox, yoff)
            print(args.dst)
            return 0
        if args.cmd == "compare":
            compare_stills(args.src, args.dst, args.time, size, fps, letterbox, yoff)
            print(args.dst)
            return 0
        if args.cmd == "headroom":
            info = headroom_stills(args.src, args.outdir, yoff, getattr(args, "time", 0.0), size)
            y0, y1 = info["window"]
            print(f"{args.outdir}  window y={y0}..{y1} (active={info['active_height']}, "
                  f"centred bar={info['bar']}, yoff={info['yoff']})")
            return 0
        print(__doc__.strip())
        return 2
    except GradeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
