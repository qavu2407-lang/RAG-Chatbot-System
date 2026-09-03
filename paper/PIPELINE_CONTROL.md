# Pipeline Control

Running record of the systematic review pipeline. One section per phase.

---

## Phase 1: Bibliographic harvest

**Status:** Complete.
**Run date:** 2026-09-03
**Scope:** 2020-01-01 to present, uncapped
**Scripts:** `rag_harvest.py` → `enrich_abstracts.py` → `classify_language.py`
**Assumptions**: This data collection method assumes OpenAlex account has enough credits 
to finish everything in one run, and other API keys has no hard cap except for Scorpus.

### 1.1 Method

Four search concepts were run against six bibliographic APIs. Each concept is
rendered in each API's **own** query syntax, because every API parses booleans
differently and a silently mangled string produces a hit count that means
nothing. The exact string sent to each API is recorded verbatim in
`search_log.csv` — not the string that was intended.

Sources: **OpenAlex, Semantic Scholar, arXiv, Scopus, IEEE Xplore, Google
Scholar** (via SerpAPI). Records are deduplicated on DOI, then arXiv ID, then
normalised title; a record found in several databases is kept once with all its
sources listed.

Nothing is invented. Where an API was unreachable or returned an error, its row
says so and the hit count is left **blank** — never guessed, never zero.

### 1.2 Results

**28,905 unique records.**

| Source | Records | Share |
|---|---|---|
| OpenAlex | 25,412 | 87.9% |
| Semantic Scholar | 17,645 | 61.0% |
| arXiv | 6,751 | 23.4% |
| Scopus | 6,717 | 23.2% |
| IEEE Xplore | 3,910 | 13.5% |
| Google Scholar | 707 | 2.4% |

Shares exceed 100% because most records are found in more than one database.

| Metric | Value |
|---|---|
| Abstract present | 26,462 / 28,905 (91.5%) |
| DOI or arXiv ID present | 27,057 / 28,905 (93.6%) |
| Duplicate DOIs remaining | 0 |
| Raw API responses retained | 774 |

Publication years: 2020 (181), 2021 (141), 2022 (174), 2023 (357), 2024 (3,046),
2025 (10,436), 2026 (14,521), 2027 (42), 2028 (1).

### 1.3 Outputs

All in `paper/out/`. `raw/` is git-ignored (~300 MB); the derived files are tracked.

| File | Contents |
|---|---|
| `records.csv` | 28,905 deduplicated records — the screening set |
| `search_log.csv` | 24 rows (6 databases × 4 queries) for the PRISMA search log |
| `manifest.json` | Run metadata, inclusion criteria, known limitations |
| `enrichment_report.json` | Abstract-enrichment outcome |
| `language_report.json` | Language-classification outcome |
| `raw/` | 774 unmodified API responses — the audit trail |

`raw/` is **not** a copy of `records.csv`. It holds every API response exactly as
returned, before parsing, deduplication or filtering, so every number in the
search log can be re-derived without re-running the searches — which matters
because these hit counts drift day to day.

### 1.4 Inclusion criteria

**Date range:** 2020-01-01 to present.

**Language: English only.** Applied by `classify_language.py` using seeded
(deterministic) `langdetect` over title + abstract. Nothing is deleted; the
result is recorded in `language` and `language_flag` so every call is auditable.

| Outcome | Records |
|---|---|
| English — include | 27,871 |
| Non-English — exclude | 667 |
| Flagged for human review | 367 |

Two design decisions worth recording:

**A keyword heuristic was rejected.** An English-function-word test misclassified
552 records — short English titles with no abstract, e.g. *"Towards Personalized
Oncology Rehabilitation: A Hybrid LLM–Knowledge Graph"*, contain no function
words at all. Excluding ~500 English papers is a worse error than one dependency.

**Mixed-language records go to review, not exclusion.** 317 records carry an
English title over a non-English abstract. Many national journals publish an
English title for a locally-written paper, but some publish English full text
with a local abstract. These are flagged `REVIEW`: a wrong exclusion is
unrecoverable, while a wrong inclusion is caught at screening. A further 50
records have too little text to classify and are likewise flagged, not excluded.

**Future-dated records are retained.** 43 records dated beyond the current year
are flagged in `publication_status` as *"NOT YET OFFICIALLY PUBLISHED"* for human
review rather than dropped.

### 1.5 Limitations

Properties of the source APIs, not defects in the harvest. Each must be carried
into the PRISMA flow diagram and the methods section.

**L1 — Scopus is truncated at 5,000 records per query.** Elsevier's search
endpoint refuses `start > 5000`. `BASE` returned 5,000 of a true 13,396 hits.
This is a structural per-query ceiling, **not** a quota — it does not reset, and
re-running changes nothing. Recovering the remainder requires partitioning the
query by `PUBYEAR`. The affected row carries an explicit `TRUNCATED` note.

