#!/usr/bin/env python3
"""Grok Bot Video Studio: local speech-to-text (faster-whisper) and script word match (QA gate Q2).

  vstudio_transcribe.py words AUDIO OUT.json [--model small] [--language en]
  vstudio_transcribe.py match AUDIO --script SCRIPT.txt [--min 0.95] [--keyterms A,B] [--model small]

`words` writes [{"word","start","end"}] for vstudio_captions.py. `match` prints {ok, score, missing_keyterms}.
"""
import argparse, difflib, json, re, sys
from pathlib import Path

_NUM = {"0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten"}


def norm(text):
    toks = re.findall(r"[a-z0-9']+", text.lower().replace("-", " "))
    return [_NUM.get(t, t) for t in toks]


def word_match(script, heard):
    a, b = norm(script), norm(heard)
    if not a:
        raise ValueError("script is empty")
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    return round(sum(m.size for m in sm.get_matching_blocks()) / len(a), 4)


def missing_keyterms(keyterms, heard):
    h = " " + " ".join(norm(heard)) + " "
    return [k for k in keyterms if k.strip() and " " + " ".join(norm(k)) + " " not in h]


def transcribe(audio, model="small", language="en"):
    import subprocess, numpy as np
    from faster_whisper import WhisperModel
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    samples = np.frombuffer(pcm, dtype=np.float32)
    m = WhisperModel(model, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(samples, language=language, word_timestamps=True, vad_filter=True)
    words = []
    for s in segs:
        for w in s.words or []:
            words.append({"word": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)})
    return words


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("words"); w.add_argument("audio"); w.add_argument("out")
    m = sub.add_parser("match"); m.add_argument("audio"); m.add_argument("--script", required=True)
    m.add_argument("--min", type=float, default=0.95); m.add_argument("--keyterms", default="")
    for p in (w, m):
        p.add_argument("--model", default="small"); p.add_argument("--language", default="en")
    a = ap.parse_args(argv)
    words = transcribe(a.audio, a.model, a.language)
    if a.cmd == "words":
        Path(a.out).write_text(json.dumps(words, indent=1)); print(json.dumps({"words": len(words)})); return 0
    heard = " ".join(x["word"] for x in words)
    score = word_match(Path(a.script).read_text(), heard)
    miss = missing_keyterms([k for k in a.keyterms.split(",") if k], heard)
    ok = score >= a.min and not miss
    print(json.dumps({"ok": ok, "score": score, "missing_keyterms": miss, "heard": heard}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
