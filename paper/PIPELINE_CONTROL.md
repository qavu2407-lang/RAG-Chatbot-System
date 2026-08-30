# Pipeline Control

Running record of the systematic review pipeline. One section per phase.

---

## Phase 1: Raw API harvest

**Status:** Complete, with four documented gaps (see Limitations).
**Run date:** 2026-08-28, 05:02 UTC
**Script:** `paper/rag_harvest.py`
**Scope:** 2020-01-01 to present, uncapped (`--max-records 0`)

### 1.1 What was done

Four search concepts were run against five bibliographic APIs. Each concept is
rendered in each API's **own** query syntax, because every API parses booleans
differently and a silently mangled string produces a hit count that means nothing.
The exact string sent to each API is recorded verbatim in `search_log.csv` — not
the string that was intended.

| Query ID | Concept |
|---|---|
| `BASE` | Broad identification sweep across RAG architectures |
| `GAP-POSTRET` | Gap 1 — post-retrieval: reranking, context compression, repacking |
| `GAP-ORCH` | Gap 2 — orchestration: agentic RAG, self-reflection, adaptive routing |
| `GAP-GOV` | Gap 3 — governance: access control, prompt injection, provenance, audit |

Sources queried: arXiv, Semantic Scholar, Scopus, Google Scholar (via SerpAPI),
IEEE Xplore. OpenAlex is configured but was not run — see L3. Crossref was
removed from the pipeline entirely — see §1.6.

Nothing was invented. Where an API was unreachable or unconfigured, its row
states so and the hit count is left **blank**, never guessed and never zero.

### 1.2 Outputs

All in `out/`. `raw/` is git-ignored (~110 MB); the three derived files are tracked.

| File | Contents |
|---|---|
| `records.csv` | 20,186 deduplicated records — the screening set |
| `search_log.csv` | 20 rows, one per (database × query) — for the PRISMA search log |
| `manifest.json` | Run metadata: timestamps, counts, which API keys were present |
| `raw/` | 532 unmodified API responses — the audit trail |

`raw/` is **not** a copy of `records.csv`. It holds every API response exactly as
returned, before parsing, deduplication or filtering. `records.csv` is derived
from it. This is what allows every number in the search log to be re-derived
without re-running the searches — which matters because these hit counts drift
day to day. Raw Crossref responses are retained there even though Crossref left
the pipeline, so the removal decision stays auditable.

**Identification totals:** 41,618 records retrieved → 21,404 duplicates removed →
20,214 unique → **20,186** after Crossref removal.

#### Yield by source

| Source | Records | Title mentions RAG |
|---|---|---|
| Semantic Scholar | 17,623 | 44.1% |
| arXiv | 6,751 | 40.9% |
| Scopus | 6,717 | 38.6% |
| Google Scholar | 158 | 94.9% |
| IEEE Xplore | 0 | — (see L5) |

Sources sum to more than 20,186 because a record found in multiple databases is
retained once with all its sources listed. The percentages count titles only;
87% of records carry the anchor in title **or** abstract.

#### Record completeness

- Abstract present: 16,164 / 20,186 (80.1%)
- DOI or arXiv ID present: 19,569 / 20,186 (96.9%)
- Duplicate DOIs remaining: **0**
- Publication years: 2020 (123), 2021 (110), 2022 (113), 2023 (281), 2024 (2,804),
  2025 (7,968), 2026 (8,635), 2027 (152)

#### Future-dated records

152 records are dated 2027 and are **retained**, flagged in the
`publication_status` column as *"NOT YET OFFICIALLY PUBLISHED (dated 2027) —
REVIEW: cite only if you accept a forthcoming/online-first reference"*. Nothing
was dropped; the decision is left to human screening per record. Records dated
2026 are current-year and are not flagged.

### 1.3 Limitations

These are properties of the source APIs, not defects in the harvest. Each must be
carried into the PRISMA flow diagram and the methods section.

**L1 — Scopus is truncated at 5,000 records per query.**
Elsevier's search endpoint refuses `start > 5000`. The `BASE` query returned
5,000 of a true 13,396 hits. This is a structural per-query ceiling, **not** a
quota — it does not reset, and re-running changes nothing. The affected row
carries an explicit `TRUNCATED AT SCOPUS API CEILING` note stating the true total.

