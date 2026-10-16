#!/usr/bin/env python3
"""Grok Bot Video Studio: opt-in xAI Imagine API adapter (owner's own XAI_API_KEY).

  vstudio_providers.py generate JOB.json OUT.mp4 --project ID [--home /workspace/vstudio] [--timeout 900]

JOB.json: {"model": "grok-imagine-video-1.5"|"grok-imagine-video-1.5-lite"|"grok-imagine-video", "prompt": str,
           "resolution": "480p"|"720p"|"1080p", "seconds": 1-15, "aspect": "9:16", "image": path?, "last_frame": path?,
           "reference_images": [path, ...]?, "audio": bool?, "likeness_pack": dir?}

Flow: validate -> owner-likeness consent check -> budget check against the owner's caps -> submit (transient
errors retried, max 2) -> poll until done/failed/timeout -> download (retry, max 2) -> ffprobe verification ->
the ledger entry is written at submit with the estimated USD. The key is read only from XAI_API_KEY and is never
printed or logged.

Endpoints (docs.x.ai, checked 2026-10-16):
  POST https://api.x.ai/v1/videos/generations -> {"request_id"}
  GET  https://api.x.ai/v1/videos/{request_id} -> status pending|done|failed; done: video.url, video.respect_moderation;
       failed: error.code in invalid_argument|permission_denied|failed_precondition|service_unavailable|internal_error
LIVE VERIFICATION: pending until the owner supplies XAI_API_KEY (see RECOMMENDATIONS.md). Contract tests cover the
documented request/response shapes.
"""
import argparse, base64, json, mimetypes, os, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import vstudio_budget as budget
import vstudio_pack as pack

ENV = {"xai": "XAI_API_KEY"}
XAI_BASE = "https://api.x.ai"
XAI_ASPECTS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "5:2"}
MAX_RETRIES = 2


class ProviderError(Exception):
    def __init__(self, msg, code=None, retryable=False, status=None):
        super().__init__(msg); self.code = code; self.retryable = retryable; self.status = status


# ---------------- transport ----------------
def http(method, url, headers=None, body=None, timeout=60, raw=False):
    """Default transport. Returns (status, parsed JSON or bytes). Raises ProviderError on network failure."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read(); status = r.status
    except urllib.error.HTTPError as e:
        payload = e.read(); status = e.code
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ProviderError(f"network error: {type(e).__name__}", code="network", retryable=True)
    if raw and status < 400:
        return status, payload
    try:
        return status, json.loads(payload.decode() or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return status, {"_raw": payload[:200].decode("latin-1")}


def _check_http(status, body, provider):
    if status < 400:
        return
    retry = status == 429 or status >= 500
    msg = code = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg, code = err.get("message"), err.get("code")
        elif isinstance(err, str):
            msg = err
        msg = body.get("message") or msg
        code = body.get("code", code) if isinstance(body.get("code"), (int, str)) else code
    raise ProviderError(f"{provider} HTTP {status}: {msg or 'request failed'}", code=code, retryable=retry, status=status)


def _data_uri(path):
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    return f"data:{mime};base64," + base64.b64encode(Path(path).read_bytes()).decode()


def _b64(path):
    return base64.b64encode(Path(path).read_bytes()).decode()


# ---------------- adapters ----------------
class XAI:
    name = "xai"
    def __init__(self, key, transport=http, base=XAI_BASE):
        self.key, self.t, self.base = key, transport, base
    def _h(self): return {"Authorization": f"Bearer {self.key}"}
    def build(self, job):
        b = {"model": job["model"], "prompt": job.get("prompt", ""), "duration": int(job["seconds"]),
             "resolution": job["resolution"], "aspect_ratio": job.get("aspect", "16:9")}
        if job.get("image"): b["image"] = {"url": _data_uri(job["image"])}
        if job.get("last_frame"): b["last_frame"] = {"url": _data_uri(job["last_frame"])}
        if job.get("reference_images"): b["reference_images"] = [{"url": _data_uri(p)} for p in job["reference_images"]]
        if job.get("audio") is False: b["generate_audio"] = False
        return b
    def submit(self, job):
        s, body = self.t("POST", f"{self.base}/v1/videos/generations", self._h(), self.build(job))
        _check_http(s, body, self.name)
        if not body.get("request_id"): raise ProviderError("xai: no request_id in response")
        return body["request_id"]
    def poll(self, rid):
        s, body = self.t("GET", f"{self.base}/v1/videos/{urllib.parse.quote(rid)}", self._h(), None)
        _check_http(s, body, self.name)
        st = body.get("status")
        if st == "done":
            v = body.get("video") or {}
            if v.get("respect_moderation") is False: raise ProviderError("xai: video filtered by moderation", code="moderation")
            if not v.get("url"): raise ProviderError("xai: done without video url")
            return "done", v["url"]
        if st == "failed":
            err = body.get("error") or {}
            code = err.get("code")
            raise ProviderError(f"xai failed [{code}]: {err.get('message', '')}", code=code,
                                retryable=code in ("service_unavailable", "internal_error"))
        return "pending", None
    def download(self, url, out):
        s, data = self.t("GET", url, {}, None, raw=True)  # temporary public URL; no auth header
        _check_http(s, data if isinstance(data, dict) else {}, self.name)
        Path(out).write_bytes(data)


ADAPTERS = {"xai": XAI}


# ---------------- validation + orchestration ----------------
def validate_job(job, pricing):
    errs = []
    job.setdefault("provider", "xai")
    prov = job["provider"]
    if prov not in ADAPTERS: return ["provider must be xai (the only API provider)"]
    m = pricing["providers"]["xai"]["models"].get(job.get("model"))
    if not m or m["kind"] != "video": errs.append(f"unknown video model {job.get('model')!r}")
    if job.get("aspect", "16:9") not in XAI_ASPECTS: errs.append(f"aspect must be one of {sorted(XAI_ASPECTS)}")
    if job.get("model") == "grok-imagine-video-1.5-lite" and (job.get("reference_images") or job.get("last_frame")):
        errs.append("grok-imagine-video-1.5-lite takes no reference images or pinned last frame")
    if job.get("model") == "grok-imagine-video" and (job.get("last_frame") or (job.get("image") and job.get("reference_images"))):
        errs.append("grok-imagine-video rejects last_frame and image combined with references")
    if job.get("model") == "grok-imagine-video" and len(job.get("reference_images") or []) > 7:
        errs.append("grok-imagine-video takes at most 7 reference images")
    if not job.get("prompt") and not job.get("image"): errs.append("prompt required")
    for k in ("image", "last_frame"):
        if job.get(k) and not Path(job[k]).is_file(): errs.append(f"{k} file missing")
    for p in job.get("reference_images") or []:
        if not Path(p).is_file(): errs.append(f"reference image missing: {p}")
    if len(job.get("reference_images") or []) > 14: errs.append("at most 14 reference images")
    if job.get("reference_images") and job.get("resolution") == "1080p": errs.append("reference-to-video is max 720p")
    if not errs:
        try: budget.job_cost(pricing, {k: job[k] for k in ("provider", "model", "resolution", "seconds")})
        except (budget.BudgetError, KeyError) as e: errs.append(str(e))
    return errs


def likeness_check(job):
    """Owner likeness may only go to a provider listed in an approved owner pack's consent.json."""
    d = job.get("likeness_pack")
    if not d: return []
    r = pack.validate(d)
    if not r["ok"]: return [f"likeness pack invalid: {e}" for e in r["errors"]]
    p = json.loads((Path(d) / "pack.json").read_text())
    if p["type"] != "owner": return []
    if job["provider"] != "xai": return ["owner likeness runs on the xAI API only"]
    if p["trust"] != "approved": return ["owner pack is not approved"]
    c = json.loads((Path(d) / p.get("consent_file", "consent.json")).read_text())
    if job["provider"] not in c["providers"]: return [f"consent does not allow provider {job['provider']}"]
    return []


