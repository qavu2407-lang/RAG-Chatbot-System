#!/usr/bin/env python3
"""
rag_harvest.py — bibliographic harvester for a PRISMA-style systematic review of
retrieval-augmented generation (RAG) architectures and workflows.

WHAT THIS DOES
--------------
Runs a fixed set of search queries against scholarly APIs, records the EXACT query
string sent to each API, the date it ran, and the real hit count reported by that API. Writes:

    paper/out/search_log.csv   one row per (database x query) — paste into the Search log tab
    paper/out/records.csv      deduplicated records — paste into the Screening log tab
    paper/out/raw/*.json       every raw API response, for audit / re-derivation
    paper/out/manifest.json    run metadata (timestamps, versions, counts)

SOURCES
-------
  arxiv       arXiv API              no key needed
  openalex    OpenAlex               no key needed (email = polite pool, faster)
  s2          Semantic Scholar       no key needed (key = higher rate limit)
  ieee        IEEE Xplore API        FREE key: https://developer.ieee.org/
  scopus      Scopus (Elsevier)      key + institutional network:
                                     https://dev.elsevier.com/

  ggscholar   Serpapi API            free key: https://serpapi.com/

USAGE
-----
    pip install requests
    python rag_harvest.py --email you@example.com

    # only some sources
    python rag_harvest.py --email you@example.com --sources arxiv openalex s2

    # with keys
    export IEEE_API_KEY=xxxx
    export SCOPUS_API_KEY=xxxx
    export S2_API_KEY=xxxx
    export GGSCHOLAR_API_KEY=xxxx
    python rag_harvest.py --email you@example.com

    # cap how many records are pulled per (source, query)
    python rag_harvest.py --email you@example.com --max-records 200

Re-running is safe: out/ is overwritten, raw/ is rewritten.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

# Load .env from the repo root. Names are upper-cased so `ieee_api_key` works too.
_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = next((c for c in (_ROOT / ".env", _ROOT / "temp" / ".env") if c.exists()), None)
if _ENV_FILE is None:
    print("WARNING: no .env found in repo root or temp/. IEEE, Scopus and Google "
          "Scholar will all be SKIPPED.", file=sys.stderr)
else:
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip().upper(), _v.strip().strip("\"'"))


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

DATE_FROM = "2020-01-01"
YEAR_FROM = 2020

# Anchored to the script, not the caller's cwd: out/ lives beside rag_harvest.py,
# and a cwd-relative path silently creates a second output tree when run from the
# repo root. Override with --out-dir.
OUT = Path(__file__).resolve().parent / "out"
RAW = OUT / "raw"

# Per-source politeness delay in seconds between HTTP calls.
DELAY = {
    "arxiv": 3.0,      # arXiv asks for >=3s between API calls
    "openalex": 0.5,   # ~2/s; 0.15 trips 429 on long cursor walks
    "s2": 1.1,         # ~1 req/s unauthenticated
    "ieee": 0.5,
    "scopus": 0.3,
    "scholar": 1.5,
}

PAGE_SIZE = {
    "arxiv": 100,
    "openalex": 200,
    "s2": 100,
    "ieee": 200,
    "scopus": 25,
    "scholar": 20,   # SerpAPI google_scholar max num per search
}


# ----------------------------------------------------------------------------
# Query definitions
#
# One concept per query id. Each source gets the concept rendered in ITS OWN
# syntax — because every API parses booleans differently, and a string that is
# silently mangled produces a hit count that means nothing.
#
# BASE is the review's headline string. Note it is deliberately broad: group B
# contains "retrieval", "generation" and "architecture", so `A AND B` is close
# to `A` alone on most engines. That is fine for the PRISMA identification
# count, but the three GAP queries are what actually close the corpus holes.
# ----------------------------------------------------------------------------

QUERIES: dict[str, dict[str, Any]] = {
    "BASE": {
        "label": "Base review string (broad identification sweep)",
        "arxiv": (
            '(all:"retrieval-augmented generation" OR all:"RAG") AND '
            '(all:"graph RAG" OR all:"agentic RAG" OR all:"multimodal RAG" OR '
            'all:"advanced RAG" OR all:"modular RAG" OR all:indexing OR all:chunking OR '
            'all:database OR all:"graph database" OR all:"vector database" OR '
            'all:reranking OR all:retrieval OR all:generation OR all:architecture OR '
            'all:pipeline OR all:evaluation OR all:governance)'
        ),
        "openalex": (
            '("retrieval-augmented generation" OR "RAG") AND '
            '("graph RAG" OR "agentic RAG" OR "multimodal RAG" OR "advanced RAG" OR '
            '"modular RAG" OR indexing OR chunking OR database OR "graph database" OR '
            '"vector database" OR reranking OR retrieval OR generation OR architecture OR '
            'pipeline OR evaluation OR governance)'
        ),
        # Semantic Scholar bulk syntax: + = AND, | = OR, quotes = phrase
        "s2": (
            '("retrieval-augmented generation" | "RAG") + '
            '("graph RAG" | "agentic RAG" | "multimodal RAG" | "advanced RAG" | '
            '"modular RAG" | indexing | chunking | database | "graph database" | '
            '"vector database" | reranking | retrieval | generation | architecture | '
            'pipeline | evaluation | governance)'
        ),
        "ieee": (
            '("retrieval-augmented generation" OR "RAG") AND '
            '("graph RAG" OR "agentic RAG" OR "multimodal RAG" OR indexing OR chunking OR '
            '"vector database" OR reranking OR architecture OR pipeline OR evaluation OR '
            'governance)'
        ),
        "scopus": (
            'TITLE-ABS-KEY(("retrieval-augmented generation" OR "RAG") AND '
            '("graph RAG" OR "agentic RAG" OR "multimodal RAG" OR "advanced RAG" OR '
            '"modular RAG" OR indexing OR chunking OR database OR "graph database" OR '
            '"vector database" OR reranking OR retrieval OR generation OR architecture OR '
            'pipeline OR evaluation OR governance))'
        ),
        # Google Scholar truncates queries past 256 chars; shortened form.
        "scholar": (
            '("retrieval-augmented generation" OR "RAG") AND ("graph RAG" OR "agentic RAG" OR "modular RAG" OR indexing OR chunking OR "vector database" OR reranking OR architecture OR pipeline OR evaluation)'
        ),
    },
    "GAP-POSTRET": {
        "label": "Gap 1 — Post-retrieval: reranking, context compression, repacking",
        "arxiv": (
            '(all:"retrieval-augmented generation" OR all:"RAG") AND '
            '(all:reranking OR all:reranker OR all:"cross-encoder" OR all:"bi-encoder" OR '
            'all:monoT5 OR all:RankGPT OR all:"listwise reranking" OR '
            'all:"context compression" OR all:"prompt compression" OR all:repacking OR '
            'all:"context curation" OR all:"passage reranking")'
        ),
        "openalex": (
            '("retrieval-augmented generation" OR "RAG") AND '
            '(reranking OR reranker OR "cross-encoder" OR "bi-encoder" OR monoT5 OR '
            'RankGPT OR "listwise reranking" OR "context compression" OR '
            '"prompt compression" OR repacking OR "passage reranking")'
        ),
        "s2": (
            '("retrieval-augmented generation" | "RAG") + '
            '(reranking | reranker | "cross-encoder" | "bi-encoder" | monoT5 | RankGPT | '
            '"listwise reranking" | "context compression" | "prompt compression" | '
            'repacking | "passage reranking")'
        ),
        "ieee": (
            '("retrieval-augmented generation" OR "RAG") AND '
            '(reranking OR reranker OR "cross-encoder" OR "context compression" OR '
            '"passage reranking")'
        ),
        "scopus": (
            'TITLE-ABS-KEY(("retrieval-augmented generation" OR "RAG") AND '
            '(reranking OR reranker OR "cross-encoder" OR "bi-encoder" OR monoT5 OR '
            'RankGPT OR "listwise reranking" OR "context compression" OR '
            '"prompt compression" OR repacking OR "passage reranking"))'
        ),
        # Google Scholar truncates queries past 256 chars; shortened form.
        "scholar": (
            '("retrieval-augmented generation" OR "RAG") AND (reranking OR reranker OR "cross-encoder" OR "context compression" OR "prompt compression" OR repacking OR "passage reranking")'
        ),
    },
    "GAP-ORCH": {
        "label": "Gap 2 — Orchestration: agentic RAG, self-reflection, adaptive routing",
        "arxiv": (
            '(all:"retrieval-augmented generation" OR all:"RAG") AND '
            '(all:"agentic RAG" OR all:"Self-RAG" OR all:"corrective RAG" OR all:CRAG OR '
            'all:"adaptive retrieval" OR all:"query routing" OR all:"adaptive RAG" OR '
            'all:orchestration OR all:"multi-agent" OR all:"self-reflection" OR '
            'all:"iterative retrieval" OR all:"active retrieval")'
        ),
        "openalex": (
            '("retrieval-augmented generation" OR "RAG") AND '
            '("agentic RAG" OR "Self-RAG" OR "corrective RAG" OR "adaptive retrieval" OR '
            '"query routing" OR "adaptive RAG" OR orchestration OR "multi-agent" OR '
            '"self-reflection" OR "iterative retrieval" OR "active retrieval")'
        ),
        "s2": (
            '("retrieval-augmented generation" | "RAG") + '
            '("agentic RAG" | "Self-RAG" | "corrective RAG" | "adaptive retrieval" | '
            '"query routing" | "adaptive RAG" | orchestration | "multi-agent" | '
            '"self-reflection" | "iterative retrieval" | "active retrieval")'
        ),
        "ieee": (
            '("retrieval-augmented generation" OR "RAG") AND '
            '("agentic RAG" OR "adaptive retrieval" OR "query routing" OR orchestration OR '
            '"multi-agent")'
        ),
        "scopus": (
            'TITLE-ABS-KEY(("retrieval-augmented generation" OR "RAG") AND '
            '("agentic RAG" OR "Self-RAG" OR "corrective RAG" OR "adaptive retrieval" OR '
            '"query routing" OR "adaptive RAG" OR orchestration OR "multi-agent" OR '
            '"iterative retrieval" OR "active retrieval"))'
        ),
        # Google Scholar truncates queries past 256 chars; shortened form.
        "scholar": (
            '("retrieval-augmented generation" OR "RAG") AND ("agentic RAG" OR "Self-RAG" OR "corrective RAG" OR "adaptive retrieval" OR "query routing" OR orchestration OR "multi-agent")'
        ),
    },
    "GAP-GOV": {
        "label": "Gap 3 — Governance: access control, prompt injection, provenance, audit",
        "arxiv": (
            '(all:"retrieval-augmented generation" OR all:"RAG") AND '
            '(all:"access control" OR all:"permission-aware" OR all:"prompt injection" OR '
            'all:provenance OR all:"audit trail" OR all:auditability OR all:governance OR '
            'all:"knowledge base poisoning" OR all:"data leakage" OR all:multi-tenant OR '
            'all:compliance OR all:"regulated domain" OR all:privacy)'
        ),
        "openalex": (
            '("retrieval-augmented generation" OR "RAG") AND '
            '("access control" OR "permission-aware" OR "prompt injection" OR provenance OR '
            '"audit trail" OR auditability OR governance OR "knowledge base poisoning" OR '
            '"data leakage" OR multi-tenant OR compliance OR privacy)'
        ),
        "s2": (
            '("retrieval-augmented generation" | "RAG") + '
            '("access control" | "permission-aware" | "prompt injection" | provenance | '
            '"audit trail" | auditability | governance | "knowledge base poisoning" | '
            '"data leakage" | multi-tenant | compliance | privacy)'
        ),
        "ieee": (
            '("retrieval-augmented generation" OR "RAG") AND '
            '("access control" OR "prompt injection" OR provenance OR governance OR '
            'compliance OR privacy)'
        ),
        "scopus": (
            'TITLE-ABS-KEY(("retrieval-augmented generation" OR "RAG") AND '
            '("access control" OR "permission-aware" OR "prompt injection" OR provenance OR '
            '"audit trail" OR auditability OR governance OR "knowledge base poisoning" OR '
            '"data leakage" OR multi-tenant OR compliance OR privacy))'
        ),
        # Google Scholar truncates queries past 256 chars; shortened form.
        "scholar": (
            '("retrieval-augmented generation" OR "RAG") AND ("access control" OR "prompt injection" OR provenance OR audit OR governance OR poisoning OR "data leakage")'
        ),
    },
}

SCHOLAR_MAX_PAGES = 10   # 10 pages x 20 = 200 records/query; 40 of 250 monthly searches

# ----------------------------------------------------------------------------
# Local boolean post-filter
# ----------------------------------------------------------------------------
# Re-applies a query's intended boolean to a record's title+abstract.
# Structure mirrors every query: (RAG anchor) AND (>=1 concept term).
#
# Currently has no caller: it existed to clean Crossref's bag-of-words output,
# and Crossref was removed from the pipeline. Retained deliberately — Phase 2
# screening tiers use these same regexes, and re-adding IEEE Xplore will need
# the filter again.

ANCHOR_TERMS = [r"retrieval[- ]augmented generation", r"\bRAG\b", r"\bRAG-"]

CONCEPT_TERMS = {
    "BASE": [r"graph RAG", r"agentic RAG", r"multimodal RAG", r"advanced RAG",
             r"modular RAG", r"indexing", r"chunking", r"vector database",
             r"graph database", r"reranking", r"retrieval", r"architecture",
             r"pipeline", r"evaluation", r"governance"],
    "GAP-POSTRET": [r"reranking", r"reranker", r"re-ranking", r"cross-encoder",
                    r"bi-encoder", r"monoT5", r"RankGPT", r"listwise",
                    r"context compression", r"prompt compression", r"repacking",
                    r"context curation", r"passage rank"],
    "GAP-ORCH": [r"agentic", r"self-RAG", r"corrective RAG", r"adaptive retrieval",
                 r"query rout", r"orchestrat", r"multi-agent", r"iterative retrieval",
                 r"self-reflect", r"query decomposition", r"query planning"],
    "GAP-GOV": [r"access control", r"permission", r"prompt injection", r"provenance",
                r"audit", r"governance", r"poisoning", r"data leakage",
                r"confidential", r"attribution", r"citation", r"compliance"],
}

_ANCHOR_RE = re.compile("|".join(ANCHOR_TERMS), re.I)
_CONCEPT_RE = {k: re.compile("|".join(v), re.I) for k, v in CONCEPT_TERMS.items()}


def matches_boolean(rec: dict, qid: str) -> bool:
    """Re-apply the intended (anchor AND concept) boolean to one record."""
    text = f"{rec.get('title', '')} {rec.get('abstract', '')}"
    return bool(_ANCHOR_RE.search(text) and _CONCEPT_RE[qid].search(text))



# ----------------------------------------------------------------------------
# Record schema
# ----------------------------------------------------------------------------

FIELDS = [
    "record_id",
    "title",
    "authors",
    "year",
    "source_database",
    "doi_or_arxiv_id",
    "venue",
    "publication_type",
    "peer_reviewed_guess",
    "publication_status",
    "url",
    "query_id",
    "abstract",
]


def blank_record() -> dict[str, str]:
    return {k: "" for k in FIELDS}


# ----------------------------------------------------------------------------
# HTTP helper
# ----------------------------------------------------------------------------

class Fetcher:
    def __init__(self, email: str, verbose: bool = True):
        self.email = email
        self.verbose = verbose
        self.session = requests.Session()
        ua = f"rag-systematic-review/1.0 (mailto:{email})" if email else "rag-systematic-review/1.0"
        self.session.headers.update({"User-Agent": ua, "Accept": "application/json"})
        self._last: dict[str, float] = {}

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg, file=sys.stderr, flush=True)

    def _throttle(self, source: str) -> None:
        gap = DELAY.get(source, 0.5)
        prev = self._last.get(source)
        if prev is not None:
            wait = gap - (time.monotonic() - prev)
            if wait > 0:
                time.sleep(wait)
        self._last[source] = time.monotonic()

    def get(
        self,
        source: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        as_json: bool = True,
        tries: int = 6,
    ):
        """GET with throttling and exponential backoff. Returns parsed body or None."""
        for attempt in range(1, tries + 1):
            self._throttle(source)
            try:
                r = self.session.get(url, params=params, headers=headers, timeout=60)
            except requests.RequestException as exc:
                self.log(f"    [{source}] network error ({exc.__class__.__name__}), attempt {attempt}/{tries}")
                time.sleep(min(2 ** attempt, 30))
                continue

            if r.status_code == 200:
                try:
                    return r.json() if as_json else r.text
                except ValueError:
                    self.log(f"    [{source}] response was not valid JSON")
                    return None

            if r.status_code in (429, 500, 502, 503, 504):
                backoff = min(2 ** attempt, 60)
                self.log(f"    [{source}] HTTP {r.status_code}, backing off {backoff}s "
                         f"(attempt {attempt}/{tries})")
                time.sleep(backoff)
                continue

            self.log(f"    [{source}] HTTP {r.status_code} — giving up. "
                     f"{r.text[:200]}")
            return None

        self.log(f"    [{source}] exhausted {tries} attempts")
        return None


# ----------------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------------

def norm_title(t: str) -> str:
    """Aggressive title normalisation for duplicate detection."""
    if not t:
        return ""
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.lower()
    t = re.sub(r"[^a-z0-9]+", "", t)
    return t


def clean(s: Any, limit: int = 4000) -> str:
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def save_raw(name: str, payload: Any) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / f"{name}.json"
    with path.open("w", encoding="utf-8") as fh:
        if isinstance(payload, str):
            json.dump({"_raw_text": payload}, fh, ensure_ascii=False, indent=1)
        else:
            json.dump(payload, fh, ensure_ascii=False, indent=1)


# ----------------------------------------------------------------------------
# Source adapters
#
# Each returns (total_hits: int | None, records: list[dict], note: str)
# total_hits is the count the API itself reports — never an estimate.
# ----------------------------------------------------------------------------

ARXIV_NS = {"a": "http://www.w3.org/2005/Atom",
            "o": "http://a9.com/-/spec/opensearch/1.1/"}


def search_arxiv(f: Fetcher, qid: str, query: str, cap: int):
    query_sent = f"({query}) AND submittedDate:[{YEAR_FROM}0101 TO 20301231]"
    records: list[dict] = []
    total: int | None = None
    start = 0
    page = 0

    while True:
        body = f.get(
            "arxiv",
            "http://export.arxiv.org/api/query",
            params={
                "search_query": query_sent,
                "start": start,
                "max_results": PAGE_SIZE["arxiv"],
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
            as_json=False,
        )
        if body is None:
            break
        save_raw(f"arxiv_{qid}_p{page}", body)

        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            f.log("    [arxiv] could not parse Atom feed")
            break

        if total is None:
            node = root.find("o:totalResults", ARXIV_NS)
            if node is not None and node.text and node.text.isdigit():
                total = int(node.text)

        entries = root.findall("a:entry", ARXIV_NS)
        if not entries:
            break

        for e in entries:
            rec = blank_record()
            rec["title"] = clean(e.findtext("a:title", default="", namespaces=ARXIV_NS))
            authors = [clean(a.findtext("a:name", default="", namespaces=ARXIV_NS))
                       for a in e.findall("a:author", ARXIV_NS)]
            rec["authors"] = "; ".join(x for x in authors if x)
            published = e.findtext("a:published", default="", namespaces=ARXIV_NS)
            rec["year"] = published[:4]
            rec["source_database"] = "arXiv"
            aid = e.findtext("a:id", default="", namespaces=ARXIV_NS)
            m = re.search(r"arxiv\.org/abs/(.+)$", aid)
            short = m.group(1) if m else aid
            rec["doi_or_arxiv_id"] = f"arXiv:{short}"
            rec["url"] = aid
            journal_ref = e.findtext("{http://arxiv.org/schemas/atom}journal_ref", default="")
            doi_el = e.findtext("{http://arxiv.org/schemas/atom}doi", default="")
            rec["venue"] = clean(journal_ref) or "arXiv preprint"
            rec["publication_type"] = "preprint"
            # A journal_ref or DOI means it was published somewhere; still needs
            # confirming against the venue, so never asserted outright.
            rec["peer_reviewed_guess"] = "VERIFY" if (journal_ref or doi_el) else "No (preprint)"
            rec["abstract"] = clean(e.findtext("a:summary", default="", namespaces=ARXIV_NS))
            rec["query_id"] = qid
            records.append(rec)

        start += len(entries)
        page += 1
        if total is not None and start >= total:
            break
        if start >= cap:
            break

    return total, records, ""


def search_openalex(f: Fetcher, qid: str, query: str, cap: int):
    records: list[dict] = []
    total: int | None = None
    cursor = "*"
    page = 0
    filt = f"title_and_abstract.search:{query},from_publication_date:{DATE_FROM}"

    while True:
        params = {
            "filter": filt,
            "per-page": PAGE_SIZE["openalex"],
            "cursor": cursor,
            "select": "id,doi,title,publication_year,authorships,primary_location,type,"
                      "abstract_inverted_index",
        }
        if f.email:
            params["mailto"] = f.email
        # mailto only requests the polite pool; it does NOT authenticate. Without
        # the key every call is billed to the anonymous $0.10/day bucket instead
        # of this account's $1/day budget and prepaid balance.
        key = os.environ.get("OPENALEX_API_KEY", "").strip()
        if key:
            params["api_key"] = key

        body = f.get("openalex", "https://api.openalex.org/works", params=params)
        if body is None:
            break
        save_raw(f"openalex_{qid}_p{page}", body)

        if total is None:
            total = body.get("meta", {}).get("count")

        results = body.get("results", [])
        if not results:
            break

        for w in results:
            rec = blank_record()
            rec["title"] = clean(w.get("title") or w.get("display_name"))
            names = [clean((a.get("author") or {}).get("display_name"))
                     for a in (w.get("authorships") or [])]
            rec["authors"] = "; ".join(x for x in names if x)
            rec["year"] = str(w.get("publication_year") or "")
            rec["source_database"] = "OpenAlex"
            doi = w.get("doi") or ""
            rec["doi_or_arxiv_id"] = doi.replace("https://doi.org/", "") if doi else ""
            loc = w.get("primary_location") or {}
            src = loc.get("source") or {}
            rec["venue"] = clean(src.get("display_name"))
            wtype = clean(w.get("type"))
            rec["publication_type"] = wtype
            src_type = clean(src.get("type"))
            if src_type == "repository" or wtype == "preprint":
                rec["peer_reviewed_guess"] = "No (preprint)"
            elif src_type in ("journal", "conference", "book series"):
                rec["peer_reviewed_guess"] = "VERIFY"
            else:
                rec["peer_reviewed_guess"] = "VERIFY"
            rec["url"] = clean(loc.get("landing_page_url")) or clean(w.get("id"))
            rec["abstract"] = _openalex_abstract(w.get("abstract_inverted_index"))
            rec["query_id"] = qid
            records.append(rec)

        cursor = body.get("meta", {}).get("next_cursor")
        page += 1
        if not cursor or len(records) >= cap:
            break

    return total, records, ""


def _openalex_abstract(inv: dict | None) -> str:
    """OpenAlex stores abstracts as an inverted index; rebuild word order."""
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return clean(" ".join(w for _, w in positions))


def search_s2(f: Fetcher, qid: str, query: str, cap: int):
    key = os.environ.get("S2_API_KEY", "").strip()
    headers = {"x-api-key": key} if key else None
    records: list[dict] = []
    total: int | None = None
    token: str | None = None
    page = 0
    fields = ("title,year,authors,externalIds,venue,publicationTypes,abstract,"
              "openAccessPdf,url,publicationVenue")

    while True:
        params: dict[str, Any] = {
            "query": query,
            "fields": fields,
            "year": f"{YEAR_FROM}-",
        }
        if token:
            params["token"] = token

        body = f.get("s2", "https://api.semanticscholar.org/graph/v1/paper/search/bulk",
                     params=params, headers=headers)
        if body is None:
            break
        save_raw(f"s2_{qid}_p{page}", body)

        if total is None:
            total = body.get("total")

        data = body.get("data") or []
        if not data:
            break

        for p in data:
            rec = blank_record()
            rec["title"] = clean(p.get("title"))
            rec["authors"] = "; ".join(clean(a.get("name")) for a in (p.get("authors") or []))
            rec["year"] = str(p.get("year") or "")
            rec["source_database"] = "Semantic Scholar"
            ext = p.get("externalIds") or {}
            if ext.get("DOI"):
                rec["doi_or_arxiv_id"] = clean(ext["DOI"])
            elif ext.get("ArXiv"):
                rec["doi_or_arxiv_id"] = f"arXiv:{clean(ext['ArXiv'])}"
            rec["venue"] = clean(p.get("venue"))
            ptypes = p.get("publicationTypes") or []
            rec["publication_type"] = "; ".join(clean(t) for t in ptypes)
            venue_obj = p.get("publicationVenue") or {}
            vtype = clean(venue_obj.get("type"))
            if not rec["venue"] and ext.get("ArXiv"):
                rec["peer_reviewed_guess"] = "No (preprint)"
            elif vtype in ("journal", "conference"):
                rec["peer_reviewed_guess"] = "VERIFY"
            else:
                rec["peer_reviewed_guess"] = "VERIFY"
            rec["url"] = clean(p.get("url"))
            rec["abstract"] = clean(p.get("abstract"))
            rec["query_id"] = qid
            records.append(rec)

        token = body.get("token")
        page += 1
        if not token or len(records) >= cap:
            break

    return total, records, ""


def search_scholar(f: Fetcher, qid: str, query: str, cap: int):
    """Google Scholar via SerpAPI. Budget-guarded: the free plan is 250 searches/month
    and every page of 20 results burns one, so this refuses to spend beyond
    SCHOLAR_MAX_PAGES per query and stops early if the account runs dry."""
    key = os.environ.get("GOOGLE_SCHOLAR_API_KEY", "").strip()
    if not key:
        return None, [], ("SKIPPED — no GOOGLE_SCHOLAR_API_KEY set. SerpAPI key: "
                          "https://serpapi.com/ (free plan = 250 searches/month)")

    records: list[dict] = []
    total: int | None = None
    note = ""
    pages = min(SCHOLAR_MAX_PAGES, -(-int(min(cap, 10_000)) // PAGE_SIZE["scholar"]))

    for page in range(pages):
        body = f.get("scholar", "https://serpapi.com/search",
                     params={
                         "engine": "google_scholar",
                         "q": query,
                         "as_ylo": YEAR_FROM,
                         "start": page * PAGE_SIZE["scholar"],
                         "num": PAGE_SIZE["scholar"],
                         "api_key": key,
                     })
        if body is None:
            break
        save_raw(f"scholar_{qid}_p{page}", {k: v for k, v in body.items()
                                            if k != "search_metadata"})

        if body.get("error"):
            note = f"STOPPED — SerpAPI returned: {body['error']}"
            break

        if total is None:
            total = (body.get("search_information") or {}).get("total_results")

        items = body.get("organic_results") or []
        if not items:
            break

        for it in items:
            rec = blank_record()
            rec["title"] = clean(it.get("title"))
            # Scholar packs "authors - venue, year - publisher" into one summary string.
            summary = clean((it.get("publication_info") or {}).get("summary"))
            rec["authors"] = summary.split(" - ")[0] if " - " in summary else ""
            yr = re.search(r"\b(19|20)\d{2}\b", summary)
            rec["year"] = yr.group(0) if yr else ""
            rec["venue"] = summary.split(" - ")[1] if summary.count(" - ") >= 1 else ""
            rec["source_database"] = "Google Scholar"
            rec["doi_or_arxiv_id"] = ""
            rec["publication_type"] = clean(it.get("type"))
            rec["peer_reviewed_guess"] = "VERIFY"
            rec["url"] = clean(it.get("link"))
            rec["abstract"] = clean(it.get("snippet"))
            rec["query_id"] = qid
            records.append(rec)

        if len(items) < PAGE_SIZE["scholar"] or len(records) >= cap:
            break

    if total is not None and len(records) < total and not note:
        note = (f"CAPPED AT {len(records)} of ~{total} Scholar hits — SerpAPI free plan "
                f"is 250 searches/month and each page of 20 costs one. Scholar's total "
                f"is an unstable estimate, not a reproducible count.")
    return total, records, note


def search_ieee(f: Fetcher, qid: str, query: str, cap: int):
    key = os.environ.get("IEEE_API_KEY", "").strip()
    if not key:
        return None, [], "SKIPPED — no IEEE_API_KEY set. Free key: https://developer.ieee.org/"

    records: list[dict] = []
    total: int | None = None
    start = 1
    page = 0

    while True:
        body = f.get("ieee", "https://ieeexploreapi.ieee.org/api/v1/search/articles",
                     params={
                         "apikey": key,
                         "querytext": query,
                         "start_year": YEAR_FROM,
                         "max_records": PAGE_SIZE["ieee"],
                         "start_record": start,
                         "sort_field": "article_title",
                         "sort_order": "asc",
                     })
        if body is None:
            break
        save_raw(f"ieee_{qid}_p{page}", body)

        if total is None:
            total = body.get("total_records")

        arts = body.get("articles") or []
        if not arts:
            break

        for a in arts:
            rec = blank_record()
            rec["title"] = clean(a.get("title"))
            authors = ((a.get("authors") or {}).get("authors")) or []
            rec["authors"] = "; ".join(clean(x.get("full_name")) for x in authors)
            rec["year"] = clean(a.get("publication_year"))
            rec["source_database"] = "IEEE Xplore"
            rec["doi_or_arxiv_id"] = clean(a.get("doi"))
            rec["venue"] = clean(a.get("publication_title"))
            rec["publication_type"] = clean(a.get("content_type"))
            rec["peer_reviewed_guess"] = "VERIFY"
            rec["url"] = clean(a.get("html_url"))
            rec["abstract"] = clean(a.get("abstract"))
            rec["query_id"] = qid
            records.append(rec)

        start += len(arts)
        page += 1
        if total is not None and start > total:
            break
        if len(records) >= cap:
            break

    return total, records, ""


def search_scopus(f: Fetcher, qid: str, query: str, cap: int):
    key = os.environ.get("SCOPUS_API_KEY", "").strip()
    if not key:
        return None, [], ("SKIPPED — no SCOPUS_API_KEY set. Key: https://dev.elsevier.com/ "
                          "(must run from your institution's network)")

    records: list[dict] = []
    total: int | None = None
    start = 0
    page = 0
    note = ""
    dated = f"{query} AND PUBYEAR > {YEAR_FROM - 1}"

    # Scopus refuses start > 5000 on the standard search endpoint.
    SCOPUS_WALL = 5000

    while True:
        body = f.get("scopus", "https://api.elsevier.com/content/search/scopus",
                     params={
                         "query": dated,
                         "count": PAGE_SIZE["scopus"],
                         "start": start,
                         "apiKey": key,
                     },
                     headers={"Accept": "application/json"})
        if body is None:
            break
        save_raw(f"scopus_{qid}_p{page}", body)

        sr = body.get("search-results", {})
        if total is None:
            tr = sr.get("opensearch:totalResults")
            total = int(tr) if str(tr).isdigit() else None

        entries = sr.get("entry") or []
        if not entries or "error" in entries[0]:
            break

        for e in entries:
            rec = blank_record()
            rec["title"] = clean(e.get("dc:title"))
            rec["authors"] = clean(e.get("dc:creator"))
            rec["year"] = clean(e.get("prism:coverDate"))[:4]
            rec["source_database"] = "Scopus"
            rec["doi_or_arxiv_id"] = clean(e.get("prism:doi"))
            rec["venue"] = clean(e.get("prism:publicationName"))
            rec["publication_type"] = clean(e.get("subtypeDescription"))
            rec["peer_reviewed_guess"] = "VERIFY"
            rec["url"] = clean(e.get("prism:url"))
            rec["abstract"] = clean(e.get("dc:description"))
            rec["query_id"] = qid
            records.append(rec)

        start += len(entries)
        page += 1
        if total is not None and start >= total:
            break
        if len(records) >= cap:
            break
        if start >= SCOPUS_WALL:
            note = (f"TRUNCATED AT SCOPUS API CEILING: the search endpoint refuses "
                    f"start > {SCOPUS_WALL}, so only the {len(records)} most relevant "
                    f"of {total} hits could be retrieved. hits_returned is the true "
                    f"total; the shortfall is an API limit, not a screening decision.")
            break

    return total, records, note


# ----------------------------------------------------------------------------
# Deduplication
# ----------------------------------------------------------------------------

# Source preference when the same paper appears in several databases — the one
# that usually carries the richest metadata wins the surviving row.
SOURCE_RANK = {
    "OpenAlex": 0,
    "Semantic Scholar": 1,
    "Scopus": 2,
    "IEEE Xplore": 3,
    "arXiv": 7,
}


def dedupe(records: list[dict]) -> tuple[list[dict], int]:
    """Collapse duplicates on DOI, then arXiv ID, then normalised title."""
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []

    for rec in records:
        ident = (rec.get("doi_or_arxiv_id") or "").strip().lower()
        key = ident if ident else "t:" + norm_title(rec.get("title", ""))
        if not key or key == "t:":
            key = f"anon:{len(order)}"
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(rec)

    # Second pass: merge DOI-keyed and title-keyed buckets describing one paper.
    title_index: dict[str, str] = {}
    for key in order:
        for rec in buckets[key]:
            nt = norm_title(rec.get("title", ""))
            if nt and nt not in title_index:
                title_index[nt] = key

    merged: dict[str, list[dict]] = {}
    remap: dict[str, str] = {}
    for key in order:
        nt_keys = {norm_title(r.get("title", "")) for r in buckets[key]}
        target = key
        for nt in nt_keys:
            if nt and title_index.get(nt) and title_index[nt] != key:
                target = remap.get(title_index[nt], title_index[nt])
                break
        remap[key] = target
        merged.setdefault(target, []).extend(buckets[key])

    survivors: list[dict] = []
    duplicates_removed = 0
    for key, group in merged.items():
        group.sort(key=lambda r: SOURCE_RANK.get(r.get("source_database", ""), 99))
        best = dict(group[0])
        others = group[1:]
        duplicates_removed += len(others)

        # Backfill empty fields from the discarded copies rather than losing them.
        for other in others:
            for fld in FIELDS:
                if not best.get(fld) and other.get(fld):
                    best[fld] = other[fld]

        all_sources = sorted({r["source_database"] for r in group if r.get("source_database")})
        all_queries = sorted({r["query_id"] for r in group if r.get("query_id")})
        best["source_database"] = "; ".join(all_sources)
        best["query_id"] = "; ".join(all_queries)
        survivors.append(best)

    survivors.sort(key=lambda r: (r.get("year") or "0", norm_title(r.get("title", ""))),
                   reverse=True)
    return survivors, duplicates_removed


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

ADAPTERS = {
    "arxiv": ("arXiv", search_arxiv),
    "openalex": ("OpenAlex", search_openalex),
    "s2": ("Semantic Scholar", search_s2),
    "ieee": ("IEEE Xplore", search_ieee),
    "scopus": ("Scopus", search_scopus),
    "scholar": ("Google Scholar", search_scholar),
}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Harvest RAG systematic-review records from scholarly APIs.")
    ap.add_argument("--email", default=os.environ.get("HARVEST_EMAIL", ""),
                    help="Your email. Used for the OpenAlex polite pool (faster, "
                         "more reliable). Strongly recommended.")
    ap.add_argument("--sources", nargs="+", default=list(ADAPTERS.keys()),
                    choices=list(ADAPTERS.keys()),
                    help="Which sources to query. Default: all.")
    ap.add_argument("--queries", nargs="+", default=list(QUERIES.keys()),
                    choices=list(QUERIES.keys()),
                    help="Which queries to run. Default: all.")
    ap.add_argument("--max-records", type=int, default=400,
                    help="Cap on records pulled per (source, query). 0 = no cap. Hit "
                         "COUNTS are always the API's full total, regardless of this cap.")
    ap.add_argument("--out-dir", default=None,
                    help="Write outputs here instead of the default out/ beside "
                         "this script. Use for side runs that must not clobber "
                         "the main record set.")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.email:
        print("WARNING: no --email given. OpenAlex will be slower and "
              "may rate-limit.\n", file=sys.stderr)

    global OUT, RAW
    if args.out_dir:
        OUT = Path(args.out_dir).resolve()
        RAW = OUT / "raw"

    cap = float("inf") if args.max_records == 0 else args.max_records
    args.max_records = cap

    OUT.mkdir(exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    f = Fetcher(args.email, verbose=not args.quiet)

    run_date = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    run_stamp = datetime.now(timezone.utc).isoformat()

    search_rows: list[dict] = []
    all_records: list[dict] = []

    for qid in args.queries:
        qdef = QUERIES[qid]
        f.log(f"\n=== {qid}: {qdef['label']} ===")

        for skey in args.sources:
            label, fn = ADAPTERS[skey]
            query_str = qdef[skey]
            f.log(f"  -> {label}")
            t0 = time.time()
            try:
                scap = args.max_records
                total, recs, note = fn(f, qid, query_str, scap)
            except Exception as exc:  # never let one source kill the run
                total, recs, note = None, [], f"ERROR — {exc.__class__.__name__}: {exc}"
                f.log(f"    [{skey}] unhandled error: {exc}")

            elapsed = time.time() - t0
            f.log(f"     total={total if total is not None else 'n/a'} "
                  f"retrieved={len(recs)} in {elapsed:.1f}s "
                  f"{'| ' + note if note else ''}")

            if note:
                row_note = note
            elif total is None and not recs:
                row_note = ("NO DATA RETURNED — the API was unreachable or returned an "
                            "error, so nothing ran. Hit count left blank deliberately; "
                            "it is not an estimate and must not be read as zero hits.")
            else:
                row_note = API_NOTE[skey]

            # Any adapter can break out of paging on an API failure. Without this,
            # a truncated pull is logged as if it were a complete one.
            # OpenAlex reports a count that drifts during a cursor walk, so
            # retrieved may exceed it or fall marginally short. Only flag a
            # real shortfall, not paging jitter.
            shortfall = (total - len(recs)) / total if total else 0
            if (total is not None and shortfall > 0.01 and len(recs) < scap
                    and not note):
                note = (f"INCOMPLETE — paging stopped after {len(recs)} of {total} "
                        f"records (API error or rate limit). This is a harvest "
                        f"failure, not a screening decision; re-run before relying "
                        f"on this row.")
                f.log(f"    [{skey}] INCOMPLETE: {len(recs)}/{total}")
                row_note = note

            n_raw = len(recs)

            all_records.extend(recs)
            search_rows.append({
                "database": label,
                "query_id": qid,
                "search_string_verbatim": query_str,
                "fields_searched": FIELDS_SEARCHED[skey],
                "date_range_filter": f"{YEAR_FROM} - present",
                "date_run": run_date if (total is not None or recs) else "",
                "hits_returned": "" if total is None else total,
                "records_retrieved": n_raw if (total is not None or n_raw) else "",
                "notes": row_note,
            })

        # Google Scholar placeholder — only when it was NOT run via SerpAPI.
        if "scholar" not in args.sources:
            search_rows.append({
                "database": "Google Scholar",
                "query_id": qid,
                "search_string_verbatim": QUERIES[qid]["openalex"],
                "fields_searched": "MANUAL — run by hand",
                "date_range_filter": f"{YEAR_FROM} - present",
                "date_run": "",
                "hits_returned": "",
                "records_retrieved": "",
                "notes": ("NOT RUN — Google Scholar publishes no API and prohibits "
                          "automated querying. Run this string manually, then fill "
                          "date_run and hits_returned yourself, or record the database "
                          "as not searched."),
            })

    deduped, dup_removed = dedupe(all_records)
    this_year = datetime.now(timezone.utc).year
    n_future = 0
    for i, rec in enumerate(deduped, start=1):
        rec["record_id"] = f"R-{i:04d}"
        # Keep future-dated records (online-first / accepted-not-yet-issued) but flag
        # them, so a human decides per record rather than a filter dropping them.
        yr = rec.get("year", "")[:4]
        if yr.isdigit() and int(yr) > this_year:
            rec["publication_status"] = (f"NOT YET OFFICIALLY PUBLISHED (dated {yr}) "
                                         f"— REVIEW: cite only if you accept a "
                                         f"forthcoming/online-first reference")
            n_future += 1
        else:
            rec["publication_status"] = ""

    with (OUT / "records.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(deduped)

    log_fields = ["database", "query_id", "search_string_verbatim", "fields_searched",
                  "date_range_filter", "date_run", "hits_returned", "records_retrieved",
                  "notes"]
    with (OUT / "search_log.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=log_fields)
        w.writeheader()
        w.writerows(search_rows)

    manifest = {
        "run_started_utc": run_stamp,
        "run_date_local": run_date,
        "script_version": "1.0",
        "sources_queried": args.sources,
        "queries_run": args.queries,
        "max_records_per_source_query": (None if args.max_records == float("inf")
                                         else args.max_records),
        "date_range": f"{YEAR_FROM}-present",
        "raw_records_before_dedup": len(all_records),
        "duplicates_removed": dup_removed,
        "unique_records": len(deduped),
        "keys_present": {
            "S2_API_KEY": bool(os.environ.get("S2_API_KEY")),
            "IEEE_API_KEY": bool(os.environ.get("IEEE_API_KEY")),
            "SCOPUS_API_KEY": bool(os.environ.get("SCOPUS_API_KEY")),
            "GOOGLE_SCHOLAR_API_KEY": bool(os.environ.get("GOOGLE_SCHOLAR_API_KEY")),
            "OPENALEX_API_KEY": bool(os.environ.get("OPENALEX_API_KEY")),
        },
    }
    with (OUT / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    print("\n" + "=" * 68)
    print(f"  raw records retrieved : {len(all_records)}")
    print(f"  duplicates removed    : {dup_removed}")
    print(f"  unique records        : {len(deduped)}")
    print(f"  search log rows       : {len(search_rows)}")
    print(f"  future-dated (flagged): {n_future}")
    print("=" * 68)
    print("\nWrote:")
    print(f"  {OUT/'records.csv'}")
    print(f"  {OUT/'search_log.csv'}")
    print(f"  {OUT/'manifest.json'}")
    print(f"  {RAW}/  ({len(list(RAW.glob('*.json')))} raw API responses)")
    print("\nSend records.csv, search_log.csv and manifest.json back to Claude.")
    return 0


FIELDS_SEARCHED = {
    "arxiv": "All fields (all:) + submittedDate range",
    "openalex": "Title and abstract (title_and_abstract.search)",
    "s2": "Title and abstract (bulk search endpoint)",
    "ieee": "All metadata (querytext)",
    "scopus": "TITLE-ABS-KEY",
    "scholar": "All fields (Google Scholar default), via SerpAPI",
}

API_NOTE = {
    "arxiv": "arXiv API. Preprint server — expect high preprint proportion.",
    "openalex": "OpenAlex. Broad index spanning most publishers including ACM and IEEE.",
    "s2": "Semantic Scholar bulk search. Boolean syntax: + = AND, | = OR.",
    "ieee": "IEEE Xplore API.",
    "scopus": "Scopus API. Requires institutional network access.",
    "scholar": ("Google Scholar via SerpAPI (third-party proxy; Google publishes no "
                "official API). Scholar TRUNCATES queries past 256 characters, so a "
                "shortened string was sent — it is recorded verbatim above. Scholar's "
                "hit count is an unstable estimate that varies between identical runs "
                "and must not be treated as a reproducible identification number."),
}


if __name__ == "__main__":
    sys.exit(main())
