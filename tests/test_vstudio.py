"""Tests for Grok Bot Video Studio scripts. Run: python3 -m unittest discover -s tests -v
Media fixtures are generated with ffmpeg at test time (no stored sample files)."""
import datetime as dt, json, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import os, vstudio_voice as VO, vstudio_transcribe as T, vstudio_budget as B, vstudio_pack as P, vstudio_captions as C, vstudio_assemble as A, vstudio_qa_tech as Q, vstudio_beats as BT
from vstudio_media import probe

PRICING = json.loads((ROOT / "config" / "pricing.json").read_text())
CAPS = {"set_by": "owner", "set_at": "2026-10-16T06:00:00+00:00", "generations_per_video": 30, "generations_per_month": 200,
        "usd_per_video": 5.0, "usd_per_month": 25.0}  # test fixture: owner-entered caps


def ff(*args):
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


class Budget(unittest.TestCase):
    def test_xai_video_cost(self):
        c = B.job_cost(PRICING, {"provider": "xai", "model": "grok-imagine-video-1.5", "resolution": "720p", "seconds": 8, "count": 2, "images_in": 3})
        self.assertAlmostEqual(c["usd"], 0.14 * 16 + 0.01 * 6)

    def test_seconds_and_tiers(self):
        with self.assertRaises(B.BudgetError):
            B.job_cost(PRICING, {"provider": "xai", "model": "grok-imagine-video-1.5", "resolution": "720p", "seconds": 16})
        with self.assertRaises(B.BudgetError):
            B.job_cost(PRICING, {"provider": "xai", "model": "grok-imagine-video", "resolution": "1080p", "seconds": 5})
        self.assertAlmostEqual(B.job_cost(PRICING, {"provider": "xai", "model": "grok-imagine-video-1.5-lite", "resolution": "720p", "seconds": 10})["usd"], 0.30)
        self.assertAlmostEqual(B.job_cost(PRICING, {"provider": "xai", "model": "grok-imagine-video-1.5", "resolution": "1080p", "seconds": 4})["usd"], 1.00)

    def test_only_xai_and_builtin(self):
        self.assertEqual(set(PRICING["providers"]), {"builtin", "xai", "browser"})
        with self.assertRaises(B.BudgetError):
            B.job_cost(PRICING, {"provider": "veo", "model": "veo-3.1", "resolution": "720p", "seconds": 8})

    def test_builtin_counts_generations(self):
        e = B.estimate(PRICING, {"jobs": [{"provider": "builtin", "model": "generate-video", "seconds": 6, "count": 4},
                                          {"provider": "builtin", "model": "generate-image", "count": 3}]})
        self.assertEqual(e["builtin"]["generations"], 7); self.assertEqual(e["builtin"]["usd"], 0.0)

    def test_tts(self):
        self.assertAlmostEqual(B.job_cost(PRICING, {"provider": "xai", "model": "tts", "chars": 2000})["usd"], 0.03)

    def test_caps_and_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            led = Path(d) / "ledger.jsonl"
            plan = {"jobs": [{"provider": "xai", "model": "grok-imagine-video-1.5", "resolution": "720p", "seconds": 10, "count": 3}]}  # $4.20
            self.assertEqual(B.check(PRICING, CAPS, B.read_ledger(led), "p1", plan), [])
            B.log_entry(led, {"project": "p1", "provider": "xai", "model": "grok-imagine-video-1.5", "usd": 1.0, "generations": 1})
            probs = B.check(PRICING, CAPS, B.read_ledger(led), "p1", plan)
            self.assertTrue(any("for this video" in p for p in probs))
            self.assertEqual(B.spent(B.read_ledger(led))["xai"]["usd"], 1.0)

    def test_generation_cap(self):
        plan = {"jobs": [{"provider": "builtin", "model": "generate-video", "seconds": 6, "count": 31}]}
        self.assertTrue(B.check(PRICING, CAPS, [], "p", plan))

    def test_no_caps_refuses_everything(self):
        plan = {"jobs": [{"provider": "builtin", "model": "generate-video", "seconds": 6, "count": 1}]}
        self.assertTrue(any("not set" in p for p in B.check(PRICING, {}, [], "p", plan)))
        self.assertTrue(B.check(PRICING, {"generations_per_video": 5, "generations_per_month": 9}, [], "p", plan))  # not owner-set

    def test_generation_caps_without_dollar_caps(self):
        caps = B.make_caps(10, 50)
        builtin = {"jobs": [{"provider": "builtin", "model": "generate-video", "seconds": 6, "count": 3}]}
        self.assertEqual(B.check(PRICING, caps, [], "p", builtin), [])
        api = {"jobs": [{"provider": "xai", "model": "grok-imagine-video-1.5-lite", "resolution": "720p", "seconds": 6}]}
        self.assertTrue(any("no dollar caps" in p for p in B.check(PRICING, caps, [], "p", api)))

    def test_browser_counts_against_generation_caps(self):
        caps = B.make_caps(2, 50)
        plan = {"jobs": [{"provider": "browser", "model": "imagine-web-video", "seconds": 6, "count": 3}]}
        self.assertTrue(any("generations for this video" in p for p in B.check(PRICING, caps, [], "p", plan)))
        self.assertEqual(B.check(PRICING, B.make_caps(5, 50), [], "p", plan), [])

    def test_make_caps_validation(self):
        for args in ((0, 10), (5, 10, -1.0, 5.0), (5, 10, 9.0, 5.0)):
            with self.assertRaises(B.BudgetError): B.make_caps(*args)
        self.assertEqual(B.make_caps(5, 10, 2.0, 20.0)["set_by"], "owner")

    def test_demo_allowance(self):
        with tempfile.TemporaryDirectory() as d:
            led = Path(d) / "l.jsonl"
            self.assertEqual(B.demo_remaining(PRICING, B.read_ledger(led)), {"video": 1, "image": 2})
            B.log_entry(led, {"project": "demo", "provider": "builtin", "model": "generate-video", "generations": 1})
            B.log_entry(led, {"project": "demo", "provider": "builtin", "model": "generate-image", "generations": 1})
            self.assertEqual(B.demo_remaining(PRICING, B.read_ledger(led)), {"video": 0, "image": 1})

    def test_explicit_no_cap(self):
        now = dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)
        caps = B.make_caps(None, 500, no_cap=["gen-video"], now=now)
        self.assertEqual(caps["no_cap"]["generations_per_video"]["chosen_at"], "2026-10-16T00:00:00+00:00")
        big = {"jobs": [{"provider": "builtin", "model": "generate-video", "seconds": 6, "count": 400}]}
        self.assertEqual(B.check(PRICING, caps, [], "p", big), [])
        self.assertTrue(B.check(PRICING, caps, [], "p", {"jobs": [dict(big["jobs"][0], count=501)]}))  # month cap still applies
        with self.assertRaises(B.BudgetError):
            B.make_caps(None, None, None, None, no_cap=["usd-month"])  # dollar no-cap needs typed confirmation
        with self.assertRaises(B.BudgetError):
            B.make_caps(10, 20, 5, None, no_cap=["usd-month"], confirmation="yes")
        caps = B.make_caps(10, 20, 5, None, no_cap=["usd-month"], confirmation=B.NO_DOLLAR_CAP_PHRASE)
        api = {"jobs": [{"provider": "xai", "model": "grok-imagine-video-1.5", "resolution": "480p", "seconds": 10}]}
        self.assertEqual(B.check(PRICING, caps, [], "p", api), [])
        self.assertEqual(B.estimate(PRICING, api)["xai"]["usd"], 0.8)  # the A1 card still shows the estimate
        with self.assertRaises(B.BudgetError):
            B.make_caps(10, 20, no_cap=["gen-video"])  # both a number and no-cap
        bad = dict(caps); bad["no_cap"] = {"usd_per_month": {"chosen_at": "x"}}
        self.assertTrue(B.validate_caps(bad))

    def test_tts_is_dollars_not_generations(self):
        c = B.job_cost(PRICING, {"provider": "xai", "model": "tts", "chars": 2000})
        self.assertEqual((c["generations"], c["usd"]), (0, 0.03))

    def test_cli_no_cap(self):
        d = Path(tempfile.mkdtemp()); f = d / "caps.json"
        self.assertEqual(B.main(["set-caps", "--caps", str(f), "--gen-video", "10", "--no-cap", "gen-month"]), 0)
        self.assertIn("generations_per_month", json.loads(f.read_text())["no_cap"])
        self.assertEqual(B.main(["set-caps", "--caps", str(f), "--gen-video", "10", "--gen-month", "50", "--usd-video", "2",
                                 "--no-cap", "usd-month"]), 2)
        self.assertEqual(B.main(["set-caps", "--caps", str(f), "--gen-video", "10", "--gen-month", "50", "--usd-video", "2",
                                 "--no-cap", "usd-month", "--confirm-no-dollar-cap", "NO DOLLAR CAP"]), 0)
        shutil.rmtree(d)

    def test_cli_set_caps_and_check(self):
        with tempfile.TemporaryDirectory() as d:
            plan = Path(d) / "plan.json"; caps = Path(d) / "caps.json"; pr = str(ROOT / "config/pricing.json")
            plan.write_text(json.dumps({"jobs": [{"provider": "xai", "model": "grok-imagine-video-1.5", "resolution": "1080p", "seconds": 10, "count": 3}]}))
            args = ["check", "--pricing", pr, "--caps", str(caps), "--ledger", str(Path(d) / "l.jsonl"), "--project", "x", "--plan", str(plan)]
            self.assertEqual(B.main(args), 3)
            self.assertEqual(B.main(["set-caps", "--caps", str(caps), "--gen-video", "20", "--gen-month", "100", "--usd-video", "5", "--usd-month", "20"]), 0)
            self.assertEqual(B.main(args), 3)
            plan.write_text(json.dumps({"jobs": [{"provider": "xai", "model": "grok-imagine-video-1.5", "resolution": "720p", "seconds": 10, "count": 2}]}))
            self.assertEqual(B.main(args), 0)


