"""Contract tests for the xAI Imagine API adapter, using recorded responses in the documented shapes
(docs.x.ai video generation, checked 2026-10-16). LIVE verification is pending until the owner supplies XAI_API_KEY."""
import http.server, json, shutil, subprocess, sys, tempfile, threading, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import vstudio_providers as V, vstudio_budget as B, vstudio_pack as P


def make_mp4(path):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=24:d=1", "-pix_fmt", "yuv420p", str(path)], check=True)
    return Path(path).read_bytes()


class Recorder:
    """Replays a scripted list of (method, url-substring, status, body) and records every request."""
    def __init__(self, script):
        self.script, self.calls = list(script), []
    def __call__(self, method, url, headers=None, body=None, timeout=60, raw=False):
        self.calls.append({"method": method, "url": url, "headers": headers or {}, "body": body, "raw": raw})
        m, frag, status, resp = self.script.pop(0)
        assert m == method and frag in url, f"unexpected {method} {url}; expected {m} *{frag}*"
        return status, resp


class Base(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()); (self.home / "config").mkdir()
        shutil.copy(ROOT / "config/pricing.json", self.home / "config/pricing.json")
        (self.home / "caps.json").write_text(json.dumps(B.make_caps(30, 200, 5.0, 25.0)))  # owner-entered caps (fixture)
        self.mp4 = make_mp4(self.home / "fixture.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=512x512", "-frames:v", "1", str(self.home / "still.png")], check=True)
        self.sleeps = []
    def tearDown(self): shutil.rmtree(self.home)
    def run_job(self, job, rec, env=None, **kw):
        return V.generate(job, self.home / "out.mp4", "p1", self.home, transport=rec, env=env if env is not None else {V.ENV[job["provider"]]: "sk-test-123"},
                          sleep=self.sleeps.append, poll_interval=1, **kw)
    def ledger(self): return B.read_ledger(self.home / "ledger.jsonl")