def _retry(fn, what, sleep):
    for attempt in range(MAX_RETRIES + 1):
        try:
            return fn()
        except ProviderError as e:
            if not e.retryable or attempt == MAX_RETRIES:
                raise
            sleep(min(30, 2 ** attempt * 2))


def generate(job, out, project, home, transport=http, env=None, sleep=time.sleep, poll_interval=10, timeout=900, probe=True):
    home = Path(home); env = os.environ if env is None else env
    pricing = budget.load_json(home / "config" / "pricing.json")
    caps = budget.load_caps(home / "caps.json")
    ledger = home / "ledger.jsonl"
    errs = validate_job(job, pricing) + likeness_check(job)
    if errs: raise ProviderError("; ".join(errs), code="invalid_job")
    if budget.validate_caps(caps): raise ProviderError("caps are not set; ask the owner for their generation and dollar caps", code="no_caps")
    key = env.get(ENV[job["provider"]])
    if not key: raise ProviderError(f"{ENV[job['provider']]} is not set; ask the owner to add it via a secret request", code="no_key")
    plan = {"jobs": [{k: job[k] for k in ("provider", "model", "resolution", "seconds")} | {"images_in": len(job.get("reference_images") or []) + bool(job.get("image")) + bool(job.get("last_frame"))}]}
    probs = budget.check(pricing, caps, budget.read_ledger(ledger), project, plan)
    if probs: raise ProviderError("budget: " + "; ".join(probs), code="over_budget")
    est = budget.estimate(pricing, plan)[job["provider"]]
    ad = ADAPTERS[job["provider"]](key, transport)
    rid = _retry(lambda: ad.submit(job), "submit", sleep)
    # the provider may bill from here on: log the estimated cost now (failed jobs are logged too)
    entry = budget.log_entry(ledger, {"project": project, "provider": job["provider"], "model": job["model"], "seconds": float(job["seconds"]),
                                      "usd": est["usd"], "generations": 1, "note": f"submitted {rid}"})
    deadline = time.monotonic() + timeout; url = None
    while True:
        state, url = _retry(lambda: ad.poll(rid), "poll", sleep)
        if state == "done": break
        if time.monotonic() > deadline: raise ProviderError(f"timed out after {timeout}s waiting for {rid}", code="timeout")
        sleep(poll_interval)
    tmp = Path(str(out) + ".part")
    _retry(lambda: ad.download(url, tmp), "download", sleep)
    if probe:
        from vstudio_media import probe as ffprobe
        try: info = ffprobe(tmp)
        except RuntimeError: tmp.unlink(missing_ok=True); raise ProviderError("downloaded file is not a playable video", code="bad_media")
        if not info["video"]: tmp.unlink(missing_ok=True); raise ProviderError("downloaded file has no video stream", code="bad_media")
    tmp.replace(out)
    return {"out": str(out), "id": rid, "usd_estimate": est["usd"], "ledger_ts": entry["ts"]}


