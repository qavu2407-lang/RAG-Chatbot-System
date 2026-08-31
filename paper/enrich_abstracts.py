#!/usr/bin/env python3
"""
enrich_abstracts.py — fill missing abstracts in records.csv from three providers.

WHY THIS EXISTS
---------------
Scopus's search endpoint omits abstracts and its Abstract Retrieval API is
subscription-gated (401 on META_ABS/FULL without an institutional entitlement).
No single free source covers the gap, so this runs three providers in sequence,
each only on the records still missing an abstract after the previous one.

    1. OpenAlex   batched 50 DOIs/request. Broad but shallow: it holds an
                  abstract for only a minority of the records that reach here.
    2. Crossref   one DOI/request. Measured 27% over the residual gap, and far
                  higher for preprint registrants — SSRN (10.2139) and Research
                  Square (10.21203) return an abstract almost every time.
                  NOTE: Crossref was removed as a SEARCH source because it has no
                  boolean operators. DOI metadata lookup is a different
                  capability and is unaffected by that limitation.
    3. Springer   one DOI/request, 10.1007 only. Neither Crossref nor OpenAlex
                  carries Springer abstracts; Springer's own Meta API does.
                  Free tier rejects batched `OR` queries (HTTP 403).

Providers are ordered cheapest-and-broadest first so later passes see the
smallest possible candidate set.

Nothing is invented. A record with no abstract at any provider stays empty, and
`abstract_source` records which provider supplied each abstract.

USAGE
-----
    python paper/enrich_abstracts.py --email you@example.com
    python paper/enrich_abstracts.py --email you@example.com --dry-run
    python paper/enrich_abstracts.py --providers crossref springer
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

ROOT = Path(__file__).resolve().parent
RECORDS = ROOT / "out" / "records.csv"
REPORT = ROOT / "out" / "enrichment_report.json"

# mailto only requests a polite pool; keys are what bill the right account.
_ENV = next((c for c in (ROOT.parent / ".env", ROOT.parent / "temp" / ".env")
             if c.exists()), None)
if _ENV:
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip().upper(), _v.strip().strip("\"'"))

OPENALEX_KEY = os.environ.get("OPENALEX_API_KEY", "").strip()
SPRINGER_KEY = os.environ.get("SPRINGER_META_API_KEY", "").strip()

OA_BATCH = 50
DELAY = {"openalex": 0.5, "crossref": 0.1, "springer": 1.0}

_TAG = re.compile(r"<[^>]+>")
_JATS = re.compile(r"^\s*(abstract|summary)\s*", re.I)


def clean_abstract(text: str) -> str:
    """Strip JATS/HTML markup Crossref and Springer wrap abstracts in."""
    if not text:
        return ""
    text = _TAG.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return _JATS.sub("", text).strip()


def invert(index: dict[str, list[int]] | None) -> str:
    """Rebuild running text from OpenAlex's {word: [positions]} inverted index."""
    if not index:
        return ""
    return " ".join(w for _, w in sorted(
        (pos, word) for word, poss in index.items() for pos in poss))


def pass_openalex(session, todo, email, state):
    """Batched DOI lookup. Cheapest per record, so it runs first."""
    dois = [r["doi_or_arxiv_id"].strip().lower() for r in todo]
    by_doi = {d: r for d, r in zip(dois, todo)}
    for i in range(0, len(dois), OA_BATCH):
        chunk = dois[i:i + OA_BATCH]
        params = {"filter": "doi:" + "|".join(chunk), "per-page": OA_BATCH,
                  "select": "doi,abstract_inverted_index"}
        if email:
            params["mailto"] = email
        if OPENALEX_KEY:
            params["api_key"] = OPENALEX_KEY
        try:
            resp = session.get("https://api.openalex.org/works", params=params,
                               timeout=60)
        except requests.RequestException:
            state["errors"] += 1
            continue
        if resp.status_code == 429:
            state["stopped"] = "openalex: budget exhausted"
            return
        if not resp.ok:
            state["errors"] += 1
            continue
        body = resp.json()
        state["cost"] += body.get("meta", {}).get("cost_usd", 0.0) or 0.0
        for work in body.get("results", []):
            doi = (work.get("doi") or "").lower().replace("https://doi.org/", "")
            rec = by_doi.get(doi)
            if rec and not rec["abstract"].strip():
                text = invert(work.get("abstract_inverted_index"))
                if text:
                    rec["abstract"] = text
                    rec["abstract_source"] = "OpenAlex"
                    state["filled"] += 1
        state["done"] = min(i + OA_BATCH, len(dois))
        yield
        time.sleep(DELAY["openalex"])


def pass_crossref(session, todo, email, state):
    """One DOI per request. Strong on preprint registrants (SSRN, Research Square)."""
    for n, rec in enumerate(todo, 1):
        params = {"mailto": email} if email else {}
        try:
            resp = session.get(
                "https://api.crossref.org/works/" + rec["doi_or_arxiv_id"].strip(),
                params=params, timeout=30)
        except requests.RequestException:
            state["errors"] += 1
            continue
        if resp.ok:
            text = clean_abstract(resp.json().get("message", {}).get("abstract", ""))
            if text:
                rec["abstract"] = text
                rec["abstract_source"] = "Crossref"
                state["filled"] += 1
        state["done"] = n
        yield
        time.sleep(DELAY["crossref"])


