#!/usr/bin/env python3
"""Grok Bot Video Studio: pricing estimates, ledger and budget caps.

Usage:
  vstudio_budget.py estimate --pricing P --plan plan.json
  vstudio_budget.py check    --pricing P --caps caps.json --ledger L --project ID --plan plan.json
  vstudio_budget.py log      --ledger L --project ID --provider X --model M [--seconds S] [--images N] [--usd U] [--note T]
  vstudio_budget.py summary  --ledger L [--month YYYY-MM]
  vstudio_budget.py demo     --pricing P --ledger L          -> remaining free demo generations
  vstudio_budget.py set-caps --caps caps.json --gen-video N --gen-month N [--usd-video X --usd-month X]

There are no default caps: the owner sets them at setup. Without a valid caps.json, `check` refuses every
job (exit 3). Dollar caps are required only before the xAI API is used.

plan.json "jobs": [{"provider","model","resolution"?,"seconds"?,"count"?,"images_in"?,"chars"?}]
"""
import argparse, datetime as dt, json, sys
from pathlib import Path


class BudgetError(ValueError):
    pass


def load_json(path):
    return json.loads(Path(path).read_text())


def job_cost(pricing, job):
    prov = pricing["providers"].get(job["provider"])
    if prov is None:
        raise BudgetError(f"unknown provider {job['provider']!r}")
    m = prov["models"].get(job["model"])
    if m is None:
        raise BudgetError(f"unknown model {job['model']!r} for {job['provider']}")
    count = int(job.get("count", 1))
    if count < 1:
        raise BudgetError("count must be >= 1")
    if prov["billing"] == "generations":
        return {"usd": 0.0, "generations": count, "seconds": float(job.get("seconds", 0)) * count}
    usd = 0.0
    seconds = 0.0
    if m["kind"] == "video":
        res = job.get("resolution")
        if res not in m["per_second"]:
            raise BudgetError(f"{job['model']} has no {res!r} tier (has {sorted(m['per_second'])})")
        sec = float(job["seconds"])
        if "allowed_seconds" in m and sec not in m["allowed_seconds"]:
            raise BudgetError(f"{job['model']} allows {m['allowed_seconds']} s, got {sec:g}")
        lo = m.get("min_seconds", 1)
        if "max_seconds" in m and not (lo <= sec <= m["max_seconds"]):
            raise BudgetError(f"{job['model']} allows {lo}-{m['max_seconds']} s, got {sec:g}")
        seconds = sec * count
        usd = m["per_second"][res] * seconds
    elif m["kind"] == "image":
        usd = m["per_image"] * count
    elif m["kind"] == "tts":
        usd = m["per_million_chars"] * int(job["chars"]) / 1e6 * count
        return {"usd": round(usd, 4), "generations": 0, "seconds": 0.0}  # voice is metered in dollars, not generations
    usd += m.get("image_input", 0.0) * int(job.get("images_in", 0)) * count
    return {"usd": round(usd, 4), "generations": count, "seconds": seconds}


def estimate(pricing, plan):
    by = {}
    for job in plan["jobs"]:
        c = job_cost(pricing, job)
        b = by.setdefault(job["provider"], {"usd": 0.0, "generations": 0, "seconds": 0.0})
        for k in b:
            b[k] = round(b[k] + c[k], 4)
    return by


