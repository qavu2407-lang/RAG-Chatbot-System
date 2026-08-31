# Pipeline Control

Running record of the systematic review pipeline. One section per phase.

---

## Phase 1: Bibliographic harvest

**Status:** Complete.
**Run date:** 2026-08-31
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

| Query ID | Concept |
|---|---|
| `BASE` | Broad identification sweep across RAG architectures |
| `GAP-POSTRET` | Gap 1 — post-retrieval: reranking, context compression, repacking |
| `GAP-ORCH` | Gap 2 — orchestration: agentic RAG, self-reflection, adaptive routing |
| `GAP-GOV` | Gap 3 — governance: access control, prompt injection, provenance, audit |

Sources: **OpenAlex, Semantic Scholar, arXiv, Scopus, IEEE Xplore, Google
Scholar** (via SerpAPI). Records are deduplicated on DOI, then arXiv ID, then
normalised title; a record found in several databases is kept once with all its
sources listed.

Nothing is invented. Where an API was unreachable or returned an error, its row
says so and the hit count is left **blank** — never guessed, never zero.

### 1.2 Results

**28,689 unique records.**

| Source | Records | Share |
|---|---|---|
| OpenAlex | 25,267 | 88.1% |
| Semantic Scholar | 17,551 | 61.2% |
| arXiv | 6,751 | 23.5% |
| Scopus | 6,717 | 23.4% |
| IEEE Xplore | 3,866 | 13.5% |
| Google Scholar | 158 | 0.6% |

Shares exceed 100% because most records are found in more than one database.

| Metric | Value |
|---|---|
| Abstract present | 25,838 / 28,689 (90.1%) |
| DOI or arXiv ID present | 26,892 / 28,689 (93.7%) |
| Duplicate DOIs remaining | 0 |
| Raw API responses retained | 791 |

Publication years: 2020 (181), 2021 (141), 2022 (173), 2023 (360), 2024 (3,062),
2025 (10,415), 2026 (14,314), 2027 (42), 2028 (1).

### 1.3 Outputs

All in `paper/out/`. `raw/` is git-ignored (~300 MB); the derived files are tracked.

| File | Contents |
|---|---|
| `records.csv` | 28,689 deduplicated records — the screening set |
| `search_log.csv` | 24 rows (6 databases × 4 queries) for the PRISMA search log |
| `manifest.json` | Run metadata, inclusion criteria, known limitations |
| `enrichment_report.json` | Abstract-enrichment outcome |
| `language_report.json` | Language-classification outcome |
| `raw/` | 791 unmodified API responses — the audit trail |

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
| English — include | 27,659 |
| Non-English — exclude | 667 |
| Flagged for human review | 363 |

Two design decisions worth recording:

**A keyword heuristic was rejected.** An English-function-word test misclassified
552 records — short English titles with no abstract, e.g. *"Towards Personalized
Oncology Rehabilitation: A Hybrid LLM–Knowledge Graph"*, contain no function
words at all. Excluding ~500 English papers is a worse error than one dependency.

**Mixed-language records go to review, not exclusion.** 309 records carry an
English title over a non-English abstract. Many national journals publish an
English title for a locally-written paper, but some publish English full text
with a local abstract. These are flagged `REVIEW`: a wrong exclusion is
unrecoverable, while a wrong inclusion is caught at screening. A further 54
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

**L2 — Scopus abstracts are subscription-gated.** The search endpoint's standard
view omits abstracts. The Abstract Retrieval API returns HTTP 200 under
`view=META` (no abstract text); `META_ABS` and `FULL` both return
`401 AUTHORIZATION_ERROR`. This is an entitlement, not a key problem — no
additional developer key resolves it. Access needs an institutional subscription
exercised from the institution's IP range, or an Elsevier InstToken. If it
becomes available, the right resource is `/content/abstract/scopus_id/{id}`: the
Scopus ID is already in each record's `url`, and it covers the Scopus records
that carry no DOI and so cannot be looked up by DOI.

**L3 — Google Scholar counts are estimates and paging is shallow.** Google
publishes no official API; SerpAPI is a third-party proxy. Scholar truncates
queries beyond 256 characters (shortened strings were sent, recorded verbatim in
the log) and refuses deep paging — SerpAPI returns *"Google hasn't returned any
results"* at around record 40 per query, yielding 178 records against a claimed
40,170 hits. **Scholar's totals must not be used as reproducible identification
numbers.**

**L4 — 2,851 records have no abstract.** Down from 4,008 after enrichment
(§1.5). What remains, and why:

| Block | Records | Status |
|---|---|---|
| Springer `10.1007` | 962 | Recoverable — Springer daily quota was exhausted mid-pass |
| No identifier at all | 554 | Unreachable: nothing to look up by |
| Elsevier `10.1016` | 504 | Needs a Scopus entitlement (L2) |
| SSRN `10.2139` | 206 | Crossref holds no abstract for these |
| ResearchGate / Zenodo / arXiv | 174 | arXiv's 36 are recoverable once it stops throttling |

Only the 554 with no identifier are structurally unreachable. These records
cannot be screened on abstract evidence and must be judged on title alone.

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
| OpenAlex | 3,454 | 0 | Batched 50 DOIs/request. Fills nothing once OpenAlex is harvested as a source — every abstract it holds already arrives with the record. |
| Crossref | 3,454 | 668 | One DOI/request. Strong on preprint registrants: SSRN and Research Square return an abstract almost every time. |
| Springer | 1,451 | 489 | One DOI/request, `10.1007` only. ~99% hit rate when it responds; stopped on its daily quota with 962 candidates unprocessed. |

Coverage rose from 86.0% to **90.1%**. `abstract_source` records which provider
supplied each abstract: original 24,370, Crossref 668, Springer 489, OpenAlex 311.

**Crossref remains valid for DOI lookup.** It was removed as a *search* source
because it has no boolean operators (above) — but metadata retrieval by DOI is a
different capability, unaffected by that limitation, and it supplied the single
largest share of enriched abstracts. An early sample suggesting Crossref held no
abstracts was drawn from a Scopus-heavy subset and was not representative.

**Springer signals overload by dropping connections, not by HTTP 429.** At
0.4s/request the first pass lost 1,035 of 1,451 records to connection errors
while continuing to report success. The delay is now 1.0s with one retry. Its
free tier also enforces a daily quota that ends the pass outright.

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

1. **Consolidated single-pass re-run** — planned, to regenerate every artifact
   from one execution.
2. **Scopus query-splitting** — partition `BASE` by `PUBYEAR` to recover the
   ~8,400 hits lost to the 5,000 ceiling (L1). Methodology change; needs sign-off.
3. **Finish the Springer pass** — `--providers springer` after the daily quota
   resets; 962 candidates at a ~99% hit rate, taking coverage to roughly 93.5%.
4. **Scopus abstracts** — retry from the institutional network (L2); would add
   the 504 Elsevier records.
5. **Phase 2 screening sweep** — tier the record set and emit `screening.csv` and
   `excluded.csv` with reasons. Current evidence tiers: A 10,796 (RAG anchor in
   title), B 14,904 (anchor in abstract), C 1,647 (no anchor, no abstract —
   unjudgeable), D 1,342 (no anchor despite an abstract — excludable).
6. **Zip `records.csv`** after Phase 2.
7. **PRISMA flow diagram** from `search_log.csv`, carrying L1–L5 forward as
   stated deviations.