def pass_springer(session, todo, email, state):
    """One DOI per request, Springer DOIs only. Free tier rejects batched OR queries."""
    if not SPRINGER_KEY:
        state["stopped"] = "springer: no SPRINGER_META_API_KEY set"
        return
    for n, rec in enumerate(todo, 1):
        # Throttling shows up as a dropped connection, not a 429, so retry once
        # before giving up on the record.
        resp = None
        for attempt in (1, 2):
            try:
                resp = session.get("https://api.springernature.com/meta/v2/json",
                                   params={"q": "doi:" + rec["doi_or_arxiv_id"].strip(),
                                           "api_key": SPRINGER_KEY}, timeout=30)
                break
            except requests.RequestException:
                if attempt == 2:
                    state["errors"] += 1
                time.sleep(3)
        if resp is None:
            continue
        if resp.status_code == 429:
            state["stopped"] = "springer: daily quota exhausted"
            return
        if resp.ok:
            recs = resp.json().get("records") or []
            text = clean_abstract(recs[0].get("abstract", "")) if recs else ""
            if text:
                rec["abstract"] = text
                rec["abstract_source"] = "Springer"
                state["filled"] += 1
        state["done"] = n
        yield
        time.sleep(DELAY["springer"])


PROVIDERS = {
    "openalex": (pass_openalex, lambda r: r["doi_or_arxiv_id"].startswith("10.")),
    "crossref": (pass_crossref, lambda r: r["doi_or_arxiv_id"].startswith("10.")),
    "springer": (pass_springer, lambda r: r["doi_or_arxiv_id"].startswith("10.1007")),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Fill missing abstracts.")
    ap.add_argument("--email", default="", help="Contact email for polite pools.")
    ap.add_argument("--providers", nargs="+", default=list(PROVIDERS),
                    choices=list(PROVIDERS), help="Which providers to run, in order.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report candidate counts, fetch nothing.")
    args = ap.parse_args()

    rows = list(csv.DictReader(RECORDS.open(encoding="utf-8")))
    fields = list(rows[0].keys())
    if "abstract_source" not in fields:
        fields.insert(fields.index("abstract") + 1, "abstract_source")
    for r in rows:
        r.setdefault("abstract_source", "original" if r["abstract"].strip() else "")

    missing0 = sum(1 for r in rows if not r["abstract"].strip())
    no_id = sum(1 for r in rows
                if not r["abstract"].strip() and not r["doi_or_arxiv_id"].strip())
    print(f"records                : {len(rows)}")
    print(f"missing an abstract    : {missing0}")
    print(f"  ...no identifier     : {no_id}  (cannot be looked up)\n")

    if args.dry_run:
        for name in args.providers:
            _, want = PROVIDERS[name]
            n = sum(1 for r in rows if not r["abstract"].strip() and want(r))
            print(f"  {name:10} candidates: {n}")
        print("\ndry run — nothing fetched.")
        return 0

    session = requests.Session()
    ua = f"rag-systematic-review/1.0 (mailto:{args.email})" if args.email \
        else "rag-systematic-review/1.0"
    session.headers.update({"User-Agent": ua, "Accept": "application/json"})

    per_provider, total_cost, stops = {}, 0.0, []
    for name in args.providers:
        fn, want = PROVIDERS[name]
        todo = [r for r in rows if not r["abstract"].strip() and want(r)]
        state = {"filled": 0, "errors": 0, "cost": 0.0, "done": 0, "stopped": ""}
        print(f"-> {name}: {len(todo)} candidates")
        if todo:
            for _ in fn(session, todo, args.email, state):
                print(f"   {state['done']:5}/{len(todo)} | filled {state['filled']}"
                      f" | errors {state['errors']}", end="\r", flush=True)
            print()
        per_provider[name] = {"candidates": len(todo), "filled": state["filled"],
                              "errors": state["errors"],
                              "cost_usd": round(state["cost"], 6),
                              "stopped_early": state["stopped"] or None}
        total_cost += state["cost"]
        if state["stopped"]:
            stops.append(state["stopped"])
            print(f"   *** {state['stopped']} ***")

    with RECORDS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    still = sum(1 for r in rows if not r["abstract"].strip())
    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "records_total": len(rows),
        "missing_before": missing0,
        "missing_after": still,
        "filled_total": missing0 - still,
        "without_identifier": no_id,
        "by_provider": per_provider,
        "cost_usd_reported_by_openalex": round(total_cost, 6),
        "stopped_early": stops or None,
    }
    REPORT.write_text(json.dumps(report, indent=2))

    print(f"\nmissing before : {missing0}")
    print(f"filled         : {missing0 - still}")
    print(f"missing after  : {still}")
    for name, s in per_provider.items():
        print(f"  {name:10} {s['filled']:>5} of {s['candidates']}")
    print(f"cost (OpenAlex): ${total_cost:.4f}")
    print(f"\nwrote {RECORDS}\nwrote {REPORT}")
    return 2 if stops else 0


if __name__ == "__main__":
    raise SystemExit(main())