def read_ledger(path):
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def log_entry(path, entry):
    entry = dict(entry)
    entry.setdefault("ts", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    with open(path, "a") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    return entry


def spent(entries, project=None, month=None):
    out = {}
    for e in entries:
        if project and e.get("project") != project:
            continue
        if month and not e["ts"].startswith(month):
            continue
        b = out.setdefault(e["provider"], {"usd": 0.0, "generations": 0})
        b["usd"] = round(b["usd"] + float(e.get("usd", 0.0)), 4)
        b["generations"] += int(e.get("generations", 1))
    return out


GEN_KEYS = ("generations_per_video", "generations_per_month")
USD_KEYS = ("usd_per_video", "usd_per_month")
NO_CAP_FLAGS = {"gen-video": "generations_per_video", "gen-month": "generations_per_month",
                "usd-video": "usd_per_video", "usd-month": "usd_per_month"}
NO_DOLLAR_CAP_PHRASE = "NO DOLLAR CAP"


def _no_cap(caps, k):
    """True when the owner explicitly chose no cap for meter k (recorded with a date)."""
    c = (caps.get("no_cap") or {}).get(k)
    return isinstance(c, dict) and bool(c.get("chosen_at"))


def validate_caps(caps):
    """Return problems with an owner-set caps dict ([] = usable)."""
    if not isinstance(caps, dict) or caps.get("set_by") != "owner":
        return ["caps are not set; ask the owner for their generation and dollar caps"]
    probs = []
    for k in GEN_KEYS:
        v = caps.get(k)
        if _no_cap(caps, k):
            if v is not None: probs.append(f"{k} is both capped and marked no cap")
        elif not isinstance(v, int) or isinstance(v, bool) or v < 1:
            probs.append(f"{k} must be a positive whole number set by the owner, or an explicit no-cap choice")
    for k in USD_KEYS:
        v = caps.get(k)
        if _no_cap(caps, k):
            if v is not None: probs.append(f"{k} is both capped and marked no cap")
            if caps["no_cap"][k].get("confirmation") != NO_DOLLAR_CAP_PHRASE:
                probs.append(f"no cap on {k} needs the owner's typed confirmation {NO_DOLLAR_CAP_PHRASE!r}")
        elif v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0):
            probs.append(f"{k} must be a positive number")
    if isinstance(caps.get("usd_per_video"), (int, float)) and isinstance(caps.get("usd_per_month"), (int, float)) \
            and caps["usd_per_video"] > caps["usd_per_month"]:
        probs.append("usd_per_video cannot exceed usd_per_month")
    return probs


def make_caps(gen_video, gen_month, usd_video=None, usd_month=None, no_cap=(), confirmation=None, now=None):
    """Build owner caps. no_cap lists meters (keys of NO_CAP_FLAGS or field names) the owner explicitly chose not to cap;
    a dollar meter also needs confirmation == NO_DOLLAR_CAP_PHRASE typed by the owner."""
    ts = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
    caps = {"set_by": "owner", "set_at": ts, "generations_per_video": gen_video, "generations_per_month": gen_month}
    if usd_video is not None: caps["usd_per_video"] = usd_video
    if usd_month is not None: caps["usd_per_month"] = usd_month
    for n in no_cap:
        k = NO_CAP_FLAGS.get(n, n)
        if k not in GEN_KEYS + USD_KEYS: raise BudgetError(f"unknown meter {n!r}")
        caps["no_cap"] = caps.get("no_cap", {})
        caps["no_cap"][k] = {"chosen_at": ts, "choice": "owner explicitly chose no cap"}
        if k in USD_KEYS: caps["no_cap"][k]["confirmation"] = confirmation
    probs = validate_caps(caps)
    if probs: raise BudgetError("; ".join(probs))
    return caps


def check(pricing, caps, entries, project, plan, now=None):
    """Return list of human-readable cap violations ([] means OK)."""
    cp = validate_caps(caps)
    if cp:
        return cp
    month = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m")
    est = estimate(pricing, plan)
    proj = spent(entries, project=project)
    mon = spent(entries, month=month)
    problems = []
    new = sum(v["generations"] for v in est.values())
    gen_proj = sum(v["generations"] for v in proj.values()) + new
    gen_mon = sum(v["generations"] for v in mon.values()) + new
    if not _no_cap(caps, "generations_per_video") and gen_proj > caps["generations_per_video"]:
        problems.append(f"generations for this video {gen_proj} > cap {caps['generations_per_video']}")
    if not _no_cap(caps, "generations_per_month") and gen_mon > caps["generations_per_month"]:
        problems.append(f"generations this month {gen_mon} > cap {caps['generations_per_month']}")
    usd_new = sum(e["usd"] for p, e in est.items() if pricing["providers"][p]["billing"] == "usd")
    if usd_new > 0:
        set_or_none = [caps.get(k) is not None or _no_cap(caps, k) for k in USD_KEYS]
        if not all(set_or_none):
            problems.append("no dollar caps set; ask the owner for per-video and per-month dollar caps before using the xAI API")
        else:
            pv = sum(v["usd"] for v in proj.values()) + usd_new
            pm = sum(v["usd"] for v in mon.values()) + usd_new
            if not _no_cap(caps, "usd_per_video") and pv > caps["usd_per_video"] + 1e-9:
                problems.append(f"${pv:.2f} for this video > cap ${caps['usd_per_video']:.2f}")
            if not _no_cap(caps, "usd_per_month") and pm > caps["usd_per_month"] + 1e-9:
                problems.append(f"${pm:.2f} this month > cap ${caps['usd_per_month']:.2f}")
    return problems