class Pack(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); (self.d / "refs").mkdir()
        ff("-f", "lavfi", "-i", "testsrc2=s=512x512", "-frames:v", "1", str(self.d / "refs/front.png"))
        self.pack = {"schema_version": 1, "name": "Nova", "slug": "nova", "version": 1, "type": "fictional", "trust": "untrusted",
                     "created_by": "Owner", "created_at": "2026-10-16T06:00:00+00:00",
                     "lock_text": "Adult woman, short silver bob, amber eyes, freckles, teal flight jacket.",
                     "refs": [{"file": "refs/front.png", "view": "front", "sha256": P.sha256(self.d / "refs/front.png")}],
                     "voice": {"route": "builtin"}, "license": "owner-created", "provenance": {"route": "builtin"}}
        self.write()

    def tearDown(self): shutil.rmtree(self.d)
    def write(self): (self.d / "pack.json").write_text(json.dumps(self.pack))

    def test_valid_untrusted(self):
        r = P.validate(self.d); self.assertTrue(r["ok"], r); self.assertEqual(r["trust"], "untrusted")

    def test_tamper_detected(self):
        ff("-f", "lavfi", "-i", "color=red:s=64x64", "-frames:v", "1", str(self.d / "refs/front.png"))
        self.assertIn("sha256 mismatch", " ".join(P.validate(self.d)["errors"]))

    def test_not_an_image(self):
        (self.d / "refs/front.png").write_text("hello"); self.pack["refs"][0]["sha256"] = P.sha256(self.d / "refs/front.png"); self.write()
        self.assertIn("not a PNG", " ".join(P.validate(self.d)["errors"]))

    def test_path_escape(self):
        self.pack["refs"][0]["file"] = "../../etc/passwd"; self.write()
        self.assertFalse(P.validate(self.d)["ok"])

    def test_screen_refuses_hits(self):
        for kw in ({"real_person": True, "minor": False, "sexual": False}, {"real_person": False, "minor": True, "sexual": False},
                   {"real_person": False, "minor": False, "sexual": True}):
            self.write(); r = P.screen(self.d, "videoReview", **kw); self.assertFalse(r["ok"], kw)
            self.assertFalse(P.approve(self.d)["ok"])

    def test_approve_requires_clean_screen(self):
        self.assertFalse(P.approve(self.d)["ok"])
        P.screen(self.d, "videoReview", False, False, False)
        r = P.approve(self.d); self.assertTrue(r["ok"], r); self.assertEqual(r["trust"], "approved")

    def test_hash_resets_trust(self):
        P.screen(self.d, "videoReview", False, False, False); P.approve(self.d); P.rehash(self.d)
        self.assertEqual(json.loads((self.d / "pack.json").read_text())["trust"], "untrusted")

    def _owner(self, **over):
        self.pack.update(type="owner", provenance={"route": "owner-upload"}, voice={"route": "owner-recording"}, consent_file="consent.json"); self.write()
        c = {"owner_name": "Owner", "date": "2026-10-16T06:00:00+00:00", "scope": "presenter in my own videos", "providers": ["xai"],
             "voice": True, "revocable": True, "ref_sha256": [self.pack["refs"][0]["sha256"]], "consent_sentence_match": 0.93}
        c.update(over); (self.d / "consent.json").write_text(json.dumps(c))

    def test_owner_pack_valid(self):
        self._owner(); self.assertTrue(P.validate(self.d)["ok"], P.validate(self.d))

    def test_owner_pack_rules(self):
        for over, msg in (({"owner_name": "Someone Else"}, "owner only"), ({"providers": ["builtin"]}, "never builtin"), ({"providers": ["veo"]}, "exactly"),
                          ({"revocable": False}, "revocable"), ({"consent_sentence_match": 0.5}, "0.85"), ({"ref_sha256": ["0" * 64]}, "does not match")):
            self._owner(**over); self.assertIn(msg, " ".join(P.validate(self.d)["errors"]))

    def test_owner_pack_needs_consent(self):
        self._owner(); (self.d / "consent.json").unlink(); self.assertIn("requires consent.json", " ".join(P.validate(self.d)["errors"]))

    def test_fictional_cannot_use_owner_voice(self):
        self.pack["voice"] = {"route": "owner-recording"}; self.write(); self.assertFalse(P.validate(self.d)["ok"])

    def test_bad_slug(self):
        self.pack["slug"] = "Bad Slug!"; self.write(); self.assertFalse(P.validate(self.d)["ok"])


