#!/usr/bin/env python3
"""
screen_records.py — Phase 2. Split the harvested set into a screening set and an
excluded set, with a stated reason on every exclusion.

TIERS
-----
Relevance is judged on the RAG anchor alone ("retrieval-augmented generation",
"RAG"). A concept-term dimension was trialled and dropped: it ranked core RAG
architecture papers below peripheral ones, because a paper about RAG as a whole
names no single sub-concept.

    A  anchor in the title              — strongest evidence of topicality
    B  anchor in the abstract only
    C  no anchor, and no abstract       — unjudgeable, kept for manual screening
       Tier is independent of whether a record has an abstract; records without
       one carry a `reason` note saying so.
    D  no anchor despite having an abstract — excluded

EXCLUSIONS
----------
Applied in order, first match wins, so every excluded record carries exactly one
reason:

    1. non-English            (from classify_language.py, EXCLUDE flags only —
                               REVIEW flags stay in the screening set)
    2. out of date range      (before 2020; a blank year is never excluded)
    3. no RAG anchor          (tier D)

Nothing is deleted. Both files together reconstruct records.csv exactly.

USAGE
-----
    python paper/screen_records.py
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RECORDS = ROOT / "out" / "records.csv"
SCREENING = ROOT / "out" / "screening.csv"
EXCLUDED = ROOT / "out" / "excluded.csv"
REPORT = ROOT / "out" / "screening_report.json"

YEAR_FROM = 2020

# Deliberately wider than the harvester's search-time boolean, which requires the
# full phrase "retrieval-augmented generation". 226 papers write "retrieval-
# augmented framework / LLM / synergy" and never spell out "generation"; the
# strict phrase excluded all of them. Screening errs toward inclusion, because a
# wrong exclusion here is unrecoverable while a wrong inclusion is caught later.
ANCHOR = re.compile(r"retrieval[- ]augment|\bRAG\b|\bRAG-", re.I)

csv.field_size_limit(10 ** 7)


def tier_of(rec: dict) -> str:
    if ANCHOR.search(rec.get("title", "")):
        return "A"
    if ANCHOR.search(rec.get("abstract", "")):
        return "B"
    return "C" if not rec.get("abstract", "").strip() else "D"


def exclusion(rec: dict, tier: str) -> str:
    if rec.get("language_flag", "").startswith("EXCLUDE"):
        return rec["language_flag"].replace("EXCLUDE — ", "non-English: ")
    year = rec.get("year", "").strip()
    if year.isdigit() and int(year) < YEAR_FROM:
        return f"out of date range (dated {year}, review covers {YEAR_FROM}+)"
    if tier == "D":
        return "no RAG anchor in title or abstract"
    return ""


def note(rec: dict, tier: str) -> str:
    notes = []
    if rec.get("language_flag", "").startswith("REVIEW"):
        notes.append(rec["language_flag"].replace("REVIEW — ", "language: "))
    # Tier A needs this too: the anchor was in the title, so the tier stands,
    # but a screener has to know the judgement was made without an abstract.
    if not rec.get("abstract", "").strip():
        notes.append("no abstract — tier assigned from the title alone" if tier == "A"
                     else "no abstract — screen on title, or retrieve full text")
    if rec.get("publication_status", "").strip():
        notes.append("not yet officially published")
    return "; ".join(notes)


def main() -> int:
    rows = list(csv.DictReader(RECORDS.open(encoding="utf-8")))
    if not rows:
        sys.exit(f"{RECORDS} is empty — run the harvest first.")

    keep, drop = [], []
    tiers, reasons = Counter(), Counter()
    for rec in rows:
        tier = tier_of(rec)
        tiers[tier] += 1
        reason = exclusion(rec, tier)
        if reason:
            drop.append({**rec, "tier": tier, "reason": reason})
            reasons[reason.split(":")[0].split("(")[0].strip()] += 1
        else:
            keep.append({**rec, "tier": tier, "reason": note(rec, tier)})

    # Highest-evidence first, then newest — the order a human should screen in.
    keep.sort(key=lambda r: (r["tier"], -int(r["year"] or 0)))

    fields = list(rows[0].keys()) + ["tier", "reason"]
    for path, data in ((SCREENING, keep), (EXCLUDED, drop)):
        with path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(data)

    assert len(keep) + len(drop) == len(rows), "records lost in the split"
    assert {r["record_id"] for r in keep}.isdisjoint(r["record_id"] for r in drop)

    kept_tiers = Counter(r["tier"] for r in keep)
    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "records_in": len(rows),
        "screening_set": len(keep),
        "excluded": len(drop),
        "tiers_all_records": dict(sorted(tiers.items())),
        "tiers_screening_set": dict(sorted(kept_tiers.items())),
        "exclusion_reasons": dict(reasons.most_common()),
        "flagged_for_manual_attention": sum(1 for r in keep if r["reason"]),
    }
    REPORT.write_text(json.dumps(report, indent=2))

    print(f"records in     : {len(rows)}")
    print(f"screening set  : {len(keep)}")
    print(f"excluded       : {len(drop)}")
    print("\ntiers (screening set):")
    for t, n in sorted(kept_tiers.items()):
        print(f"  {t}  {n:>7}")
    print("\nexclusion reasons:")
    for r, n in reasons.most_common():
        print(f"  {r:<42}{n:>7}")
    print(f"\nwrote {SCREENING}\nwrote {EXCLUDED}\nwrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
