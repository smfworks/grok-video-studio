#!/usr/bin/env python3
"""Grok Bot Video Studio: character-pack and consent validation.

  vstudio_pack.py validate PACK_DIR            -> JSON {ok, errors, trust}
  vstudio_pack.py hash PACK_DIR                -> rewrite refs[].sha256 from files (owner-created packs only)
  vstudio_pack.py screen PACK_DIR --by WHO --real-person yes|no --minor yes|no --sexual yes|no
                                               -> record a screening result; any "yes" refuses the pack
  vstudio_pack.py approve PACK_DIR             -> trust=approved (only after a clean screening, call after the A2 yes)

Rules enforced: schema; refs exist and match sha256; fictional packs must carry a clean screening
before approval; owner packs need a valid consent.json whose ref hashes match; images must be real
PNG/JPEG/WEBP files.
"""
import argparse, datetime as dt, hashlib, json, re, sys
from pathlib import Path

VIEWS = {"front", "three-quarter", "profile", "full-body", "expressions", "other"}
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
MAGIC = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"RIFF")
CONSENT_KEYS = {"owner_name", "date", "scope", "providers", "voice", "revocable", "ref_sha256", "consent_sentence_match"}
PROVIDERS = {"xai"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_image(path):
    head = Path(path).read_bytes()[:12]
    return head.startswith(MAGIC[0]) or head.startswith(MAGIC[1]) or (head.startswith(MAGIC[2]) and head[8:12] == b"WEBP")


def _date(s):
    try:
        dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


def validate(pack_dir):
    d = Path(pack_dir)
    errs = []
    pj = d / "pack.json"
    if not pj.exists():
        return {"ok": False, "errors": ["pack.json missing"], "trust": None}
    try:
        p = json.loads(pj.read_text())
    except json.JSONDecodeError as ex:
        return {"ok": False, "errors": [f"pack.json is not valid JSON: {ex}"], "trust": None}
    req = ["schema_version", "name", "slug", "version", "type", "trust", "created_by", "created_at", "lock_text", "refs", "voice", "license", "provenance"]
    for k in req:
        if k not in p:
            errs.append(f"missing field {k}")
    if errs:
        return {"ok": False, "errors": errs, "trust": p.get("trust")}
    if p["schema_version"] != 1: errs.append("schema_version must be 1")
    if not isinstance(p["name"], str) or not 1 <= len(p["name"]) <= 80: errs.append("name must be 1-80 chars")
    if not isinstance(p["slug"], str) or not SLUG.match(p["slug"]): errs.append("slug must match ^[a-z0-9][a-z0-9-]{0,47}$")
    if not isinstance(p["version"], int) or p["version"] < 1: errs.append("version must be an integer >= 1")
    if p["type"] not in ("fictional", "owner"): errs.append("type must be fictional or owner")
    if p["trust"] not in ("untrusted", "approved"): errs.append("trust must be untrusted or approved")
    if not _date(p["created_at"]): errs.append("created_at must be ISO 8601")
    if not isinstance(p["lock_text"], str) or not 20 <= len(p["lock_text"]) <= 1200: errs.append("lock_text must be 20-1200 chars")
    if p.get("voice", {}).get("route") not in ("builtin", "xai-preset", "owner-recording"): errs.append("voice.route invalid")
    if p["type"] == "fictional" and p["voice"].get("route") == "owner-recording": errs.append("fictional packs cannot use an owner recording as voice")
    if p.get("provenance", {}).get("route") not in ("builtin", "xai", "owner-upload", "import"): errs.append("provenance.route invalid")
    refs = p["refs"]
    if not isinstance(refs, list) or not 1 <= len(refs) <= 14:
        errs.append("refs must list 1-14 images")
        refs = []
    for r in refs:
        f = d / str(r.get("file", ""))
        if r.get("view") not in VIEWS: errs.append(f"ref {r.get('file')}: bad view")
        if not f.is_file() or d.resolve() not in f.resolve().parents:
            errs.append(f"ref {r.get('file')}: file missing or outside pack"); continue
        if not _is_image(f): errs.append(f"ref {r.get('file')}: not a PNG/JPEG/WEBP image")
        if r.get("sha256") != sha256(f): errs.append(f"ref {r.get('file')}: sha256 mismatch")
    scr = p.get("screening")
    if scr is not None:
        for k in ("by", "at", "real_person", "minor", "sexual"):
            if k not in scr: errs.append(f"screening.{k} missing")
        if p["type"] == "fictional" and scr.get("real_person"): errs.append("refused: screening found a real person in a fictional pack")
        if scr.get("minor"): errs.append("refused: screening found a minor")
        if scr.get("sexual"): errs.append("refused: screening found sexual content")
    if p["trust"] == "approved" and scr is None:
        errs.append("approved packs need a screening record")
    if p["type"] == "owner":
        errs += _consent_errors(d, p, refs)
    return {"ok": not errs, "errors": errs, "trust": p["trust"]}


def _consent_errors(d, p, refs):
    cf = d / p.get("consent_file", "consent.json")
    if not cf.is_file():
        return ["owner pack requires consent.json"]
    try:
        c = json.loads(cf.read_text())
    except json.JSONDecodeError:
        return ["consent.json is not valid JSON"]
    errs = [f"consent.json missing {k}" for k in sorted(CONSENT_KEYS - c.keys())]
    if errs: return errs
    if c["owner_name"] != p["created_by"]: errs.append("consent owner_name must equal pack created_by (owner only)")
    if not _date(c["date"]): errs.append("consent date must be ISO 8601")
    if c["revocable"] is not True: errs.append("consent must be revocable")
    if not isinstance(c["providers"], list) or set(c["providers"]) != PROVIDERS: errs.append("consent providers must be exactly ['xai'] (the xAI API; never builtin)")
    if p.get("provenance", {}).get("route") not in ("owner-upload",): errs.append("owner pack refs must be owner uploads")
    if sorted(c["ref_sha256"]) != sorted(r.get("sha256") for r in refs): errs.append("consent ref_sha256 does not match pack refs")
    if not isinstance(c["consent_sentence_match"], (int, float)) or c["consent_sentence_match"] < 0.85: errs.append("voice consent sentence match must be >= 0.85")
    return errs


def rehash(pack_dir):
    d = Path(pack_dir); pj = d / "pack.json"; p = json.loads(pj.read_text())
    for r in p["refs"]:
        r["sha256"] = sha256(d / r["file"])
    p["trust"] = "untrusted"; p.pop("screening", None)
    pj.write_text(json.dumps(p, indent=2) + "\n")
    return p


def screen(pack_dir, by, real_person, minor, sexual):
    d = Path(pack_dir); pj = d / "pack.json"; p = json.loads(pj.read_text())
    p["screening"] = {"by": by, "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                      "real_person": real_person, "minor": minor, "sexual": sexual}
    p["trust"] = "untrusted"
    pj.write_text(json.dumps(p, indent=2) + "\n")
    return validate(d)


def approve(pack_dir):
    d = Path(pack_dir); pj = d / "pack.json"; p = json.loads(pj.read_text())
    if "screening" not in p:
        return {"ok": False, "errors": ["run screen before approve"], "trust": p.get("trust")}
    res = validate(d)
    if not res["ok"]:
        return res
    p["trust"] = "approved"
    pj.write_text(json.dumps(p, indent=2) + "\n")
    return validate(d)


def _yn(s):
    if s not in ("yes", "no"): raise argparse.ArgumentTypeError("yes or no")
    return s == "yes"


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("validate", "hash", "approve"):
        sub.add_parser(n).add_argument("pack")
    s = sub.add_parser("screen"); s.add_argument("pack"); s.add_argument("--by", required=True)
    for k in ("--real-person", "--minor", "--sexual"): s.add_argument(k, type=_yn, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "validate": r = validate(a.pack)
    elif a.cmd == "hash": rehash(a.pack); r = validate(a.pack)
    elif a.cmd == "screen": r = screen(a.pack, a.by, a.real_person, a.minor, a.sexual)
    else: r = approve(a.pack)
    print(json.dumps(r, indent=2))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
