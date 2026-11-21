"""Tests for the strobe ad tools: vstudio_brand_frames.py, vstudio_audio_finish.py, vstudio_strobe_cut.py.
All fixtures (images, a test clip, a synthetic drum-and-bass track) are generated in code at test time.

Run: python3 -m unittest tests.test_strobe -v
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import vstudio_brand_frames as BF  # noqa: E402
import vstudio_audio_finish as AF  # noqa: E402
import vstudio_strobe_cut as SC  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
ACCENT = "#FF4D00"
NAME = "NORTHWIND LAB"
BPM = 140.0
BAR = 4 * 60 / BPM


# ---------------------------------------------------------------- fixtures
def synth_track(path, bpm=120.0, bars=10, lead=0.5, sr=48000):
    """Drum-and-bass loop: kick on every beat, a long bass hit on each downbeat, 16th-note hats.
    Downbeats at lead + k*bar. Returns the downbeat times."""
    beat = 60.0 / bpm
    n = int((lead + bars * 4 * beat + 1.0) * sr)
    y = np.zeros(n)
    rng = np.random.default_rng(3)
    t = np.arange(int(0.5 * sr)) / sr
    kick = np.sin(2 * np.pi * (50 * t + 60 * 0.03 * (1 - np.exp(-t / 0.03)))) * np.exp(-t / 0.12)
    bass = np.sin(2 * np.pi * 41 * t) * np.exp(-t / 0.35)
    hat = rng.standard_normal(int(0.02 * sr)) * np.exp(-np.arange(int(0.02 * sr)) / (0.003 * sr))
    hat = np.diff(hat, prepend=0)
    for k in range(bars * 4):
        s = int((lead + k * beat) * sr)
        y[s:s + len(kick)] += kick[:n - s] * (1.0 if k % 4 == 0 else 0.55)
        if k % 4 == 0:
            y[s:s + len(bass)] += bass[:n - s] * 0.9
    for k in range(bars * 16):
        s = int((lead + k * beat / 4) * sr)
        y[s:s + len(hat)] += hat[:n - s] * 0.08
    y = np.stack([y, y], 1) / np.abs(y).max() * 0.8
    AF.save(path, y, sr, codec="pcm_s16le")
    return [lead + k * 4 * beat for k in range(bars)]


def still(path, hue, label, size=(360, 640)):
    im = Image.new("RGB", size, tuple(int(40 + 180 * ((hue * k) % 1)) for k in (0.37, 0.61, 0.83)))
    d = ImageDraw.Draw(im)
    d.ellipse([size[0] * 0.25, size[1] * 0.2, size[0] * 0.75, size[1] * 0.5], fill=(230, 220, 210))
    d.text((10, 10), label, fill=(0, 0, 0))
    im.save(path)
    return str(path)


def music_info(bars=12, waves=3, tail=1.75, ending="hit"):
    off = AF.PRE
    T = bars * BAR
    info = {"bar": BAR, "bars": bars, "offset": off, "bpm": BPM,
            "downbeats": [off + k * BAR for k in range(bars + 1)],
            "wave_starts": [off + BAR * b for b in [0] + AF.wave_bars(bars, waves)], "waves": waves,
            "final_hit": off + T if ending == "hit" else None, "body_end": off + T,
            "duration": off + T + (tail if ending == "hit" else 0), "ending": ending}
    return info


def make_project(tmp, heroes=8, objects=3, clip=True, keys=(("K1", ["FINAL", "SAY"], ["STAYS", "HUMAN."]),
                                                              ("K2", ["THE LOCAL STACK NEVER", "SENDS DATA OFF"], ["ITS MACHINES."]))):
    tmp = Path(tmp)
    (tmp / "stills").mkdir(parents=True, exist_ok=True)
    spec = {"brand": {"name": NAME, "accent": ACCENT}, "aspect": "9:16", "frames": []}
    for kid, lines, punch in keys:
        spec["frames"].append({"id": kid, "layout": "statement", "lines": lines, "punch": punch, "key": True})
    spec["frames"] += [
        {"id": "MS1", "layout": "swiss", "lines": ["A RESEARCH", "LAB"], "sub": ["SMALL LINE"]},
        {"id": "MT1", "layout": "terminal", "rows": [["KEY", "VALUE"]], "headline": "BIG LINE", "bar": "ACCENT BAR"},
        {"id": "MW1", "layout": "type", "word": "OPEN", "bg": "ink"},
        {"id": "MW2", "layout": "type", "word": "OWNED", "bg": "paper", "sub": "SUB"},
        {"id": "ML1", "layout": "list", "title": "TEAMS", "items": ["ALPHA", "BETA"], "footer": "LINE"},
        {"id": "MI1", "layout": "index", "items": ["A", "B", "C", "D"], "footer": "LINE"},
        {"id": "TF1", "layout": "flash", "bg": "accent"},
        {"id": "TH1", "layout": "halftone"},
        {"id": "TR1", "layout": "rings"}]
    (tmp / "frames_spec.json").write_text(json.dumps(spec))
    BF.render_spec(spec, tmp / "frames", aspect=(270, 480))
    assets = []
    for i in range(heroes):
        a = {"id": f"H{i}", "path": still(tmp / "stills" / f"h{i}.png", i / 7 + 0.05, f"H{i}"), "role": "hero"}
        if i == 0:
            a.update(opener=True, clear_y=0.55, eyes_y=0.35)
        if i == heroes - 1:
            a["end"] = True
        assets.append(a)
    for i in range(objects):
        assets.append({"id": f"O{i}", "path": still(tmp / "stills" / f"o{i}.png", 0.5 + i / 9, f"O{i}"), "role": "object"})
    if clip and HAVE_FFMPEG:
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=360x640:r=24:d=1.5",
                        "-pix_fmt", "yuv420p", str(tmp / "clip.mp4")], check=True)
        assets.append({"id": "C1", "path": str(tmp / "clip.mp4"), "kind": "clip", "role": "hero", "clip_in": 0.2, "clip_len": 0.45})
    man = {"brand": {"name": NAME, "accent": ACCENT}, "aspect": "9:16", "fps": 25, "include": ["frames/frames.json"],
           "assets": assets}
    (tmp / "manifest.json").write_text(json.dumps(man))
    return tmp / "manifest.json"


def band_dirty_fraction(im, brand, bg):
    top, bot = brand.band()
    a = np.asarray(im.convert("RGB"), dtype=np.int16)[top:bot]
    return float((np.abs(a - np.array(bg)).sum(axis=2) > 30).mean())


# ---------------------------------------------------------------- brand
class Brand(unittest.TestCase):
    def test_hex_and_contrast(self):
        self.assertEqual(BF.parse_hex("#00A3FF"), (0, 163, 255))
        self.assertEqual(BF.parse_hex("0af"), (0, 170, 255))
        for bad in ("blue", "#12345", "", "#GGGGGG"):
            with self.assertRaises(BF.BrandError):
                BF.parse_hex(bad)
        self.assertEqual(BF.ink_on((255, 255, 255)), BF.INK)
        self.assertEqual(BF.ink_on((0, 0, 0)), BF.PAPER)

    def test_brand_needs_name_and_valid_mark(self):
        with self.assertRaises(BF.BrandError):
            BF.Brand("", ACCENT)
        with self.assertRaises(BF.BrandError):
            BF.Brand(NAME, ACCENT, mark="logo")
        self.assertEqual(BF.Brand("acme  co", ACCENT).name, "ACME CO")

    def test_no_brand_or_character_hard_coded(self):
        for f in ("vstudio_brand_frames.py", "vstudio_audio_finish.py", "vstudio_strobe_cut.py"):
            src = (ROOT / "scripts" / f).read_text()
            self.assertIsNone(re.search(r"(?i)smf ?works|00a3ff", src), f)

    def test_wordmark_centred_and_fits(self):
        for size in ((1080, 1920), (1920, 1080), (1080, 1080)):
            for name in ("ACME", NAME, "A VERY LONG BRAND NAME FOR TESTING WIDTH"):
                for mark in BF.MARKS:
                    b = BF.Brand(name, ACCENT, size, mark)
                    L, box = b.wordmark_layer(b.ink)
                    bb = L.getbbox()
                    self.assertLessEqual(bb[2] - bb[0], size[0] * 0.9, (name, size))
                    self.assertAlmostEqual((bb[0] + bb[2]) / 2, size[0] / 2, delta=size[0] * 0.02)
                    self.assertAlmostEqual((bb[1] + bb[3]) / 2, size[1] / 2, delta=size[1] * 0.03)
                    self.assertTrue(box[1] <= bb[1] and bb[3] <= box[3] + 2)

    def test_auto_black_white_and_halo(self):
        b = BF.Brand(NAME, ACCENT, (540, 960))
        _, info = b.overlay(Image.new("RGB", (540, 960), (250, 250, 250)))
        self.assertEqual(info["color"], "black")
        self.assertFalse(info["halo"])
        _, info = b.overlay(Image.new("RGB", (540, 960), (5, 5, 5)))
        self.assertEqual(info["color"], "white")
        chk = Image.fromarray(((np.indices((960, 540)).sum(0) // 12 % 2) * 255).astype(np.uint8)).convert("RGB")
        _, info = b.overlay(chk)
        self.assertTrue(info["halo"])

    def test_mark_switches_on_accent_background(self):
        b = BF.Brand(NAME, ACCENT, (540, 960))
        out, info = b.overlay(Image.new("RGB", (540, 960), b.accent))
        self.assertTrue(info["mark_on_accent"])
        L, _ = b.wordmark_layer(b.ink)
        a = np.asarray(out, dtype=np.int16)
        self.assertGreater(np.abs(a - np.array(b.accent)).sum(axis=2).max(), 200)

    def test_eye_line_shift(self):
        b = BF.Brand(NAME, ACCENT, (1080, 1920))
        top, bot = b.band()
        self.assertEqual(b.safe_shift(top - 200), 0)
        eyes = (top + bot) / 2
        sh = b.safe_shift(eyes)
        self.assertGreater(sh, 0)
        self.assertTrue(b.eye_line_clear(eyes, sh))
        self.assertFalse(b.eye_line_clear(eyes, 0))
        self.assertLessEqual(b.safe_shift(1900), int(0.3 * 1920))
        im = Image.new("RGB", (100, 200), (200, 0, 0))
        s = BF.shift_up(im, 40)
        self.assertEqual(s.size, (100, 200))
        self.assertEqual(s.getpixel((50, 10)), (200, 0, 0))

    def test_fit_clear_reframes_for_every_aspect(self):
        src = Image.new("RGB", (720, 1280), (90, 90, 90))
        for size in ((1080, 1920), (1920, 1080), (1080, 1080)):
            b = BF.Brand(NAME, ACCENT, size)
            top, _ = b.band()
            im, info = BF.fit_clear(src, b, clear_y=0.54)
            self.assertEqual(im.size, size)
            self.assertLessEqual(info["clear_px"], top)
            self.assertGreater(info["clear_px"], 0)
        # same aspect as the frame: no crop room, so the picture is shifted (the v2 opener fix)
        b = BF.Brand(NAME, ACCENT, (1080, 1920))
        _, info = BF.fit_clear(src, b, clear_y=0.54)
        self.assertGreater(info["shift"], 0)
        # landscape from a portrait source: the crop window moves instead of shifting
        b = BF.Brand(NAME, ACCENT, (1920, 1080))
        _, info = BF.fit_clear(src, b, clear_y=0.54)
        self.assertEqual(info["shift"], 0)
        _, info = BF.fit_clear(src, b, clear_y=0.2)
        self.assertEqual((info["shift"], info["window_top"]), (0, round((1280 - 405) * 0.4, 1)))
        # with an eye line: eyes stay in frame and above the band in every aspect; the nose is best effort
        for size in ((1080, 1920), (1920, 1080), (1080, 1080)):
            b = BF.Brand(NAME, ACCENT, size)
            top, _ = b.band()
            _, info = BF.fit_clear(src, b, clear_y=0.54, eyes_y=0.35)
            self.assertGreaterEqual(info["eyes_px"], 0.06 * size[1] - 1)
            self.assertLessEqual(info["eyes_px"], top)
            if size[1] >= size[0]:
                self.assertTrue(info["clear_ok"], size)

    def test_layouts_keep_band_clear_in_every_aspect(self):
        frames = [
            {"layout": "swiss", "lines": ["A HUMAN", "RESEARCH LAB"], "sub": ["A SMALL LINE", "ANOTHER"], "corner": ["X", "Y"]},
            {"layout": "terminal", "rows": [["A", "B"], ["C", "D"]], "headline": ["BIG", "LINE"], "bar": "ACCENT BAR"},
            {"layout": "type", "word": "HUMAN", "sub": "SUB LINE", "bg": "ink"},
            {"layout": "statement", "lines": ["FINAL", "SAY"], "punch": ["STAYS", "HUMAN."], "bg": "paper"},
            {"layout": "list", "title": "TEAMS", "items": ["ALPHA", "BETA", "GAMMA"], "footer": "LINE"},
            {"layout": "index", "items": ["A", "B", "C", "D", "E"], "footer": "LINE"},
            {"layout": "flash", "bg": "accent", "caption": "CAPTION"}]
        for size in ((540, 960), (960, 540), (540, 540)):
            b = BF.Brand(NAME, ACCENT, size)
            for fr in frames:
                im = BF.render_frame(b, fr)
                self.assertEqual(im.size, size)
                bg = np.asarray(im.convert("RGB")).reshape(-1, 3)
                vals, cnt = np.unique(bg, axis=0, return_counts=True)
                dom = vals[cnt.argmax()]
                self.assertLess(band_dirty_fraction(im, b, dom), 0.04, (size, fr["layout"]))
            for lay in ("halftone", "rings"):
                self.assertEqual(BF.render_frame(b, {"layout": lay}).size, size)
        with self.assertRaises(BF.BrandError):
            BF.render_frame(b, {"layout": "collage"})

    def test_frame_words_and_label(self):
        self.assertEqual(BF.frame_words({"layout": "statement", "lines": ["THE LOCAL STACK"], "punch": ["NEVER LEAVES."]}), 5)
        self.assertEqual(BF.frame_words({"layout": "terminal", "rows": [["A", "B C D"]], "headline": "ONE TWO", "bar": "THREE"}), 3)
        self.assertEqual(BF.frame_words({"layout": "type", "word": "HUMAN", "words": 9}), 9)
        b = BF.Brand(NAME, ACCENT, (1080, 1920))
        x0, y0, x1, y1 = b.label_box()
        self.assertTrue(0 < x0 < x1 <= 1080 and y1 < 1920 - 500)
        top, bot = b.band()
        self.assertGreater(y0, bot)
        im = b.label(Image.new("RGB", (1080, 1920), (0, 0, 0)))
        self.assertGreater(np.asarray(im.crop((x0, y0, x1, y1))).max(), 200)

    def test_render_spec_manifest_and_duplicates(self):
        with tempfile.TemporaryDirectory() as d:
            spec = {"brand": {"name": NAME, "accent": ACCENT}, "aspect": "1:1", "frames": [
                {"id": "K", "layout": "statement", "lines": ["ONE TWO"], "punch": ["THREE"], "key": True},
                {"id": "F", "layout": "flash"}]}
            e = BF.render_spec(spec, Path(d) / "f", aspect=(270, 270))
            self.assertEqual([(x["id"], x["role"], x["key"], x["words"]) for x in e],
                             [("K", "message", True, 3), ("F", "texture", False, 0)])
            m = json.loads((Path(d) / "f" / "frames.json").read_text())
            self.assertTrue((Path(d) / "f" / m["assets"][0]["path"]).exists())
            spec["frames"].append({"id": "K", "layout": "flash"})
            with self.assertRaises(BF.BrandError):
                BF.render_spec(spec, Path(d) / "g")

    def test_grain_deterministic(self):
        im = Image.new("RGB", (64, 64), (128, 128, 128))
        a = np.asarray(BF.halftone_grain(im, seed=4))
        self.assertTrue(np.array_equal(a, np.asarray(BF.halftone_grain(im, seed=4))))
        self.assertFalse(np.array_equal(a, np.asarray(BF.halftone_grain(im, seed=5))))
        self.assertEqual(BF.duotone(im, BF.Brand(NAME, ACCENT)).size, (64, 64))


# ---------------------------------------------------------------- audio
class AudioDSP(unittest.TestCase):
    def test_fft_filter(self):
        sr = 8000
        t = np.arange(sr) / sr
        x = np.sin(2 * np.pi * 50 * t) + np.sin(2 * np.pi * 2000 * t)
        lo = AF.fft_filter(x, sr, hi=150)
        self.assertLess(np.abs(np.fft.rfft(lo))[2000] / np.abs(np.fft.rfft(x))[2000], 0.01)
        self.assertGreater(np.abs(np.fft.rfft(lo))[50] / np.abs(np.fft.rfft(x))[50], 0.95)

    def test_loop_body_on_the_bar_line(self):
        sr = 1000
        sec = np.random.default_rng(1).standard_normal((500, 2))
        body = AF.loop_body(sec, sr, 3)
        self.assertEqual(len(body), 1500)
        self.assertTrue(np.allclose(body[500 + 8:1000], sec[8:]))
        self.assertTrue(np.allclose(body[:500], sec))

    def test_ratchet_builds_and_ends_on_time(self):
        sr = 48000
        n = sr * 3
        r = AF.ratchet(n, sr, 2.0, 1.0)
        early = AF.rms_db(r, sr, 1.0, 1.2)
        late = AF.rms_db(r, sr, 1.8, 2.0)
        self.assertGreater(late, early + 6)
        self.assertLess(AF.rms_db(r, sr, 2.05, 3.0), -100)

    def test_grid_fit_and_refine(self):
        beats = [0.5 + k * 0.5 for k in range(16)]
        p, c = AF.fit_grid(beats)
        self.assertAlmostEqual(p, 0.5, places=6)
        self.assertAlmostEqual(c, 0.5, places=6)
        ons = np.array([0.503 + k * 0.4286 for k in range(30)])
        p, c = AF.refine_grid(ons, 0.43, 0.5)
        self.assertAlmostEqual(p, 0.4286, places=3)

    def test_wave_bars(self):
        self.assertEqual(AF.wave_bars(12, 3), [4, 8])
        self.assertEqual(AF.wave_bars(10, 3), [3, 7])


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class AudioFinish(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.song = Path(cls.tmp) / "song.wav"
        cls.downbeats = synth_track(cls.song, 120.0, 10, lead=0.5)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_analyze_finds_tempo_and_downbeats(self):
        a = AF.analyze(AF.load(self.song))
        self.assertAlmostEqual(a["bpm"], 120.0, delta=1.5)
        self.assertAlmostEqual(a["first_strong_downbeat"], self.downbeats[0], delta=0.03)
        self.assertAlmostEqual(a["last_strong_downbeat"], self.downbeats[-1], delta=0.06)

    def test_finish_resolved_hit(self):
        out = Path(self.tmp) / "hit.wav"
        info = AF.finish(self.song, out, Path(self.tmp) / "hit.json", bpm=120.0, downbeat=0.5, bars=8, waves=2,
                         ending="hit")
        self.assertEqual(info["bars"], 8)
        self.assertAlmostEqual(info["final_hit"], AF.PRE + 16.0, places=3)
        self.assertAlmostEqual(info["duration"], AF.PRE + 16.0 + 1.75, delta=0.01)
        self.assertEqual(info["risers"], [round(AF.PRE + 8.0, 4), round(AF.PRE + 16.0, 4)])
        self.assertAlmostEqual(info["lufs"], -14.0, delta=0.7)
        self.assertLessEqual(info["true_peak"], -1.0)
        e = info["ending_db"]
        self.assertGreater(e["hit"], e["+0.5s"] + 6)
        self.assertGreater(e["+0.5s"], e["+0.9s"])
        self.assertLess(e["last_50ms"], -60)
        self.assertTrue(json.loads((Path(self.tmp) / "hit.json").read_text())["wave_starts"])

    def test_finish_fade_and_loop(self):
        f = AF.finish(self.song, Path(self.tmp) / "fade.wav", None, bpm=120.0, downbeat=0.5, bars=4, ending="fade",
                      risers="none")
        self.assertAlmostEqual(f["duration"], 8.0, delta=0.01)
        self.assertIsNone(f["final_hit"])
        self.assertLess(f["ending_db"]["last_50ms"], -45)
        lp = AF.finish(self.song, Path(self.tmp) / "loop.wav", None, bpm=120.0, downbeat=0.5, bars=4, ending="loop",
                       waves=2)
        self.assertAlmostEqual(lp["duration"], 8.0, delta=0.01)
        self.assertEqual(lp["risers"], [round(AF.PRE + 4.0, 4)])

    def test_finish_loop_section_masks_seams(self):
        info = AF.finish(self.song, Path(self.tmp) / "lp.wav", None, bpm=120.0, downbeat=0.5, loop=(0, 3), repeat=3,
                         waves=3, ending="hit")
        self.assertEqual(info["bars"], 9)
        self.assertEqual(info["seams"], [round(AF.PRE + 6.0, 4), round(AF.PRE + 12.0, 4)])

    def test_too_many_bars_is_an_error(self):
        with self.assertRaises(AF.AudioError):
            AF.finish(self.song, Path(self.tmp) / "x.wav", None, bpm=120.0, downbeat=0.5, bars=40)
        self.assertEqual(AF.main(["finish", str(self.song), str(Path(self.tmp) / "x.wav"), "--info",
                                  str(Path(self.tmp) / "x.json"), "--bpm", "120", "--downbeat", "0.5", "--bars", "40"]), 1)


# ---------------------------------------------------------------- cutter
class CutterPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.man = SC.load_manifest(make_project(cls.tmp, clip=HAVE_FFMPEG))
        cls.info = music_info()
        cls.edl = SC.plan(cls.man, cls.info)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_subdivision_and_holds(self):
        self.assertEqual(SC.choose_subdiv(BAR, 25), 16)
        self.assertEqual(SC.choose_subdiv(4 * 60 / 70, 25), 32)
        self.assertEqual(SC.choose_subdiv(4 * 60 / 180, 24), 16)
        unit = BAR / 16
        self.assertEqual(SC.hold_units(13, unit, 4)[0], 12)   # 1.3 s -> 3 beats (1.286 s, within a frame)
        u, need = SC.hold_units(20, unit, 4)
        self.assertEqual(need, 2.0)
        self.assertGreaterEqual(u * unit, 2.0 - 0.04)

    def test_plan_passes_its_rules(self):
        res = SC.check(self.man, self.info, self.edl)
        self.assertTrue(res["ok"], res["problems"])
        self.assertEqual(self.edl["warnings"], [])

    def test_every_downbeat_is_a_cut_and_opener_first(self):
        starts = {r["frame"] for r in self.edl["shots"]}
        for t in self.info["downbeats"]:
            self.assertTrue(any(abs(round(t * 25) - s) <= 1 for s in starts), t)
        self.assertEqual(self.edl["shots"][0]["asset"], "H0")
        self.assertEqual(self.edl["shots"][0]["frame"], 0)

    def test_fast_runs_holds_and_end(self):
        rows = self.edl["shots"]
        fast = [r for r in rows if r["kind"] == "fast"]
        self.assertGreater(len(fast), len(rows) * 0.4)
        self.assertTrue(all(2 <= r["frames"] <= 3 for r in fast))
        for kid in ("K1", "K2"):
            self.assertGreaterEqual(max(r["dur"] for r in rows if r["asset"] == kid), 1.3 - 0.04)
        last = rows[-1]
        self.assertEqual((last["asset"], last["kind"]), ("H7", "end"))
        self.assertAlmostEqual(last["start"], self.info["final_hit"], delta=0.04)
        self.assertEqual(last["frame"] + last["frames"], self.edl["frames"])
        self.assertEqual(self.edl["frames"], round(self.info["duration"] * 25))

    def test_use_cap_and_balanced_waves(self):
        st = self.edl["stats"]
        self.assertLessEqual(st["max_uses"], 4)
        pw = st["cuts_per_wave"]
        self.assertEqual(len(pw), 3)
        self.assertLessEqual((max(pw) - min(pw)) / max(pw), 0.15 + 1e-9)
        for p, r in zip(self.edl["shots"], self.edl["shots"][1:]):
            self.assertNotEqual(p["asset"], r["asset"])

    def test_deterministic_and_aspect_independent(self):
        again = SC.plan(self.man, self.info)
        self.assertEqual(json.dumps(again["shots"]), json.dumps(self.edl["shots"]))
        other = dict(self.man, aspect="16:9")
        self.assertEqual([r["frame"] for r in SC.plan(other, self.info)["shots"]], [r["frame"] for r in self.edl["shots"]])

    def test_small_pool_uses_variants_not_mirrored_text(self):
        with tempfile.TemporaryDirectory() as d:
            man = SC.load_manifest(make_project(d, heroes=6, objects=2, clip=False))
            e = SC.plan(man, self.info, cap=3)
            self.assertLessEqual(e["stats"]["max_uses"], 3)
            self.assertEqual(e["warnings"], [])
            self.assertTrue(SC.check(man, self.info, e)["ok"])
            variants = [(r["asset"], r["variant"]) for r in e["shots"] if r["variant"]]
            self.assertTrue(variants)
            text_ids = {a["id"] for a in man["assets"] if a.get("text")}
            self.assertFalse([v for v in variants if v[0] in text_ids and v[1] == "flip"])

    def test_tiny_pool_breaks_cap_with_a_warning(self):
        with tempfile.TemporaryDirectory() as d:
            man = SC.load_manifest(make_project(d, heroes=2, objects=0, clip=False))
            e = SC.plan(man, music_info(), cap=2)
            self.assertTrue(any("use cap" in w for w in e["warnings"]))
            self.assertFalse(SC.check(man, music_info(), e)["ok"])

    def test_loop_ending(self):
        info = music_info(ending="loop")
        e = SC.plan(self.man, info)
        self.assertEqual(e["ending"], "loop")
        self.assertNotEqual(e["shots"][-1]["kind"], "end")
        self.assertNotEqual(e["shots"][-1]["asset"], e["shots"][0]["asset"])
        self.assertEqual(e["frames"], round((AF.PRE + 12 * BAR) * 25))
        with self.assertRaises(SC.CutError):
            SC.plan(self.man, info, ending="resolved")

    def test_check_flags_a_covered_downbeat_and_overuse(self):
        e = json.loads(json.dumps(self.edl))
        rows = e["shots"]
        db = round(self.info["downbeats"][2] * 25)
        i = next(k for k, r in enumerate(rows) if r["frame"] == db or abs(r["frame"] - db) <= 1)
        rows[i - 1]["frames"] += rows[i]["frames"]
        del rows[i]
        res = SC.check(self.man, self.info, e)
        self.assertFalse(res["ok"])
        self.assertTrue(any("downbeats without a cut" in p for p in res["problems"]))
        e2 = json.loads(json.dumps(self.edl))
        for r in e2["shots"][1:12]:
            r["asset"], r["variant"] = "H3", None
        self.assertTrue(any("used" in p for p in SC.check(self.man, self.info, e2)["problems"]))

    def test_manifest_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text(json.dumps({"brand": {"name": "X", "accent": "#123456"},
                                     "assets": [{"id": "A", "path": "a.png", "role": "star"}]}))
            with self.assertRaises(SC.CutError):
                SC.load_manifest(p)
            p.write_text(json.dumps({"brand": {"name": "X", "accent": "#123456"},
                                     "assets": [{"id": "A~1", "path": "a.png"}]}))
            with self.assertRaises(SC.CutError):
                SC.load_manifest(p)


@unittest.skipUnless(HAVE_FFMPEG, "needs ffmpeg")
class CutterRender(unittest.TestCase):

    def test_small_render_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            mp = make_project(d)
            song = Path(d) / "song.wav"
            synth_track(song, BPM, 4, lead=0.3)
            info = AF.finish(song, Path(d) / "music.wav", Path(d) / "music.json", bpm=BPM, downbeat=0.3, bars=2, waves=2)
            edl = Path(d) / "edl.json"
            self.assertEqual(SC.main(["plan", str(mp), str(Path(d) / "music.json"), str(edl)]), 0)
            out = Path(d) / "out.mp4"
            thumb = Path(d) / "thumb.jpg"
            self.assertEqual(SC.main(["render", str(mp), str(edl), str(out), "--audio", str(Path(d) / "music.wav"),
                                      "--scale", "0.25", "--workers", "2", "--thumbnail", str(thumb)]), 0)
            pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                                            "stream=codec_type,width,height,nb_read_frames,color_primaries:format=duration",
                                            "-of", "json", str(out)], capture_output=True, text=True).stdout)
            v = next(s for s in pr["streams"] if s["codec_type"] == "video")
            self.assertEqual((v["width"], v["height"]), (270, 480))
            self.assertEqual(int(v["nb_read_frames"]), json.loads(edl.read_text())["frames"])
            self.assertEqual(v.get("color_primaries"), "bt709")
            self.assertTrue(any(s["codec_type"] == "audio" for s in pr["streams"]))
            self.assertAlmostEqual(float(pr["format"]["duration"]), info["duration"], delta=0.1)
            t = Image.open(thumb)
            self.assertEqual(t.size, (270, 480))
            b = BF.Brand(NAME, ACCENT, (270, 480))
            top, bot = b.band()
            self.assertGreater(np.asarray(t.convert("L"))[top:bot].std(), 20)  # wordmark is on frame 0


if __name__ == "__main__":
    unittest.main()