**L2 — Scopus abstracts are unavailable.** The search endpoint's standard view
omits abstracts. The Abstract Retrieval API returns HTTP 200 under `view=META`
(no abstract text); `META_ABS` and `FULL` both return `401 AUTHORIZATION_ERROR`.
This is a subscription entitlement, not a key problem — no developer key resolves
it, and institutional network access was attempted without success. These
abstracts stay unfilled and the affected records are screened on title alone.

**L3 — Google Scholar counts are estimates and paging is shallow.** Google
publishes no official API; SerpAPI is a third-party proxy. Scholar truncates
queries beyond 256 characters (shortened strings were sent, recorded verbatim in
the log) and refuses deep paging — SerpAPI returns *"Google hasn't returned any
results"* at around record 40 per query, yielding 178 records against a claimed
40,170 hits. **Scholar's totals must not be used as reproducible identification
numbers.**

**L4 — 2,443 records have no abstract.** What remains:

| Block | Records |
|---|---|
| Springer `10.1007` | 567 |
| No identifier at all | 549 |
| Elsevier `10.1016` | 504 |
| SSRN `10.2139` | 201 |
| Other (long tail) | 622 |

These records are screened on title alone.

**L5 — OpenAlex counts drift during paging.** The reported `count` changes while
a cursor walk is in progress, so retrieved totals may differ from it by a
fraction of a percent in either direction. The retrieved figure is authoritative.

### 1.6 Decisions log

**Crossref removed.** Crossref has no boolean operator support;
`query.bibliographic` is bag-of-words relevance ranking, so the intended boolean
degraded into loose OR matching. It reported 60,199 hits on `GAP-ORCH` while
Scopus reported 1,562 for the same concept, and **zero** of the 400 records it
returned for that query mentioned RAG anywhere in title or abstract. A local
boolean re-filter was trialled and did work (4,800 retrieved → 567 retained,
~100% precision), but the underlying hit counts remained unusable as PRISMA
identification numbers, so the source was dropped rather than caveated. Removal
also took the ACM (member 320) and IEEE (member 263) publisher-scoped passes that
ran through the same API; IEEE coverage is now served directly by IEEE Xplore.
Raw Crossref responses are retained in `out/raw/` so the decision stays auditable.

**OpenAlex requires an API key, not `mailto`.** `mailto` only requests the polite
pool. Without `api_key`, calls are billed to an anonymous **$0.10/day** bucket;
with it, the account's **$1/day** budget applies and prepaid credit is drawn only
after that. A full harvest costs **$0.231** and fits inside the daily budget. Both
Both `rag_harvest.py` and `enrich_abstracts.py` send the key.

**Abstract enrichment uses three providers, not one.** No single free source
covers the gap, so `enrich_abstracts.py` runs OpenAlex → Crossref → Springer in
sequence, each only on the records still missing an abstract after the previous
pass. Measured yield over 4,008 candidates:

| Provider | Candidates | Filled | Notes |
|---|---|---|---|
| OpenAlex | 3,079 | 21 | Batched 50 DOIs/request. Nearly everything it holds already arrives with the harvest. |
| Crossref | 3,058 | 673 | One DOI/request. Strong on preprint registrants: SSRN and Research Square. |
| Springer | 1,058 | 491 | One DOI/request, `10.1007` only. ~98% hit rate; stopped on its daily quota. |

Abstract coverage is **91.5%**. `abstract_source` records which provider supplied
each abstract: original 24,862, Crossref 680, Springer 641, OpenAlex 279.

**Crossref remains valid for DOI lookup.** It was removed as a *search* source
because it has no boolean operators (above) — but metadata retrieval by DOI is a
different capability, unaffected by that limitation, and it supplied the single
largest share of enriched abstracts. 

### 1.7 Operational notes

- API keys live in `temp/.env` (git-ignored). Scripts check the repo root then
  `temp/`, and warn if neither has a `.env`. Names are upper-cased on load, so
  `openalex_api_key` and `OPENALEX_API_KEY` both work.
- `OUT` is anchored to the script, not the caller's cwd — a cwd-relative path
  silently creates a second output tree. `--out-dir` covers side runs.
- **`dedupe()` backfills only `FIELDS`.** Columns added by later scripts
  (`abstract_source`, `language`, `language_flag`) are not carried across a merge,
  and `publication_status` can survive with a stale year. Re-run
  `classify_language.py` after any merge.
- **`dedupe()` joins whole `source_database` strings, not individual tokens.**
  Merging an already-merged set produces duplicates such as
  `"OpenAlex; OpenAlex; Scopus"`. Harmless in a single run; a repair pass is
  required after any merge.
- arXiv rate-limits aggressively under repeated runs (HTTP 429 through all
  retries). It asks for ≥3s between calls and wants ~68 sequential pages for
  `BASE`; allow recovery time between full runs.

### 1.8 Next steps

1. **PRISMA flow diagram** from `search_log.csv` and `screening_report.json`,
   carrying L1–L5 forward as stated deviations.

---

## Phase 2: Screening sweep

**Status:** Complete.
**Run date:** 2026-09-03
**Script:** `screen_records.py`