**L2 — Scopus abstracts are paywalled.**
The Scopus search endpoint's standard view omits abstracts, leaving 2,838 Scopus
records without one. The Abstract Retrieval API reaches HTTP 200 with the current
key, but only under `view=META`, which carries no abstract text; `view=META_ABS`
and `view=FULL` both return `401 AUTHORIZATION_ERROR`. This is a **subscription
entitlement**, not a key problem — no additional developer key resolves it.
Access requires an institutional Scopus subscription, exercised from the
institution's IP range (campus network or VPN) or via an Elsevier InstToken.
If entitlement becomes available, the right resource is
`/content/abstract/scopus_id/{scopus_id}`: the Scopus ID is already present in
the `url` column of every record, and it covers the 354 Scopus records that
carry no DOI and so cannot be looked up by DOI at all.

**L3 — OpenAlex is absent from this run.**
OpenAlex moved to a prepaid credit model priced at $0.001 per 200-record page,
with a ~$0.0001 floor per request. The free daily allowance is approximately
$0.09 — inferred from the 88 pages this run completed before exhaustion, and it
resets at midnight UTC. A full uncapped OpenAlex harvest needs 228 pages (~$0.23),
so it exceeds one day's free allowance but fits across three. The budget was exhausted mid-run and the API returned HTTP 429 with `"Insufficient budget"`. OpenAlex was excluded rather than left to fail on every query. This is a real coverage loss: in an earlier capped run it was the
**highest-precision source** at 84.3% on-topic titles.

**L4 — Google Scholar counts are estimates and paging is shallow.**
Google publishes no official API; SerpAPI is a third-party proxy. Two constraints:
Scholar **truncates queries beyond 256 characters** (so shortened strings were
sent, recorded verbatim in the log), and it refuses deep paging — SerpAPI
returned *"Google hasn't returned any results"* at around record 40 per query,
yielding 178 records against a claimed 40,170 hits. Scholar's totals are unstable
estimates that vary between identical runs and **must not** be used as
reproducible identification numbers. Cost: 12 of 250 monthly searches
(238 remaining, renews 2026-09-28).

**L5 — IEEE Xplore returned no data.**
`HTTP 403 — Developer Inactive`: the API key is issued but not yet approved.
With Crossref removed, the review currently has **no IEEE or ACM coverage** from
a dedicated source. Once approved, note that the key is documented as serving
only 08:00–17:00 EST, Mon–Fri, at 200 calls/day.

**L6 — Screening set is large, and 2,357 records are unjudgeable.**
20,186 records is beyond practical manual title/abstract screening. Of these,
2,357 carry no RAG anchor **and** no abstract, so they can be neither included
nor excluded on evidence until abstracts are enriched. Only 217 records are
confirmed off-topic (no anchor despite having an abstract).

### 1.4 Why OpenAlex

OpenAlex is the designated enrichment and coverage source for Phase 2. Three
alternatives were tested for filling the 3,426 missing abstracts that carry a
DOI, and all three failed:

| Route | Result |
|---|---|
| Semantic Scholar batch API | Resolves DOIs but returned **0 abstracts** — licensing restrictions |
| Crossref | **0 of 8** sampled DOIs carried an abstract |
| Scopus Abstract Retrieval | **401** on `META_ABS`/`FULL` — subscription-gated (L2) |
| **OpenAlex** | **41.3%** abstract coverage — the only non-zero result |

OpenAlex is chosen because it is the only source that returns abstracts for these
records at all, not because it returns many. Measured directly against a random
sample of 150 of the missing-abstract DOIs: 137 matched, **62 carried an abstract
(41.3%)**. On the harder unjudgeable subset the yield is 33%, of which 62% mention
RAG — about **399 records rescued** from 1,932.

An earlier draft of this section cited 88.8% coverage. That figure was wrong: it
was measured on records returned by OpenAlex's own *search*, a population that by
construction has abstracts, and it does not transfer to records sourced elsewhere.
The corrected expectation is 41.3%.

