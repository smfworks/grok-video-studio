"""Shared ffmpeg/ffprobe helpers for Grok Bot Video Studio."""
import json, subprocess

ASPECTS = {"16x9": (1920, 1080), "9x16": (1080, 1920), "1x1": (1080, 1080)}
SAFE_BOTTOM = {"16x9": 0.08, "9x16": 0.22, "1x1": 0.10}  # caption margin as fraction of height (clears platform UI)


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"command failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr[-2000:]}")
    return p


def probe(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height,r_frame_rate,sample_rate",
               "-of", "json", str(path)]).stdout
    j = json.loads(out)
    info = {"duration": float(j["format"]["duration"]), "video": None, "audio": None}
    for s in j.get("streams", []):
        if s["codec_type"] == "video" and info["video"] is None:
            num, den = s["r_frame_rate"].split("/")
            info["video"] = {"width": s["width"], "height": s["height"], "fps": float(num) / float(den)}
        elif s["codec_type"] == "audio" and info["audio"] is None:
            info["audio"] = {"sample_rate": int(s["sample_rate"])}
    return info