### 2.1 Method

Every record is assigned an evidence **tier** from where the RAG anchor appears,
then either kept for screening or excluded with one stated reason. Nothing is
deleted: `screening.csv` and `excluded.csv` together reconstruct `records.csv`
exactly, and that identity is asserted on every run.

| Tier | Definition | Meaning |
|---|---|---|
| A | Anchor in the **title** | Strongest evidence of topicality |
| B | Anchor in the **abstract** only | Topical, weaker signal |
| C | No anchor, **and no abstract** | Unjudgeable — kept, screened on title |
| D | No anchor **despite** an abstract | Excluded |

Exclusions apply in order and stop at the first match, so each excluded record
carries exactly one reason:

1. **Non-English** — `EXCLUDE` flags from `classify_language.py`. `REVIEW` flags
   stay in the screening set by design.
2. **Out of date range** — before 2020. A blank year is never excluded.
3. **No RAG anchor** — tier D.

### 2.2 Results

**27,089 records to screen, 1,816 excluded**, from 28,905.

| Tier | Screening set |
|---|---|
| A | 11,776 |
| B | 14,028 |
| C | 1,285 |

| Exclusion reason | Records |
|---|---|
| No RAG anchor in title or abstract | 1,149 |
| Non-English | 667 |
| Out of date range | 0 |

No record predates 2020, so the date criterion excluded nothing — the harvest
already applied it at query time. **1,621 records carry a `reason` note** while
staying in the screening set: 1,285 have no abstract and 392 are
language-ambiguous or not yet officially published (56 carry both).

### 2.3 Outputs

| File | Contents |
|---|---|
| `screening.csv` | 27,089 records, sorted tier then newest — the screening order |
| `excluded.csv` | 1,816 records, each with its single exclusion reason |
| `screening_report.json` | Tier and exclusion counts for the PRISMA flow |
| `records.csv.zip` | Compressed `records.csv` (50 MB → 17 MB); git-ignored, being derivable from a tracked file |

Both CSVs carry the full 16-column record schema plus `tier` and `reason`.

### 2.4 Decisions log

**The screening anchor is wider than the search anchor.** The harvester's boolean
requires the full phrase *retrieval-augmented generation*. Hundreds of papers write
*retrieval-augmented framework / LLM / synergy* and never spell out
"generation" — including *"MARS: Multi-agent Retrieval-Augmented Synergy"* and
*"Graph Retrieval-Augmented Language Model for Question Answering of Vietnamese
Law"* — and the strict phrase excluded or left unjudgeable every one of them. Screening therefore
matches `retrieval[- ]augment`, because a wrong exclusion at this stage is
unrecoverable while a wrong inclusion is caught at full-text review. 315 records
match the wider anchor but not the strict phrase: 222 left tier D — 221 of them
leaving the excluded set, the last one non-English anyway — and 93 left tier C
for a judgeable tier.

**The concept-term dimension was dropped.** A six-tier scheme crossing the anchor
with per-query concept terms was trialled and abandoned: it ranked core RAG
architecture papers *below* peripheral ones, because a paper about RAG as a whole
names no single sub-concept. Tier depends on the anchor alone.

**Tier C is kept, not excluded.** 1,285 records have neither an anchor nor an
abstract to judge by. They are unjudgeable, not irrelevant — most are conference
proceedings volumes and Scopus records whose abstracts are paywalled (L2). They
are screened on title, or by retrieving the full text.

### 2.5 Next steps

1. **Add a Semantic Scholar provider to `enrich_abstracts.py`.** S2 holds
   abstracts for the ACM (`10.1145`) tail that Crossref has no deposit for —
   8 of 20 sampled, ~12 records. Batched 500 DOIs/request, free, no key. It is
   the only remaining provider that adds anything: sampling confirmed S2 holds
   **no** abstract for Elsevier or Springer DOIs, and does not index SSRN or
   ResearchGate at all.
2. **Re-run Springer and the new S2 pass once the rate limit resets.** Springer
   stopped on its free-tier daily quota with **567 candidates never attempted**,
   at a ~98% hit rate on the 491 it did reach. This is the single largest
   recoverable block; together with the ACM tail it takes abstract coverage from
   91.5% to roughly 93.5%. Everything remaining after that is a publisher wall,
   not a quota — see L4.
3. **Re-run the screening sweep after the new enrichment**, so tiers reflect the
   filled abstracts. A record whose title carries the RAG anchor is tier A
   whether or not it has an abstract — that is already how `tier_of()` works, and
   1,127 of the 2,443 abstract-less records are tier A today. Extend the `reason`
   note so those records say so explicitly, rather than only tier C carrying the
   no-abstract note: the tier is unaffected, but a screener needs to know the
   judgement was made on the title alone.
4. **Title/abstract screening** of the 27,089, tier A first.
5. **PRISMA flow diagram** — identification 75,592 → deduplication 28,905 →
   screening 27,089, with the 1,816 exclusions itemised by reason.