It also spans all publishers rather than one catalogue, so adding it as a source
repairs the ACM and IEEE gap left by removing Crossref, and it supports batched
DOI lookup — 50 DOIs per request, 69 requests for all 3,426 records, at a measured
**$0.0001 per request (~$0.007 total)**. Unlike the Scopus paywall it is not gated
behind an institutional subscription.

### 1.5 Next steps

Ordered by dependency.

1. **Add OpenAlex.** Purchase credits (~$0.23 for a full uncapped pull, ~$0.07
   more for abstract enrichment), then run `--sources openalex` and merge. A free
   alternative is `--max-records 400` after the midnight-UTC budget reset, which
   leaves OpenAlex a truncated sample.
2. **Enrich abstracts** for the 3,426 missing-abstract records that carry a DOI,
   via OpenAlex batched lookup. Shrinks the 2,357 unjudgeable records (L6).
3. **Re-test Scopus abstracts from the institutional network** (L2). Free if it
   works, and covers 2,838 records including the 354 with no DOI.
4. **Decide on Scopus query-splitting.** To recover the ~8,400 `BASE` hits lost
   to the 5,000 ceiling, partition by `PUBYEAR` so each sub-query returns under
   the ceiling. Adds search-log rows; a methodology change needing explicit sign-off.
5. **Re-run IEEE Xplore** once approved, inside its 08:00–17:00 EST weekday window.
   This restores the IEEE/ACM coverage lost with Crossref.
6. **Phase 2 clean sweep.** Tier the record set for manual screening. Current
   tiers: T1 5,016 (anchor in title + concept), T2 3,277 (title anchor only),
   T3 6,323 (abstract anchor + concept), T4 2,996 (abstract anchor only),
   T5 2,357 (unjudgeable), T6 217 (confirmed off-topic). Auto-exclude T6 only;
   use tiers to order screening, not to discard.
7. **Zip `records.csv`** after Phase 2 completes.
8. **Build the PRISMA flow diagram** from `search_log.csv`, carrying L1–L6
   forward as stated deviations.

### 1.6 Decisions log

**Crossref removed from the pipeline (2026-08-28).**
Crossref has no boolean operator support; `query.bibliographic` is bag-of-words
relevance ranking, so the intended boolean degraded into loose OR matching. It
reported 60,199 hits on `GAP-ORCH` while Scopus reported 1,562 for the same
concept, and **zero** of the 400 records it returned for that query mentioned RAG
anywhere in title or abstract. A local boolean re-filter was trialled and did
work (4,800 retrieved → 567 retained, ~100% precision), but the underlying hit
counts remained unusable as PRISMA identification numbers, so the source was
dropped rather than caveated.

Removal also took the ACM (member 320) and IEEE (member 263) publisher-scoped
passes, which ran through the same API. Net cost was **28 unique records**: the
other 523 Crossref-attributed records were duplicates already retrieved from
Scopus, Semantic Scholar or arXiv, and survive under those attributions. IEEE and
ACM coverage is restored by L5's IEEE Xplore key, not by Crossref.

Raw Crossref responses are retained in `out/raw/` so the decision stays auditable.
The `matches_boolean` filter is retained in the script with no caller: Phase 2
tiering reuses its anchor/concept regexes, and re-adding IEEE Xplore will need it.

**First exploratory run deleted.** Capped at 400 records/source/query (8,575
records, no local filter, no Google Scholar). Superseded and not part of the method.

### 1.7 Operational notes

- API keys live in `temp/.env` (git-ignored). The harvester checks the repo root
  then `temp/`, and warns if neither has a `.env`.
- Key names are upper-cased on load, so `ieee_api_key` and `IEEE_API_KEY` both work.
- **Do not move `out/` while a harvest is running.** The script resolves `out/`
  relative to the working directory on every write, so it will not follow the
  move — it silently creates a new one, splitting the audit trail. This happened
  during this run and was repaired; `arxiv_BASE` is verified contiguous p0–p67.
- `records.csv` is ~30 MB and is regenerated every run. Each run therefore adds a
  new ~30 MB blob to git history permanently; consider tracking only
  `search_log.csv` and `manifest.json`, or committing the zipped copy.
