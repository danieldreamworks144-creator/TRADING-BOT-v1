"""Advisor: source strategy ideas from higher-capability models and outside
research -- then hand them to the validation gate, never straight to the bot.

The contract is deliberately one-way: the advisor may PROPOSE, only the
backtest validator (improve.py) may ADOPT. A convincing paragraph from a
smarter model is still just a paragraph until it survives walk-forward.

Two modes:
- offline (default): curate candidate specs from local research notes
  (ideas/*.md) into structured JSON the validator can test.
- remote (optional): if GIDEON_ADVISOR_URL or OPENAI_API_KEY/ANTHROPIC_API_KEY
  is set, POST a structured brief and parse the reply into the same schema.
  Failures degrade to offline mode -- the pipeline never blocks on the network.
"""
from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

IDEAS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ideas")


@dataclass
class Candidate:
    name: str
    hypothesis: str
    family: str                  # trend | reversion | breakout | always
    entry: str
    exit: str
    regime: str
    failure_mode: str
    source: str                  # where the idea came from (model / URL / file)
    params: dict = field(default_factory=dict)
    status: str = "proposed"     # proposed -> validated | rejected (improve.py decides)


def build_brief(performance: dict, gaps: list) -> str:
    """Structured brief asking a stronger model for ideas that FIX the gaps,
    not for ideas that sound exciting. Anchored on what is actually failing."""
    return (
        "You are advising a risk-first ETH/USDC spot trading research bot.\n"
        "It NEVER uses leverage, martingale, or grid schemes.\n\n"
        f"Current ensemble performance (out-of-sample): {json.dumps(performance, indent=2)}\n\n"
        f"Known gaps to solve: {json.dumps(gaps, indent=2)}\n\n"
        "Propose 2-4 strategy ideas that specifically address the gaps. "
        "For each, return STRICT JSON with keys: name, hypothesis, family "
        "(trend|reversion|breakout|always), entry, exit, regime, failure_mode, "
        "params (object). Be concrete: indicator names and parameter values. "
        "Include the failure_mode -- every strategy must name where it loses money."
    )


# -- remote sourcing -------------------------------------------------------
def _post_json(url: str, payload: dict, headers: dict) -> dict | None:
    try:
        import requests  # optional dependency
        r = requests.post(url, json=payload, headers=headers, timeout=45)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def consult_remote(brief: str) -> list:
    """Ask a stronger model for candidates. Returns [] if unconfigured or the
    call fails -- the pipeline must never hard-depend on this."""
    url = os.environ.get("GIDEON_ADVISOR_URL")
    if url:
        resp = _post_json(url, {"brief": brief}, {})
        return _parse_candidates(resp, source=f"remote:{url}") if resp else []
    for env, api in (("OPENAI_API_KEY", "https://api.openai.com/v1/chat/completions"),
                     ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/messages")):
        key = os.environ.get(env)
        if not key:
            continue
        if env == "OPENAI_API_KEY":
            resp = _post_json(api, {
                "model": os.environ.get("GIDEON_ADVISOR_MODEL", "gpt-4o"),
                "messages": [{"role": "user", "content": brief}],
                "response_format": {"type": "json_object"},
            }, {"Authorization": f"Bearer {key}"})
            text = (resp or {}).get("choices", [{}])[0].get("message", {}).get("content", "")
        else:
            resp = _post_json(api, {
                "model": os.environ.get("GIDEON_ADVISOR_MODEL", "claude-sonnet-4-5"),
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": brief}],
            }, {"x-api-key": key, "anthropic-version": "2023-06-01"})
            text = (resp or {}).get("content", [{}])[0].get("text", "")
        return _parse_candidates({"candidates_text": text}, source=f"model:{env}")
    return []


def _parse_candidates(resp: dict, source: str) -> list:
    """Pull JSON objects out of a model reply (raw list, dict, or fenced text)."""
    raw = resp.get("candidates") or resp.get("candidates_text") or ""
    if isinstance(raw, list):
        objs = raw
    else:
        objs = []
        for chunk in re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)'*\}", str(raw), re.S):
            try:
                objs.append(json.loads(chunk))
            except json.JSONDecodeError:
                continue
    out = []
    for o in objs:
        if not isinstance(o, dict) or not o.get("name"):
            continue
        out.append(Candidate(
            name=str(o["name"])[:80],
            hypothesis=str(o.get("hypothesis", ""))[:500],
            family=o.get("family", "always") if o.get("family") in
            ("trend", "reversion", "breakout", "always") else "always",
            entry=str(o.get("entry", ""))[:500],
            exit=str(o.get("exit", ""))[:500],
            regime=str(o.get("regime", ""))[:200],
            failure_mode=str(o.get("failure_mode", ""))[:500],
            source=source,
            params=o.get("params") or {},
        ))
    return out


# -- offline curation ------------------------------------------------------
def curate_local() -> list:
    """Turn local research notes (ideas/*.md) into candidate stubs. Titles and
    'failure mode' lines are captured so the honesty fields survive."""
    out = []
    for path in sorted(glob.glob(os.path.join(IDEAS_DIR, "*.md"))):
        text = open(path, encoding="utf-8", errors="replace").read()
        for sec in re.split(r"\n#{2,3}\s+", text):
            title = sec.split("\n", 1)[0].strip()
            if not title or len(title) > 100 or title.lower().startswith(("ideas", "research", "engine")):
                continue
            fail = ""
            m = re.search(r"failure mode[:\s]+(.+)", sec, re.I)
            if m:
                fail = m.group(1).strip()[:300]
            out.append(Candidate(
                name=title, hypothesis=sec.split("\n", 1)[-1].strip()[:300],
                family="always", entry="", exit="", regime="",
                failure_mode=fail, source=f"file:{os.path.basename(path)}"))
    return out


def gather_candidates(brief: str | None = None, gaps: list | None = None,
                      performance: dict | None = None) -> list:
    """Remote first (higher models), local curation as backfill. Always returns
    candidates with a source tag -- nothing enters the system unsourced."""
    brief = brief or build_brief(performance or {}, gaps or ["improve robustness"])
    remote = consult_remote(brief)
    if remote:
        return remote
    return curate_local()


def save_candidates(cands: list, path: str | None = None) -> str:
    os.makedirs(IDEAS_DIR, exist_ok=True)
    path = path or os.path.join(
        IDEAS_DIR, f"candidates-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")
    with open(path, "w") as f:
        json.dump([asdict(c) for c in cands], f, indent=2)
    return path
