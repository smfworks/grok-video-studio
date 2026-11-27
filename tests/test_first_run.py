"""First-run fixes: caption burn, clip normalize, sparse-SFX loudness, and drawtext percent signs.

Flat (or lossless) sources are required for the caption test. A textured re-encode changes
pixels even when burn() draws nothing, which is why the v1.3.0 test stayed green.
"""
import io, json, shutil, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import vstudio_captions as C
import vstudio_media as M
import vstudio_qa_tech as Q
from vstudio_media import ASPECTS, SAFE_BOTTOM, probe


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def frame_rgb(path, t):
    info = probe(path)
    w, h = info["video"]["width"], info["video"]["height"]
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-ss", str(t), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True).stdout
    if len(raw) != w * h * 3:
        raise AssertionError(f"frame byte count {len(raw)} != {w * h * 3}")
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3).copy()


def ink_stats(arr, bg, tol=40):
    diff = np.max(np.abs(arr.astype(np.int16) - np.asarray(bg, dtype=np.int16)), axis=2)
    ink = diff > tol
    white = int(np.all(arr > 200, axis=2).sum())
    if not ink.any():
        return {"white": white, "bbox": None}
    ys, xs = np.where(ink)
    return {"white": white, "bbox": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))}


def stream_info(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,sample_rate,channels,sample_aspect_ratio:stream_disposition=attached_pic",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


SRT = "1\n00:00:00,300 --> 00:00:00,900\nHello\n\n2\n00:00:01,200 --> 00:00:01,900\nWorld\n"


class CaptionBurn(unittest.TestCase):
    def test_burn_captions(self):
        """Visible, correctly sized captions on 9:16, 16:9 and 1:1. Fails on the v1.3.0 burn."""
        for aspect, (w, h) in ASPECTS.items():
            with self.subTest(aspect=aspect):
                size = int(h * (0.032 if aspect == "16x9" else 0.026))
                margin = int(h * SAFE_BOTTOM[aspect])
                with tempfile.TemporaryDirectory() as d:
                    d = Path(d)
                    src, out, srt = d / "flat.mp4", d / "burned.mp4", d / "c.srt"
                    ff("-f", "lavfi", "-i", f"color=c=0x224466:s={w}x{h}:r=30:d=2.2",
                       "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=2.2",
                       "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                       "-c:a", "aac", str(src))
                    srt.write_text(SRT)
                    C.burn(src, srt, out, aspect)
                    self.assertAlmostEqual(probe(out)["duration"], probe(src)["duration"], delta=0.15)
                    blank = ink_stats(frame_rgb(out, 0.05), frame_rgb(src, 0.05)[0, 0])
                    self.assertEqual(blank["white"], 0)
                    self.assertIsNone(blank["bbox"])
                    for t in (0.60, 1.55):
                        st = ink_stats(frame_rgb(out, t), frame_rgb(src, t)[0, 0])
                        self.assertGreater(st["white"], 80, f"no caption ink at t={t}")
                        x0, y0, x1, y1 = st["bbox"]
                        height = y1 - y0 + 1
                        self.assertGreater(height, size * 0.45)
                        self.assertLess(height, size * 1.55)
                        self.assertLess(abs((h - 1 - y1) - margin), 24)
                        self.assertLess(abs((x0 + x1) / 2 - w / 2), w * 0.08)

    def test_font_comma_rejected(self):
        with self.assertRaises(ValueError):
            C.ass_document([{"start": 0, "end": 1, "text": "Hi"}], 1080, 1920, "Foo, Bar", 49, 422)


class ClipNormalize(unittest.TestCase):
    def test_1088x1920_cover_becomes_1080x1920(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ff("-f", "lavfi", "-i", "color=c=red:s=320x320", "-frames:v", "1", str(d / "cover.jpg"))
            ff("-f", "lavfi", "-i", "testsrc2=s=1088x1920:r=30:d=1.2", "-f", "lavfi", "-i", "sine=f=440:d=1.2",
               "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
               "-c:a", "aac", "-ar", "44100", "-ac", "2", str(d / "base.mp4"))
            # Cover last, as reported for Imagine clips, and cover first, which 0:v:0 would follow.
            ff("-i", str(d / "base.mp4"), "-i", str(d / "cover.jpg"), "-map", "0", "-map", "1",
               "-c", "copy", "-disposition:v:1", "attached_pic", str(d / "cover_last.mp4"))
            ff("-i", str(d / "cover.jpg"), "-i", str(d / "base.mp4"), "-map", "0:v", "-map", "1:v", "-map", "1:a",
               "-c", "copy", "-disposition:v:0", "attached_pic", str(d / "cover_first.mp4"))
            for name in ("cover_last.mp4", "cover_first.mp4"):
                with self.subTest(order=name):
                    src = d / name
                    self.assertEqual(probe(src)["video"]["width"], 1088)
                    self.assertEqual(probe(src)["video"]["height"], 1920)
                    out = d / f"norm_{name}"
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        rc = M.main(["normalize", str(src), str(out), "--aspect", "9x16"])
                    self.assertEqual(rc, 0, buf.getvalue())
                    reported = json.loads(buf.getvalue())
                    self.assertEqual((reported["width"], reported["height"]), (1080, 1920))
                    self.assertEqual(reported["sample_rate"], 48000)
                    streams = stream_info(out)
                    videos = [s for s in streams if s["codec_type"] == "video"]
                    audios = [s for s in streams if s["codec_type"] == "audio"]
                    self.assertEqual(len(videos), 1)
                    self.assertEqual(len(audios), 1)
                    self.assertEqual((videos[0]["width"], videos[0]["height"]), (1080, 1920))
                    self.assertEqual(videos[0].get("sample_aspect_ratio"), "1:1")
                    self.assertFalse(videos[0].get("disposition", {}).get("attached_pic"))
                    self.assertEqual(int(audios[0]["sample_rate"]), 48000)
                    self.assertEqual(audios[0]["channels"], 2)
                    self.assertAlmostEqual(probe(out)["duration"], probe(src)["duration"], delta=0.2)


class ClipLoudness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        # Two short beeps and a multi-second gap. One-pass loudnorm misses -14; silence QA fails.
        ff("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=4",
           "-f", "lavfi", "-i", "sine=frequency=880:duration=0.12:sample_rate=48000",
           "-f", "lavfi", "-i", "sine=frequency=660:duration=0.12:sample_rate=48000",
           "-filter_complex",
           "[1:a]adelay=200|200,apad=whole_dur=4,volume=-18dB[a];"
           "[2:a]adelay=2500|2500,apad=whole_dur=4,volume=-18dB[b];"
           "[a][b]amix=inputs=2:duration=first:normalize=0[aout]",
           "-map", "0:v", "-map", "[aout]", "-t", "4", "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-ar", "48000",
           str(cls.d / "sparse.mp4"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d)

    def _finish(self, name, *extra):
        out = self.d / name
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = M.main(["loudness", str(self.d / "sparse.mp4"), str(out), "--lufs", "-14", "--tp", "-1.5", *extra])
        self.assertEqual(rc, 0, buf.getvalue())
        return out, json.loads(buf.getvalue())

    def test_sparse_sfx_bed_passes_qa(self):
        out, reported = self._finish("bed.mp4", "--bed")
        self.assertTrue(reported["bed"])
        self.assertEqual(reported["sample_rate"], 48000)
        self.assertLess(abs(reported["lufs"] + 14), 1.0)
        self.assertLessEqual(reported["true_peak"], -1.5)
        q = Q.qa(out, "9x16", lufs=-14, max_tp=-1.5)
        self.assertTrue(q["ok"], q["problems"])
        self.assertLessEqual(q["checks"]["loudness"]["true_peak_dbfs"], -1.5)
        self.assertEqual(q["checks"]["probe"]["audio"]["sample_rate"], 48000)

    def test_intentional_silence_uses_allow_silence(self):
        out, reported = self._finish("nobed.mp4")
        self.assertFalse(reported["bed"])
        self.assertLess(abs(reported["lufs"] + 14), 1.0)
        self.assertLessEqual(reported["true_peak"], -1.5)
        q = Q.qa(out, "9x16", lufs=-14, max_tp=-1.5)
        self.assertTrue(any("silence" in p for p in q["problems"]))
        q2 = Q.qa(out, "9x16", lufs=-14, max_tp=-1.5, allow_silence=True)
        self.assertTrue(q2["ok"], q2["problems"])
        self.assertEqual(probe(out)["audio"]["sample_rate"], 48000)


class TagLiteral(unittest.TestCase):
    def test_percent_colon_and_quote(self):
        text = "100%: it's"
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            src, out = d / "flat.mp4", d / "tagged.mp4"
            ff("-f", "lavfi", "-i", "color=c=0x224466:s=1080x1080:r=30:d=1.0",
               "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1.0",
               "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
               "-c:a", "aac", str(src))
            C.tag(src, out, text, "1x1")
            st = ink_stats(frame_rgb(out, 0.4), frame_rgb(src, 0.4)[0, 0])
            self.assertGreater(st["white"], 40, "tag text was dropped")
            x0, y0, x1, y1 = st["bbox"]
            self.assertGreater(x0, 1080 * 0.5)  # top-right corner, not a full-frame label
            self.assertLess(y1, 1080 * 0.2)

    def test_ffmpeg_failure_is_raised(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError) as ctx:
                C.tag("/no/such-clip.mp4", str(Path(d) / "out.mp4"), "100%", "1x1")
            self.assertIn("No such file", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