class XAIContract(Base):
    JOB = {"provider": "xai", "model": "grok-imagine-video-1.5", "prompt": "a paper boat in rain", "resolution": "720p", "seconds": 8, "aspect": "9:16"}

    def test_happy_path(self):
        rec = Recorder([("POST", "/v1/videos/generations", 200, {"request_id": "d97415a1"}),
                        ("GET", "/v1/videos/d97415a1", 200, {"status": "pending"}),
                        ("GET", "/v1/videos/d97415a1", 200, {"status": "done", "model": "grok-imagine-video-1.5",
                                                             "video": {"url": "https://vidgen.x.ai/a/video.mp4", "duration": 8, "respect_moderation": True}}),
                        ("GET", "vidgen.x.ai", 200, self.mp4)])
        r = self.run_job(dict(self.JOB, image=str(self.home / "still.png")), rec)
        sub = rec.calls[0]
        self.assertEqual(sub["url"], "https://api.x.ai/v1/videos/generations")
        self.assertEqual(sub["headers"]["Authorization"], "Bearer sk-test-123")
        self.assertEqual(sub["body"]["duration"], 8); self.assertEqual(sub["body"]["aspect_ratio"], "9:16"); self.assertEqual(sub["body"]["resolution"], "720p")
        self.assertTrue(sub["body"]["image"]["url"].startswith("data:image/png;base64,"))
        self.assertNotIn("Authorization", rec.calls[3]["headers"])  # temporary URL fetched without the key
        self.assertTrue((self.home / "out.mp4").exists()); self.assertAlmostEqual(r["usd_estimate"], 8 * 0.14 + 0.01)
        self.assertEqual(self.ledger()[0]["provider"], "xai"); self.assertNotIn("sk-test", (self.home / "ledger.jsonl").read_text())

    def test_failed_invalid_argument_not_retried(self):
        rec = Recorder([("POST", "generations", 200, {"request_id": "r1"}),
                        ("GET", "/v1/videos/r1", 200, {"status": "failed", "error": {"code": "invalid_argument", "message": "Prompt cannot be empty."}})])
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, rec)
        self.assertEqual(cm.exception.code, "invalid_argument"); self.assertEqual(len(rec.script), 0)
        self.assertEqual(len(self.ledger()), 1)  # submitted jobs are logged even if they fail

    def test_transient_submit_retries_capped(self):
        rec = Recorder([("POST", "generations", 503, {"error": {"code": "service_unavailable", "message": "busy"}})] * 3)
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, rec)
        self.assertTrue(cm.exception.retryable); self.assertEqual(len(rec.calls), 3); self.assertEqual(self.ledger(), [])

    def test_transient_then_success(self):
        rec = Recorder([("POST", "generations", 429, {"error": {"message": "rate limited"}}),
                        ("POST", "generations", 200, {"request_id": "r2"}),
                        ("GET", "/v1/videos/r2", 200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v.mp4", "respect_moderation": True}}),
                        ("GET", "vidgen", 200, self.mp4)])
        self.run_job(self.JOB, rec); self.assertEqual(len(self.sleeps), 1)

    def test_moderation_filtered(self):
        rec = Recorder([("POST", "generations", 200, {"request_id": "r3"}),
                        ("GET", "/v1/videos/r3", 200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v.mp4", "respect_moderation": False}})])
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, rec)
        self.assertEqual(cm.exception.code, "moderation")

    def test_auth_error_not_retried(self):
        rec = Recorder([("POST", "generations", 401, {"error": "Incorrect API key provided"})])
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, rec)
        self.assertFalse(cm.exception.retryable); self.assertEqual(len(rec.calls), 1)

    def test_timeout(self):
        rec = Recorder([("POST", "generations", 200, {"request_id": "r4"})] + [("GET", "/v1/videos/r4", 200, {"status": "pending"})] * 50)
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, rec, timeout=0)
        self.assertEqual(cm.exception.code, "timeout")

    def test_bad_media_rejected(self):
        rec = Recorder([("POST", "generations", 200, {"request_id": "r5"}),
                        ("GET", "/v1/videos/r5", 200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v.mp4", "respect_moderation": True}}),
                        ("GET", "vidgen", 200, b"<html>not a video</html>")])
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, rec)
        self.assertEqual(cm.exception.code, "bad_media"); self.assertFalse((self.home / "out.mp4").exists())

    def test_lite_and_classic_rules(self):
        pr = json.loads((ROOT / "config/pricing.json").read_text()); ref = str(self.home / "still.png")
        self.assertTrue(V.validate_job(dict(self.JOB, model="grok-imagine-video-1.5-lite", reference_images=[ref]), pr))
        self.assertTrue(V.validate_job(dict(self.JOB, model="grok-imagine-video", last_frame=ref), pr))
        self.assertTrue(V.validate_job(dict(self.JOB, aspect="4:5"), pr))
        self.assertEqual(V.validate_job(dict(self.JOB, model="grok-imagine-video-1.5", reference_images=[ref] * 14, last_frame=ref), pr), [])

    def test_body_shape_refs_and_silent(self):
        ref = str(self.home / "still.png")
        b = V.XAI("k").build(dict(self.JOB, reference_images=[ref, ref], last_frame=ref, audio=False))
        self.assertEqual(len(b["reference_images"]), 2); self.assertIn("url", b["last_frame"]); self.assertIs(b["generate_audio"], False)

    def test_reference_limits(self):
        self.assertTrue(V.validate_job(dict(self.JOB, resolution="1080p", reference_images=[str(self.home / "still.png")]), json.loads((ROOT / "config/pricing.json").read_text())))


class TTSContract(Base):
    def wav(self):
        p = self.home / "tone.wav"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=1", str(p)], check=True)
        return p.read_bytes()

    def run_tts(self, rec, text="Hello there, this is the narration.", env=None, out="n.mp3", **kw):
        return V.tts(text, self.home / out, "p1", self.home, transport=rec, sleep=self.sleeps.append,
                     env=env if env is not None else {"XAI_API_KEY": "sk-test-123"}, **kw)

    def test_happy_path_logs_chars_and_cost(self):
        rec = Recorder([("POST", "/v1/tts", 200, self.wav())])
        r = self.run_tts(rec, out="n.wav", codec="wav", voice_id="ara", replace={"Grok Bot": "Grock Bot"})
        c = rec.calls[0]
        self.assertEqual(c["url"], "https://api.x.ai/v1/tts"); self.assertTrue(c["raw"])
        self.assertEqual(c["headers"]["Authorization"], "Bearer sk-test-123")
        self.assertEqual(c["body"], {"text": "Hello there, this is the narration.", "voice_id": "ara", "language": "en",
                                     "output_format": {"codec": "wav"}, "replace": {"Grok Bot": "Grock Bot"}})
        self.assertAlmostEqual(r["usd_estimate"], round(15.0 * 35 / 1e6, 4))
        e = self.ledger()[0]; self.assertEqual((e["model"], e["chars"], e["generations"]), ("tts", 35, 0))
        self.assertNotIn("sk-test", (self.home / "ledger.jsonl").read_text()); self.assertTrue((self.home / "n.wav").exists())

    def test_retries_capped_and_nothing_logged(self):
        rec = Recorder([("POST", "/v1/tts", 503, {"error": "busy"})] * 3)
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(rec)
        self.assertTrue(cm.exception.retryable); self.assertEqual(len(rec.calls), 3); self.assertEqual(self.ledger(), [])

    def test_unknown_voice_not_retried(self):
        rec = Recorder([("POST", "/v1/tts", 404, {"error": "voice not found"})])
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(rec, voice_id="nobody")
        self.assertEqual(cm.exception.status, 404); self.assertEqual(len(rec.calls), 1)

    def test_gates(self):
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(Recorder([]), env={})
        self.assertEqual(cm.exception.code, "no_key")
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(Recorder([]), text=" ")
        self.assertEqual(cm.exception.code, "invalid_job")
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(Recorder([]), speed=2.0)
        self.assertEqual(cm.exception.code, "invalid_job")
        (self.home / "caps.json").write_text(json.dumps(B.make_caps(30, 200)))  # no dollar caps
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(Recorder([]))
        self.assertEqual(cm.exception.code, "over_budget")
        (self.home / "caps.json").unlink()
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(Recorder([]))
        self.assertEqual(cm.exception.code, "no_caps")

    def test_dollar_cap_blocks(self):
        (self.home / "caps.json").write_text(json.dumps(B.make_caps(30, 200, 0.001, 0.001)))
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(Recorder([]), text="x" * 1000)
        self.assertEqual(cm.exception.code, "over_budget")

    def test_garbage_audio_rejected(self):
        rec = Recorder([("POST", "/v1/tts", 200, b"not audio at all")])
        with self.assertRaises(V.ProviderError) as cm: self.run_tts(rec)
        self.assertEqual(cm.exception.code, "bad_media"); self.assertFalse((self.home / "n.mp3").exists())

    def test_cli(self):
        import io, contextlib, unittest.mock as um
        (self.home / "s.txt").write_text("Hi.")
        rec = Recorder([("POST", "/v1/tts", 200, self.wav())])
        buf = io.StringIO()
        with um.patch.object(V, "http", rec), um.patch.dict("os.environ", {"XAI_API_KEY": "sk-test-123"}), contextlib.redirect_stdout(buf):
            # default transport is bound at def time; patch tts's default via wrapper
            orig = V.tts
            with um.patch.object(V, "tts", lambda *a, **k: orig(*a, transport=rec, **k)):
                rc = V.main(["tts", str(self.home / "s.txt"), str(self.home / "c.wav"), "--project", "p1", "--home", str(self.home)])
        self.assertEqual(rc, 0, buf.getvalue()); self.assertTrue(json.loads(buf.getvalue())["ok"])
        self.assertEqual(rec.calls[0]["body"]["output_format"], {"codec": "wav"})


class Gates(Base):
    JOB = XAIContract.JOB
    def test_no_key(self):
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, Recorder([]), env={})
        self.assertEqual(cm.exception.code, "no_key")

    def test_over_budget_blocks_before_submit(self):
        B.log_entry(self.home / "ledger.jsonl", {"project": "p1", "provider": "xai", "model": "x", "usd": 4.5, "generations": 1})
        rec = Recorder([])
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, rec)
        self.assertEqual(cm.exception.code, "over_budget"); self.assertEqual(rec.calls, [])

    def _owner_pack(self, providers, trust="approved"):
        d = self.home / "owner"; (d / "refs").mkdir(parents=True); shutil.copy(self.home / "still.png", d / "refs/front.png")
        h = P.sha256(d / "refs/front.png")
        (d / "pack.json").write_text(json.dumps({"schema_version": 1, "name": "Me", "slug": "me", "version": 1, "type": "owner", "trust": "untrusted",
            "created_by": "Owner", "created_at": "2026-10-16T06:00:00+00:00", "lock_text": "The owner: adult, short dark hair, navy blazer.",
            "refs": [{"file": "refs/front.png", "view": "front", "sha256": h}], "voice": {"route": "owner-recording"}, "license": "owner",
            "provenance": {"route": "owner-upload"}, "consent_file": "consent.json"}))
        (d / "consent.json").write_text(json.dumps({"owner_name": "Owner", "date": "2026-10-16T06:00:00+00:00", "scope": "presenter", "providers": providers,
            "voice": True, "revocable": True, "ref_sha256": [h], "consent_sentence_match": 0.95}))
        P.screen(d, "videoReview", True, False, False)
        if trust == "approved": P.approve(d)
        return d

    def test_likeness_consent_must_name_xai(self):
        d = self._owner_pack(["builtin"])
        with self.assertRaises(V.ProviderError) as cm: self.run_job(dict(self.JOB, likeness_pack=str(d)), Recorder([]))
        self.assertIn("likeness pack invalid", str(cm.exception))

    def test_no_caps_blocks(self):
        (self.home / "caps.json").unlink()
        rec = Recorder([])
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, rec)
        self.assertEqual(cm.exception.code, "no_caps"); self.assertEqual(rec.calls, [])

    def test_no_dollar_caps_blocks_api(self):
        (self.home / "caps.json").write_text(json.dumps(B.make_caps(30, 200)))
        with self.assertRaises(V.ProviderError) as cm: self.run_job(self.JOB, Recorder([]))
        self.assertEqual(cm.exception.code, "over_budget")

    def test_only_xai_provider(self):
        with self.assertRaises(V.ProviderError) as cm: self.run_job(dict(self.JOB, provider="veo"), Recorder([]), env={"XAI_API_KEY": "k"})
        self.assertEqual(cm.exception.code, "invalid_job")

    def test_likeness_requires_approval(self):
        d = self._owner_pack(["xai"], trust="untrusted")
        with self.assertRaises(V.ProviderError) as cm: self.run_job(dict(self.JOB, likeness_pack=str(d)), Recorder([]))
        self.assertIn("not approved", str(cm.exception))

    def test_likeness_allowed(self):
        d = self._owner_pack(["xai"])
        rec = Recorder([("POST", "generations", 200, {"request_id": "ok"}),
                        ("GET", "/v1/videos/ok", 200, {"status": "done", "video": {"url": "https://vidgen.x.ai/v.mp4", "respect_moderation": True}}),
                        ("GET", "vidgen", 200, self.mp4)])
        self.run_job(dict(self.JOB, likeness_pack=str(d), reference_images=[str(d / "refs/front.png")]), rec)
        self.assertEqual(len(rec.calls[0]["body"]["reference_images"]), 1)