WORDS = [{"word": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate(
    "Grok Bot Video Studio plans every shot. Then it checks the cut before you see it.".split())]


class Captions(unittest.TestCase):
    def test_group_and_srt(self):
        cues = C.group(WORDS)
        self.assertTrue(all(len(c["text"]) <= 32 for c in cues))
        self.assertTrue(any(c["text"].endswith("shot.") for c in cues))  # sentence break
        self.assertFalse(any("shot. Then" in c["text"] for c in cues))
        self.assertEqual(C.check(cues), [])
        srt = C.to_srt(cues); self.assertTrue(srt.startswith("1\n00:00:00,000 --> "))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.srt"; p.write_text(srt); back = C.read_srt(p)
            self.assertEqual([c["text"] for c in back], [c["text"] for c in cues])

    def test_check_flags_fast_and_overlap(self):
        bad = [{"start": 0, "end": 0.5, "text": "x" * 40}, {"start": 0.3, "end": 1.0, "text": "ok"}]
        p = C.check(bad); self.assertTrue(any("cps" in x for x in p)); self.assertTrue(any("overlaps" in x for x in p))

    def test_reject_negative_word(self):
        with self.assertRaises(ValueError): C.group([{"word": "a", "start": 1.0, "end": 0.5}])


class Media(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp())
        for i, src in enumerate(["testsrc2=s=1280x720:r=30", "mandelbrot=s=640x480:r=30", "cellauto=s=640x480:r=30:rule=110"]):
            ff("-f", "lavfi", "-i", src, "-f", "lavfi", "-i", "sine=f=330", "-t", "4", "-pix_fmt", "yuv420p", "-c:a", "aac", str(cls.d / f"c{i}.mp4"))
        # voice-like signal: amplitude-modulated tone with pauses < 1 s; music: two-tone chord with pulses
        ff("-f", "lavfi", "-i", "sine=f=220:d=10", "-af", "volume='0.6*(0.6+0.4*sin(2*PI*3*t))':eval=frame", str(cls.d / "voice.wav"))
        ff("-f", "lavfi", "-i", "aevalsrc='0.4*sin(2*PI*440*t)*(0.5+0.5*lt(mod(t\\,0.5)\\,0.1))+0.3*sin(2*PI*554*t)':d=20", str(cls.d / "music.wav"))

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.d)

    def edl(self, **kw):
        e = {"aspect": "9x16", "fps": 30, "crossfade": 0.5, "clips": [{"path": str(self.d / f"c{i}.mp4"), "in": 0, "out": 4} for i in range(3)]}
        e.update(kw); return e

    def test_validate_edl(self):
        self.assertTrue(A.validate_edl({"aspect": "4x3", "clips": []}))
        self.assertIn("outside", " ".join(A.validate_edl(self.edl(clips=[{"path": str(self.d / 'c0.mp4'), "in": 0, "out": 9}]))))

    def test_assemble_voice_and_music_then_qa_pass(self):
        out = self.d / "out_916.mp4"
        r = A.build(self.edl(voice=str(self.d / "voice.wav"), music=str(self.d / "music.wav")), out)
        self.assertAlmostEqual(r["duration"], 11.0, places=2)
        info = probe(out); self.assertEqual((info["video"]["width"], info["video"]["height"]), (1080, 1920))
        q = Q.qa(out, "9x16", expect=11.0)
        self.assertTrue(q["ok"], q["problems"])
        self.assertLess(abs(q["checks"]["loudness"]["integrated_lufs"] + 16), 1.5)

    def test_assemble_music_lead_16x9(self):
        out = self.d / "out_169.mp4"
        A.build(self.edl(aspect="16x9", music=str(self.d / "music.wav"), music_role="lead"), out)
        self.assertTrue(Q.qa(out, "16x9", expect=11.0)["ok"])

    def test_qa_catches_black_freeze_silence_wrong_size(self):
        bad = self.d / "bad.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=3", "-f", "lavfi", "-i", "color=black:s=1080x1920:r=30:d=3",
           "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=3", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
           "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a", "-t", "9", "-pix_fmt", "yuv420p", str(bad))
        q = Q.qa(bad, "9x16")
        txt = " ".join(q["problems"])
        self.assertIn("black", txt); self.assertIn("frozen", txt); self.assertIn("silence", txt)
        self.assertIn("size", " ".join(Q.qa(bad, "16x9")["problems"]))

    def test_burn_captions(self):
        base = self.d / "out_916_b.mp4"
        A.build(self.edl(voice=str(self.d / "voice.wav")), base)
        srt = self.d / "s.srt"; srt.write_text(C.to_srt(C.group(WORDS)))
        out = self.d / "captioned.mp4"; C.burn(base, srt, out, "9x16")
        self.assertAlmostEqual(probe(out)["duration"], probe(base)["duration"], delta=0.1)
        # captions change pixels in the lower safe area of a frame at t=1s
        def frame(p):
            return subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(p), "-frames:v", "1", "-vf", "crop=1080:500:0:1300",
                                   "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        self.assertNotEqual(frame(base), frame(out))

    def test_ai_tag(self):
        base = self.d / "out_tag_base.mp4"; A.build(self.edl(aspect="1x1"), base)
        out = self.d / "tagged.mp4"; C.tag(base, out, "AI-generated", "1x1")
        def corner(p):
            return subprocess.run(["ffmpeg", "-v", "error", "-ss", "2", "-i", str(p), "-frames:v", "1", "-vf", "crop=400:120:680:0",
                                   "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
        self.assertNotEqual(corner(base), corner(out))
        self.assertTrue(Q.qa(out, "1x1", expect=11.0, allow_silence=True)["checks"]["probe"]["video"])


class Beats(unittest.TestCase):
    def test_slots_cover_song(self):
        beats = [i * 0.5 for i in range(1, 120)]
        s = BT.slots(60.0, beats, 8.0, 3.0)
        self.assertEqual(s[0]["start"], 0.0); self.assertEqual(s[-1]["end"], 60.0)
        for a, b in zip(s, s[1:]): self.assertEqual(a["end"], b["start"])
        self.assertTrue(all(x["end"] - x["start"] >= 3.0 for x in s))
        self.assertTrue(all(x["end"] in beats or x is s[-1] for x in s))

    def test_beat_grid_on_click_track(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "click.wav"
            ff("-f", "lavfi", "-i", "aevalsrc='sin(2*PI*1000*t)*lt(mod(t\\,0.5)\\,0.03)':s=22050:d=20", str(p))
            dur, tempo, beats = BT.beat_grid(p)
            self.assertAlmostEqual(dur, 20.0, delta=0.1)
            self.assertTrue(110 <= tempo <= 130 or 55 <= tempo <= 65, tempo)
            self.assertGreater(len(beats), 15)


class Transcribe(unittest.TestCase):
    def test_word_match(self):
        self.assertEqual(T.word_match("Plan 3 shots, then cut.", "plan three shots then cut"), 1.0)
        self.assertLess(T.word_match("plan three shots then cut", "plan shots"), 0.95)
        with self.assertRaises(ValueError): T.word_match("", "x")

    def test_keyterms(self):
        self.assertEqual(T.missing_keyterms(["Grok Imagine", "Studio"], "we used grok imagine today"), ["Studio"])

    def test_local_model_runs(self):
        # Runs the real faster-whisper model on generated audio; a pure tone must yield no confident words.
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tone.wav"; ff("-f", "lavfi", "-i", "sine=f=440:d=3", str(p))
            self.assertIsInstance(T.transcribe(p), list)


PIPER_DIR = os.environ.get("VSTUDIO_PIPER_DIR", "/workspace/vstudio/piper")


class Voice(unittest.TestCase):
    """End to end: real Piper TTS -> WAV -> ffprobe -> faster-whisper match >= 0.95 (needs the pinned Piper install)."""
    SCRIPT = ("Every great video starts with a simple idea. First we write the script, "
              "then we plan each shot, and finally we add music and captions.")

    def test_piper_to_transcript_match(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "n.wav"
            r = VO.local(self.SCRIPT, out, PIPER_DIR)
            self.assertTrue(out.exists()); self.assertGreater(r["duration"], 4); self.assertEqual(r["sample_rate"], 22050)
            self.assertGreaterEqual(T.word_match(self.SCRIPT, " ".join(w["word"] for w in T.transcribe(out))), 0.95)

    def test_rate_and_errors(self):
        with tempfile.TemporaryDirectory() as d:
            slow = VO.local("One two three four five.", Path(d) / "s.wav", PIPER_DIR, rate=0.7)
            fast = VO.local("One two three four five.", Path(d) / "f.wav", PIPER_DIR, rate=1.4)
            self.assertGreater(slow["duration"], fast["duration"])
            with self.assertRaises(VO.VoiceError): VO.local(" ", Path(d) / "x.wav", PIPER_DIR)
            with self.assertRaises(VO.VoiceError): VO.local("hi", Path(d) / "x.wav", d)  # not installed there
            (Path(d) / "t.txt").write_text("Short line.")
            self.assertEqual(VO.main(["local", str(Path(d) / "t.txt"), str(Path(d) / "y.wav"), "--piper-dir", PIPER_DIR]), 0)
            self.assertEqual(VO.main(["local", str(Path(d) / "t.txt"), str(Path(d) / "z.wav"), "--piper-dir", d]), 1)


class DemoFallback(unittest.TestCase):
    """The no-scripts demo path in vstudio-getting-started: plain ffmpeg burns captions and the AI tag."""
    def test_plain_ffmpeg_captions_and_tag(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            ff("-f", "lavfi", "-i", "testsrc2=s=720x1280:r=24:d=3", "-f", "lavfi", "-i", "sine=f=300:d=3",
               "-shortest", "-pix_fmt", "yuv420p", str(d / "demo.mp4"))
            (d / "demo.srt").write_text("1\n00:00:00,000 --> 00:00:01,500\nHello there\n\n2\n00:00:01,500 --> 00:00:03,000\nThis is the demo\n")
            vf = ("subtitles=demo.srt:force_style='FontSize=14,Outline=2,MarginV=40',"
                  "drawtext=text='AI-generated':font=Sans:fontsize=h/40:fontcolor=white@0.85:box=1:boxcolor=black@0.4:x=w-tw-20:y=20")
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", "demo.mp4", "-vf", vf, "-c:a", "copy", "demo_final.mp4"], cwd=d, check=True)
            info = probe(d / "demo_final.mp4")
            self.assertEqual((info["video"]["width"], info["video"]["height"]), (720, 1280)); self.assertTrue(info["audio"])
            # the burned frame differs from the source frame (captions/tag were drawn)
            a = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(d / "demo.mp4"), "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True).stdout
            b = subprocess.run(["ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(d / "demo_final.mp4"), "-frames:v", "1", "-f", "rawvideo", "-"], capture_output=True).stdout
            self.assertNotEqual(a, b)


class Config(unittest.TestCase):
    def test_pricing_complete(self):
        for name, prov in PRICING["providers"].items():
            self.assertIn(prov["billing"], ("usd", "generations"))
            for m, spec in prov["models"].items():
                self.assertIn("kind", spec)
                if prov["billing"] == "usd" and spec["kind"] == "video":
                    self.assertTrue(all(v > 0 for v in spec["per_second"].values()), m)
        self.assertNotIn("default_caps", PRICING)
        self.assertEqual(PRICING["limits"]["demo"], {"videos": 1, "images": 2})

    def test_schema_is_json(self):
        s = json.loads((ROOT / "config" / "pack.schema.json").read_text())
        self.assertEqual(set(s["properties"]["type"]["enum"]), {"fictional", "owner"})


if __name__ == "__main__":
    unittest.main()
