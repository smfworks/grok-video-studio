"""Tests for the code-drawn kits (vkit, numbers, case study, visual kit, promo). Run: python3 -m unittest discover -s tests -v
No network, no API keys, 0 generations. Fixtures live in examples/ (made-up figures, labeled as examples)."""
import copy, json, math, os, sys, tempfile, unittest, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "kits"))
import vkit, numbers_extract as NE, numbers_kit as NK, case_study_kit as CS, visual_kit as VK, promo_kit as PK

NUM = ROOT / "examples" / "numbers-plate-cost"
CASE = ROOT / "examples" / "fix-it-case-study"


class Vkit(unittest.TestCase):
    def test_fonts_resolve(self):
        # The layouts are sized for these fonts; install.sh fetches pinned copies into $VSTUDIO_HOME/fonts.
        for p, want in ((vkit.CHALK_FONT, "CaveatBrush"), (vkit.HAND_FONT, "Kalam"), (vkit.SANS_FONT, "Inter")):
            self.assertTrue(os.path.isfile(p), p)
            self.assertIn(want, os.path.basename(p), "kit font missing (fell back to DejaVu): run install.sh and set VSTUDIO_HOME")

    def test_every_pose_draws(self):
        for pose in vkit.POSES:
            sp, ax, ay = vkit.chef_sprite(.6, pose, 0.5, 'grin', False, 1.0 if pose == 'idea' else 0, .1)
            self.assertIsNotNone(sp.getbbox(), pose)

    def test_sfx_and_bed(self):
        for k in ('printer', 'bell', 'chalk', 'tick', 'pop', 'ding', 'whoosh', 'riser', 'thump'):
            x = vkit.sfx(k)
            self.assertGreater(len(x), 0, k)
            self.assertTrue(math.isfinite(float(abs(x).max())), k)
        b = vkit.bed(4.0, 124, drop=2.0, outro=3.0)
        self.assertAlmostEqual(len(b) / vkit.SR, 4.0, delta=.05)

    def test_pdf(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.pdf"; pdf = vkit.PDF(); pdf.page(); pdf.text(72, 72, "Checklist (example)", 18, bold=True); pdf.box(72, 100, 200, 40); pdf.save(str(p))
            b = p.read_bytes(); self.assertTrue(b.startswith(b"%PDF-1.4")); self.assertIn(b"%%EOF", b[-10:])


class VisualKit(unittest.TestCase):
    def test_build_and_immutable(self):
        with tempfile.TemporaryDirectory() as d:
            VK.build(d)
            kit = json.loads((Path(d) / "kit.json").read_text())
            self.assertEqual(len(list((Path(d) / "downloads").glob("*.pdf"))), 3)
            self.assertTrue((Path(d) / "character_block.txt").read_text().startswith("CHALK CHEF"))
            self.assertTrue(kit)
            with self.assertRaises(AssertionError):
                VK.build(d)


class Numbers(unittest.TestCase):
    def setUp(self):
        self.nj = json.loads((NUM / "numbers.json").read_text())

    def test_grid_matches_saved_cells(self):
        cells = NE.grid(str(NUM / "plate_cost_sheet.csv"))
        saved = json.loads((NUM / "cells.json").read_text())
        self.assertEqual({k: v["v"] for k, v in cells.items()}, {k: v["v"] for k, v in saved.items()})

    def test_confirm_and_check(self):
        txt, bad = NE.confirm(self.nj, json.loads((NUM / "cells.json").read_text()))
        self.assertEqual(bad, 0, txt)
        NK.check(self.nj)

    def test_mismatch_is_caught(self):
        cells = json.loads((NUM / "cells.json").read_text())
        for v in cells.values():
            if v["v"] == 5.4: v["v"] = 5.9
        with self.assertRaises(AssertionError):
            txt, bad = NE.confirm(self.nj, cells)
            self.assertEqual(bad, 0)

    def test_low_confidence_cell_needs_owner(self):
        cells = NE.grid(str(NUM / "plate_cost_sheet.csv"), low=("E4",))
        txt, bad = NE.confirm(self.nj, cells)
        self.assertGreater(bad, 0)

    def test_frames_render(self):
        for aspect in ("9x16", "16x9"):
            fn, L, dur = NK.make_frame(self.nj, aspect)
            for t in (0.5, dur * .4, dur * .8, dur - .2):
                self.assertIsNotNone(fn(t).getbbox())


class CaseStudy(unittest.TestCase):
    def setUp(self):
        self.c = json.loads((CASE / "case.json").read_text())

    def test_check_example(self):
        self.assertEqual(CS.check(self.c), self.c["duration"])

    def test_client_name_needs_permission(self):
        c = copy.deepcopy(self.c); c["client_name"] = "A Real Diner"
        with self.assertRaises(AssertionError): CS.check(c)

    def test_real_results_need_owner_source(self):
        c = copy.deepcopy(self.c); c["example"] = False
        with self.assertRaises(AssertionError): CS.check(c)
        c["owner_result_source"] = "owner's POS ticket-time report, two Fridays"; CS.check(c)

    def fake_voice(self, d):
        vd = Path(d) / "voice"; vd.mkdir()
        cues = [{"hash": f"h{i}", "file": f"cue_{i:02d}.mp3", "len": 2.0, "speech_start": .1, "speech_end": 1.9} for i, _ in enumerate(self.c["cues"])]
        (vd / "manifest.json").write_text(json.dumps({"cues": cues, "usd": 0, "chars": 0, "route": "test", "voice_id": None}))

    def test_frames_fit_every_aspect(self):
        with tempfile.TemporaryDirectory() as d:
            self.fake_voice(d)
            for aspect in ("9x16", "16x9", "1x1"):
                CS.STATS["min_text_px"] = 999
                fn, W, H, dur = CS.make(self.c, aspect, d)
                for t in self.c["still_times"]:
                    im = fn(t); self.assertEqual(im.size, (W, H))
                self.assertGreaterEqual(CS.STATS["min_text_px"], CS.L_[aspect]["floor"], aspect)
                self.assertGreaterEqual(CS.L_[aspect]["floor"], 0.05 * min(W, H) - 1, aspect)

    def test_srt_no_overlap(self):
        with tempfile.TemporaryDirectory() as d:
            self.fake_voice(d)
            ch = CS.chunks(CS.words_for(self.c, CS.load_man(d)), n=10)
            for a, b in zip(ch, ch[1:]): self.assertLessEqual(a[0][1], b[0][1])
            srt = CS.srt(self.c, d); self.assertIn("-->", srt); self.assertNotIn("*", srt)

    def test_balanced_chunks(self):
        line = [(f"w{i}", i, i + 1) for i in range(7)]
        self.assertEqual([len(c) for c in CS.chunks([line], n=6)], [4, 3])

    def test_match_interpolates_numbers(self):
        heard = [("Tickets", 0.0, .4), ("at", .4, .5), ("twenty-two", .5, 1.0), ("minutes", 1.0, 1.4)]
        T, r = CS._match(["Tickets", "at", "22", "minutes"], heard, 0, 1.4)
        self.assertEqual(T[0], (0.0, .4)); self.assertAlmostEqual(T[2][0], .5); self.assertAlmostEqual(T[2][1], 1.0)


class Promo(unittest.TestCase):
    def test_music(self):
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "m.wav"); PK.music(p, 3.0)
            with wave.open(p) as w: self.assertAlmostEqual(w.getnframes() / w.getframerate(), 3.0, delta=.1)


if __name__ == "__main__":
    unittest.main()