TTS_CODECS = {"mp3", "wav"}
TTS_MAX_CHARS = 60000


def tts(text, out, project, home, voice_id="eve", language="en", codec="mp3", speed=1.0, replace=None,
        transport=http, env=None, sleep=time.sleep, probe=True):
    """xAI POST /v1/tts (docs.x.ai text-to-speech). Caps check, retries capped at MAX_RETRIES, ledger log, ffprobe check."""
    home = Path(home); env = os.environ if env is None else env
    pricing = budget.load_json(home / "config" / "pricing.json")
    caps = budget.load_caps(home / "caps.json")
    ledger = home / "ledger.jsonl"
    errs = []
    if not text or not text.strip(): errs.append("text required")
    if len(text) > TTS_MAX_CHARS: errs.append(f"text over {TTS_MAX_CHARS} characters; split it")
    if codec not in TTS_CODECS: errs.append(f"codec must be one of {sorted(TTS_CODECS)}")
    if not 0.7 <= float(speed) <= 1.5: errs.append("speed must be 0.7-1.5")
    if errs: raise ProviderError("; ".join(errs), code="invalid_job")
    if budget.validate_caps(caps): raise ProviderError("caps are not set; ask the owner for their generation and dollar caps", code="no_caps")
    key = env.get(ENV["xai"])
    if not key: raise ProviderError("XAI_API_KEY is not set; ask the owner to add it via a secret request", code="no_key")
    plan = {"jobs": [{"provider": "xai", "model": "tts", "chars": len(text)}]}
    probs = budget.check(pricing, caps, budget.read_ledger(ledger), project, plan)
    if probs: raise ProviderError("budget: " + "; ".join(probs), code="over_budget")
    usd = budget.estimate(pricing, plan)["xai"]["usd"]
    body = {"text": text, "voice_id": voice_id, "language": language, "output_format": {"codec": codec}}
    if float(speed) != 1.0: body["speed"] = float(speed)
    if replace: body["replace"] = replace
    def call():
        s, data = transport("POST", f"{XAI_BASE}/v1/tts", {"Authorization": f"Bearer {key}"}, body, raw=True)
        _check_http(s, data if isinstance(data, dict) else {}, "xai")
        if not isinstance(data, (bytes, bytearray)) or not data: raise ProviderError("xai tts: empty audio", code="bad_media")
        return bytes(data)
    audio = _retry(call, "tts", sleep)
    entry = budget.log_entry(ledger, {"project": project, "provider": "xai", "model": "tts", "chars": len(text),
                                      "usd": usd, "generations": 0, "note": f"tts {voice_id}"})
    tmp = Path(str(out) + ".part"); tmp.write_bytes(audio)
    if probe:
        from vstudio_media import probe as ffprobe
        try: info = ffprobe(tmp)
        except RuntimeError: tmp.unlink(missing_ok=True); raise ProviderError("tts output is not playable audio", code="bad_media")
        if not info["audio"]: tmp.unlink(missing_ok=True); raise ProviderError("tts output has no audio stream", code="bad_media")
    tmp.replace(out)
    return {"out": str(out), "chars": len(text), "usd_estimate": usd, "ledger_ts": entry["ts"]}


def main(argv=None):
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("tts"); t.add_argument("text_file"); t.add_argument("out"); t.add_argument("--project", required=True)
    t.add_argument("--home", default="/workspace/vstudio"); t.add_argument("--voice", default="eve"); t.add_argument("--language", default="en")
    t.add_argument("--speed", type=float, default=1.0); t.add_argument("--replace", help="JSON file of pronunciation replacements")
    g = sub.add_parser("generate"); g.add_argument("job"); g.add_argument("out"); g.add_argument("--project", required=True)
    g.add_argument("--home", default="/workspace/vstudio"); g.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "tts":
            codec = "wav" if str(a.out).endswith(".wav") else "mp3"
            r = tts(Path(a.text_file).read_text().strip(), a.out, a.project, a.home, a.voice, a.language, codec, a.speed,
                    json.loads(Path(a.replace).read_text()) if a.replace else None)
        else:
            r = generate(json.loads(Path(a.job).read_text()), a.out, a.project, a.home, timeout=a.timeout)
    except ProviderError as e:
        print(json.dumps({"ok": False, "code": e.code, "error": str(e)})); return 1
    print(json.dumps({"ok": True, **r})); return 0


if __name__ == "__main__":
    sys.exit(main())
