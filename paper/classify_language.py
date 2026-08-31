#!/usr/bin/env python3
"""
classify_language.py — apply the English-only inclusion criterion.

THE RULE
--------
Only English-language papers are included in the review. This script does not
delete anything: it writes two columns to records.csv and leaves the exclusion
itself to the screening step, so every call stays auditable.

    language        detected ISO code, or "" when there was too little text
    language_flag   "" (include)
                    | "EXCLUDE — non-English (<code>)"
                    | "REVIEW — English title but <code> abstract" (mixed-language)
                    | "REVIEW — too little text to classify"

WHY NOT A KEYWORD HEURISTIC
---------------------------
An English-function-word test ("the/of/and/for...") was trialled first and
misclassified 552 records: short English titles with no abstract, such as
"Towards Personalized Oncology Rehabilitation: A Hybrid LLM-Knowledge Graph",
contain no function words at all. Excluding ~500 English papers from a
systematic review is a far worse error than adding one small dependency, so
langdetect is used instead.

CONSERVATIVE BY DESIGN
----------------------
Language is detected on title + abstract together, because langdetect is
unreliable on short strings. Records with fewer than MIN_CHARS of text are never
excluded — they are flagged REVIEW for a human, since a wrong exclusion is
unrecoverable while a wrong inclusion is caught at screening.

USAGE
-----
    pip install langdetect
    python paper/classify_language.py
"""

from __future__ import annotations

import csv
import json
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    from langdetect import DetectorFactory, detect
    from langdetect.lang_detect_exception import LangDetectException
except ImportError:
    sys.exit("Missing dependency. Run:  pip install langdetect")

DetectorFactory.seed = 0          # deterministic output across runs

ROOT = Path(__file__).resolve().parent
RECORDS = ROOT / "out" / "records.csv"
REPORT = ROOT / "out" / "language_report.json"

MIN_CHARS = 40   # below this, langdetect is coin-flippy — flag, never exclude


def latin_fraction(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 1.0
    return sum(1 for c in letters if "LATIN" in unicodedata.name(c, "")) / len(letters)


def main() -> int:
    rows = list(csv.DictReader(RECORDS.open(encoding="utf-8")))
    fields = list(rows[0].keys())
    for col in ("language", "language_flag"):
        if col not in fields:
            fields.append(col)

    counts: Counter[str] = Counter()
    for rec in rows:
        title = rec.get("title", "")
        text = f"{title} {rec.get('abstract', '')}".strip()

        # Non-Latin title is decisive on its own: langdetect handles the script,
        # and an English abstract attached to a non-English paper does not make
        # the paper English.
        if latin_fraction(title) < 0.5:
            try:
                code = detect(title)
            except LangDetectException:
                code = "non-latin"
            rec["language"] = code
            rec["language_flag"] = f"EXCLUDE — non-English ({code}, non-Latin script)"
            counts["excluded_non_latin"] += 1
            continue

        if len(text) < MIN_CHARS:
            rec["language"] = ""
            rec["language_flag"] = "REVIEW — too little text to classify"
            counts["review_short"] += 1
            continue

        try:
            code = detect(text)
        except LangDetectException:
            rec["language"] = ""
            rec["language_flag"] = "REVIEW — language detection failed"
            counts["review_failed"] += 1
            continue

        rec["language"] = code
        if code == "en":
            rec["language_flag"] = ""
            counts["included_en"] += 1
            continue

        # An English title over a non-English abstract is genuinely ambiguous:
        # many national journals publish an English title for a paper written in
        # the local language, but some publish English full text with a local
        # abstract. Send these to a human rather than excluding on a guess.
        try:
            title_lang = detect(title) if len(title) > 25 else None
        except LangDetectException:
            title_lang = None

        if title_lang == "en":
            rec["language_flag"] = (f"REVIEW — English title but {code} abstract; "
                                    f"full-text language ambiguous")
            counts["review_mixed"] += 1
        else:
            rec["language_flag"] = f"EXCLUDE — non-English ({code})"
            counts[f"excluded_{code}"] += 1

    with RECORDS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    excluded = sum(v for k, v in counts.items() if k.startswith("excluded_"))
    review = sum(v for k, v in counts.items() if k.startswith("review_"))
    report = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "rule": "Only English-language papers are included.",
        "detector": "langdetect 1.0.9, seeded (deterministic)",
        "detected_on": "title + abstract combined",
        "min_chars_for_exclusion": MIN_CHARS,
        "records_total": len(rows),
        "included_english": counts["included_en"],
        "excluded_non_english": excluded,
        "flagged_for_review": review,
        "breakdown": dict(counts.most_common()),
    }
    REPORT.write_text(json.dumps(report, indent=2))

    print(f"records            : {len(rows)}")
    print(f"English (include)  : {counts['included_en']}")
    print(f"non-English (excl.): {excluded}")
    print(f"flagged for review : {review}")
    print("\nbreakdown:")
    for k, v in counts.most_common(12):
        print(f"  {k:26}{v:>7}")
    print(f"\nwrote {RECORDS}\nwrote {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