class RealHTTPTransport(unittest.TestCase):
    """Exercises the urllib transport against a local HTTP server: JSON, HTTP errors, redirects, raw bytes."""
    @classmethod
    def setUpClass(cls):
        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def _send(self, code, body, ctype="application/json", extra=None):
                self.send_response(code); self.send_header("Content-Type", ctype)
                for k, v in (extra or {}).items(): self.send_header(k, v)
                self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n))
                ok = self.headers.get("Authorization") == "Bearer k" and body.get("model")
                self._send(200 if ok else 401, json.dumps({"request_id": "x"} if ok else {"error": "bad key"}).encode())
            def do_GET(self):
                if self.path == "/redirect": self._send(302, b"", extra={"Location": "/file"})
                elif self.path == "/file": self._send(200, b"\x00\x01binary", "video/mp4")
                else: self._send(503, b'{"error":{"code":"service_unavailable"}}')
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()

    def test_json_and_errors(self):
        s, b = V.http("POST", self.base + "/v1/videos/generations", {"Authorization": "Bearer k"}, {"model": "m"})
        self.assertEqual((s, b), (200, {"request_id": "x"}))
        s, b = V.http("POST", self.base + "/v1/videos/generations", {"Authorization": "Bearer nope"}, {"model": "m"})
        self.assertEqual(s, 401)
        with self.assertRaises(V.ProviderError) as cm: V._check_http(s, b, "xai")
        self.assertFalse(cm.exception.retryable)
        s, b = V.http("GET", self.base + "/busy")
        with self.assertRaises(V.ProviderError) as cm: V._check_http(s, b, "xai")
        self.assertTrue(cm.exception.retryable)

    def test_redirect_raw_download(self):
        s, data = V.http("GET", self.base + "/redirect", raw=True)
        self.assertEqual((s, data), (200, b"\x00\x01binary"))

    def test_network_error_is_retryable(self):
        with self.assertRaises(V.ProviderError) as cm: V.http("GET", "http://127.0.0.1:9/none", timeout=2)
        self.assertTrue(cm.exception.retryable)


if __name__ == "__main__":
    unittest.main()