def demo_remaining(pricing, entries):
    """Free first-run allowance (no caps needed): limits.demo videos/images, counted from project 'demo'."""
    lim = pricing["limits"]["demo"]
    used = {"video": 0, "image": 0}
    for e in entries:
        if e.get("project") == "demo" and e.get("provider") == "builtin":
            kind = pricing["providers"]["builtin"]["models"].get(e.get("model"), {}).get("kind")
            if kind in used: used[kind] += int(e.get("generations", 1))
    return {"video": max(lim["videos"] - used["video"], 0), "image": max(lim["images"] - used["image"], 0)}


def load_caps(path):
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else {}


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("estimate"); e.add_argument("--pricing", required=True); e.add_argument("--plan", required=True)
    c = sub.add_parser("check")
    for a in ("--pricing", "--caps", "--ledger", "--project", "--plan"):
        c.add_argument(a, required=True)
    l = sub.add_parser("log")
    for a in ("--ledger", "--project", "--provider", "--model"):
        l.add_argument(a, required=True)
    l.add_argument("--seconds", type=float, default=0.0); l.add_argument("--images", type=int, default=0)
    l.add_argument("--usd", type=float, default=0.0); l.add_argument("--generations", type=int, default=1)
    l.add_argument("--note", default="")
    s = sub.add_parser("summary"); s.add_argument("--ledger", required=True); s.add_argument("--month")
    dm = sub.add_parser("demo"); dm.add_argument("--pricing", required=True); dm.add_argument("--ledger", required=True)
    sc = sub.add_parser("set-caps"); sc.add_argument("--caps", required=True)
    sc.add_argument("--gen-video", type=int); sc.add_argument("--gen-month", type=int)
    sc.add_argument("--no-cap", action="append", default=[], choices=sorted(NO_CAP_FLAGS),
                    help="record the owner's explicit choice of no cap for this meter (repeatable)")
    sc.add_argument("--confirm-no-dollar-cap", help=f"the owner's typed confirmation, exactly {NO_DOLLAR_CAP_PHRASE!r}")
    sc.add_argument("--usd-video", type=float); sc.add_argument("--usd-month", type=float)
    a = ap.parse_args(argv)
    try:
        if a.cmd == "estimate":
            print(json.dumps(estimate(load_json(a.pricing), load_json(a.plan)), indent=2))
        elif a.cmd == "check":
            probs = check(load_json(a.pricing), load_caps(a.caps), read_ledger(a.ledger), a.project, load_json(a.plan))
            print(json.dumps({"ok": not probs, "problems": probs}, indent=2))
            return 0 if not probs else 3
        elif a.cmd == "log":
            print(json.dumps(log_entry(a.ledger, {"project": a.project, "provider": a.provider, "model": a.model,
                  "seconds": a.seconds, "images": a.images, "usd": a.usd, "generations": a.generations, "note": a.note})))
        elif a.cmd == "demo":
            print(json.dumps(demo_remaining(load_json(a.pricing), read_ledger(a.ledger))))
        elif a.cmd == "set-caps":
            caps = make_caps(a.gen_video, a.gen_month, a.usd_video, a.usd_month, a.no_cap, a.confirm_no_dollar_cap)
            Path(a.caps).write_text(json.dumps(caps, indent=2) + "\n"); print(json.dumps(caps, indent=2))
        elif a.cmd == "summary":
            print(json.dumps(spent(read_ledger(a.ledger), month=a.month), indent=2))
    except (BudgetError, KeyError) as ex:
        print(f"error: {ex}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
