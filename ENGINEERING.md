# ENGINEERING.md — how BriefCortex actually works

This is the detailed companion to `README.md`. The README describes what the
product is and how to run it; this document describes how it is built, what each
stage guarantees, and why the choices were made that way. Every claim here is
followed back to a file.

---

## 1. Request lifecycle

```
POST /api/briefings
  └─ briefings row, status=pending          repositories/briefing_repository.py
  └─ background task                        services/briefing_service.py
       ├─ evidence reuse (incremental)      repositories/… → EvidenceRepository
       ├─ retrieval                         services/ingestion.py
       ├─ staged generation                 services/report_stages.py
       ├─ pre-delivery audit                services/report_audit.py
       └─ status=completed, report JSONB    repositories/briefing_repository.py
GET /api/briefings/{id}                      polled by the UI every 1.5s
```

A job stuck in `processing` past `STALE_JOB_MINUTES` is failed on next startup,
so a crashed worker cannot leave the UI polling forever.

## 2. Evidence window

`services/window.py`.

The window is **30 calendar dates ending on the meeting date**, not on today.
A meeting six days out therefore has 24 observable days; the remaining 6 are
fetched closer to the meeting. A meeting further out than the window itself has a
window that has not started, so the latest 30 days are read and the brief is
labelled `preliminary` rather than reported as "no coverage".

Incremental retrieval is driven by a watermark: `evidence_coverage.searched_through`
records the last date actually searched, so a second run fetches the increment and
does not pay twice for a quiet day.

## 3. Retrieval

### 3.1 Planning

`services/search_planner.py`. The model chooses queries and **categories**, never
domains. Categories resolve to domains through `SOURCE_REGISTRY`, so a
hallucinated host cannot reach the search API.

### 3.2 Lanes run concurrently

`services/ingestion.py::_search_plan`. Four lanes open in parallel:

| Lane | Domain scope | Dated | Purpose |
|---|---|---|---|
| news | registry categories + national press | yes | reporting |
| open | none (open web) | no | legal/regulatory, careers, **counterparty** |
| linkedin | `linkedin.com/jobs` | no | postings |
| local | national press only, in that country's language | yes | national-language reporting |

Measured: sequential 27.73s → parallel 12.20s (−56%). Each lane folds into its
own list and the lists merge afterwards, so dedup does not depend on which lane
finished first.

**Why the undated lanes exist.** A careers page and a regulator's case page carry
no publish date, so a date-bounded search drops all of them. The cost is that
those lanes can return genuinely old articles, which is why
`_drop_out_of_window` runs afterwards and removes anything published outside the
window. Undated results are kept: dropping them would defeat the reason the lane
exists.

**The counterparty lane.** `services/counterparty.py` reads the meeting objective
for the other company's name — capitalised phrases after *from / with / against /
vs / to*, minus a stop list so "from current levels" is not read as a firm called
Current — and adds queries about **that company's own condition** (revenue,
capacity, pricing pressure, customer contracts, restructuring) to the open lane. A
relationship query (`Canada Goose YKK zipper pricing`) returns the briefed company
and mentions the supplier in passing; measured on a real brief, the supplier side
was never looked up at all.

### 3.2b Where retrieval stops

Four bounds, whichever fires first. The one that actually fires is diminishing
returns, not the round cap.

| Bound | Constant | Value |
|---|---|---|
| Opening round | — | one concurrent round per lane |
| Follow-up rounds | `MAX_SEARCH_ROUNDS`, `MAX_EXTRA_QUERIES` | 8 rounds, 6 queries each |
| Wall clock | `SEARCH_BUDGET_SECONDS` | 45s |
| Diminishing returns | `MAX_STALE_ROUNDS` | 2 consecutive rounds with no new URL |
| Per query | `TAVILY_MAX_RESULTS` | 20, then the publisher filter |

Measured on a four-lane run with 28 planned queries:

| Round | Usable | New URLs | Total |
|---|---|---|---|
| opening | 101 | 75 | 75 |
| 2 | 4 | 0 | 75 |
| 3 | 3 | 2 | 77 |
| 4 | 5 | 0 | 77 |
| 5 | 0 | 0 | 77 → stop (`stale_streak=2`) |

The opening round produced 75 of the 77 URLs; the four follow-up rounds bought
two articles in 33 seconds. The plan ran dry (`pending_queries=0`) before either
the round cap or the budget was reached. **The constraint on coverage is the
supplied query list, not these constants** — which is why the planner is asked
for objective-specific queries and why the counterparty lane was added.

### 3.2c Replanning once when the opening round comes back thin

`search_planner.replan_after_empty`, called from `ingestion._supplement_thin_round`.
When the opening lanes return fewer than `SUPPLEMENT_BELOW_USABLE = 6` usable
bodies, the plan itself was the problem, so the model is shown what already failed
— the queries, the domains, the per-query result counts — and asked for a
different angle:

| Order | What it changes |
|---|---|
| 1 | the subject: legal name, ticker, parent, subsidiary, brand |
| 2 | the topic: a broader angle instead of the one that failed |
| 3 | the kind of source: trade press, regulator page, company site |
| 4 | nothing — reports that the window holds no coverage |

Then the round is executed **against the trusted national press list first**, with
the open web behind it. That list is what `media_trust.resolve_policy` learned for
the company's own country — the model's reading of which publications actually
cover a company there, stored once per country. This is the same list the opening
round already uses inside its `news` lane; a supplement narrows to it because the
failure mode being repaired is "the international wire list does not cover this
company", and its national press does. Measured: an English query against Japanese
titles returned one usable body (the English edition), while a query in Japanese
against the same titles returned nine. If the national list yields nothing the same
queries run again without a domain restriction.

Constraints:

- **Once per request.** A company nobody writes about must not spend a model call
  and a Tavily round on every run (`SUPPLEMENT_MAX_QUERIES = 3` bounds the round).
- **Undated.** `_drop_out_of_window` still rejects anything outside the window
  afterwards.
- **Not a retry.** A query already tried is removed from the reply, because
  repeating a search that returned little returns little again.
- **An empty answer is honoured.** `window_too_narrow=True` or `queries=[]` are
  both valid, and neither triggers another search. The brief then says the
  coverage was thin and delivers `checks_to_run` — five things the reader can
  obtain directly from a registry, which is more useful than a second thin round.

### 3.3 Reading

`services/publisher_filter.py`. Two rule tables, both verified against live pages:

- `SITE_SECTION_RULES` — keep/drop path prefixes. Longest-domain matching keeps
  `markets.businessinsider.com` on its own rules.
- `SITE_BODY_RULES` — per-publisher body open/close markers and chrome filters.
  Bloomberg opens with a ~190-line corporate mega-menu, CNBC arrives as a single
  newline-free blob, FT returns a paywall. One shared rule set cannot cover them.

`include_raw_content=True` returns page markdown. The `"text"` variant is
measurably worse: plain text has no headings or paragraph breaks, so Trafilatura
rejected every listed-publisher article outright (5 of 5 dropped). Plain text is
still handled when a publisher sends it, by rebuilding paragraphs from prose
lines (`rebuild_plain_prose`).

A body larger than `MAX_SELECTED_CHARS` goes through `services/long_document.py`,
which segments on paragraphs, scores blocks against the objective and the company
name, and keeps the top blocks within a 12k budget. Measured: a 37,863-character
filing reduced to 836 characters, retaining the results-of-operations and
liquidity sections and dropping forward-looking boilerplate and exhibit indexes.

### 3.4 Subject routing

`services/article_subject.py`. Naming the company is not the same as being about
it. Three verdicts:

- `primary` — the excerpt reports on our company.
- `comparison` — our company appears inside someone else's story as a peer,
  partner or counterparty. "Zocdoc announced a partnership with Instinct" is
  Zocdoc's action.
- `unrelated` — not a subject at all.

A headline that names the company **overrides** the body reading
(`ingestion.py::title_names_company`). Measured: an article headed "NVIDIA reports
record revenue" whose body also discussed peers came back as `comparison`, so its
facts were offered as unconfirmed leads instead of as news.

### 3.5 Same-name separation

Two levers, both needed:

- `company_description` reaches the classifier, so "a San Francisco AI assistant"
  can reject an excerpt about a veterinary software firm that shares the name.
- `services/entity_disambiguation.py` filters the trailing possible-facts block
  using known other meanings (`AMD Instinct`, `Instinct EMR`, `Deep Instinct`)
  plus description overlap, with plural/verb stemming so "text" matches "texts".

### 3.6 Hiring

`services/hiring.py`, `services/job_postings.py`.

The decision is **per line**, not per page: a markets column mentioning "jobs
data" is not a career page. A line counts when it is an explicit invitation
("apply now"), a structured field label **at line start** ("Qualification: B.Sc."),
or a bare role title ("Distributed Systems Software Engineer, Collectives").

Board chrome is rejected: `52810 associated members`, `# 7,000+ Amd … Jobs`,
`Principal systems software engineer: $240,000` (compensation, not a posting).

Roles are read from schema.org `JobPosting` markup on careers pages, because the
index page of a JavaScript board usually publishes none (measured: 0 on Lever,
Greenhouse, SmartRecruiters and iCIMS indexes). Detail pages do carry them, so the
board index is crawled for per-platform link shapes:

| Platform | Shape |
|---|---|
| Lever | `/{org}/{uuid}` |
| SmartRecruiters | `/{org}/{digits}-{slug}` |
| iCIMS | `/jobs/{digits}?lang=…` |
| slug boards | `/jobs/{dept}/{city}/{role-slug}` |

Measured against the multi-publisher path on the same query, LinkedIn returned
587 attributable role titles versus 106, which is why it runs as its own lane.

### 3.7 Market quote

`services/market_data.py`. Yahoo Finance chart endpoint, no key. Exchange suffixes
are tried in order, because a bare ticker for a non-US listing returns 404
(measured: `ZYDUSLIFE` → `.NS` → 1,197.90 INR). A quote is stored as its own
`FactBase.quote`, never in `hard_facts`: it is a market number, not reporting, and
it changes by the hour. No ticker means no quote — a private company is not
guessed.

### 3.8 Our own company

`services/own_company_scan.py`. The caller's own side is scanned for **market
position only**: the quote and the coverage that moved it. Job postings are
deliberately not searched — our own hiring says nothing about the counterparty.
A caller may instead supply internal material (`own_company_source="internal"`),
which is an API-only path.

## 4. Fact assembly

`services/fact_extraction.py`, `services/dedup.py`.

Hard facts are the opening three paragraphs plus the closing one. Middle
paragraphs contribute their first sentence only when it carries a negative signal,
so the fact base gets the leverage without absorbing commentary. Signals match on
word boundaries plus inflections: `miss` cannot fire inside `mission`.

The same fact from five outlets is stored once with all five links in
`also_reported_by`; a syndicated re-telling replaces the shorter version only when
the shared opening is long enough to be the same telling.

`services/forward_looking.py` removes stated intent from the evidence before the
model reasons: a promise is not an event. It fires on intent grammar ("will",
"expects to", "targets") **and** a horizon or goal noun ("by 2028", "market
capitalisation"), **and** the absence of a completed-action verb ("reported",
"announced", "fell"). The promises are still delivered, in their own lane.

## 5. Staged generation

`services/report_stages.py`, driven by `analysis.py::generate_briefing_report`.

| Stage | Reads | Writes |
|---|---|---|
| findings | evidence, postings, coverage | `news`, `hires` |
| **audit** | findings + fact base | removes failing claims |
| analysis | **audited** findings, postings, quote, our-company market block | `analysis` |
| recommendation | **analysis only** + own profile | `executive_summary` |

Two properties follow, and both are the point:

1. A claim the audit removed cannot reach the reasoning that produces the advice.
2. The recommendation reasons over an assessment, so the advice is a conclusion
   drawn from a stated reading rather than a parallel guess.

The caller's own **profile** reaches the recommendation and nothing earlier: what
we are is legitimate input to what to ask for, and illegitimate input to what the
evidence says. Our own **market position** reaches the analysis, because "how much
room do we have" is part of what the evidence amounts to.

## 6. Pre-delivery audit

`services/report_audit.py`. Three passes per claim, in this order — the first two
cost no model call:

1. **Unknown source** (deterministic). A claim whose `source_url` is not in the
   evidence set cannot be sourced, whatever the model said.
2. **Unsupported figure** (deterministic, `services/number_check.py`). A figure
   the claim states that **no** evidence fact contains is fabricated. Measured:
   a live brief carried invented figures next to true ones.
3. **Subject and support** (model). Is this about the company being briefed, and
   does the sentence its own source stated actually say what the claim says?

A failing claim is **removed**, not caveated: a caveat still puts the sentence in
front of the reader. Removals are listed in `removed_claims` with the source URL,
so the brief does not look cleaner than the evidence allows and an over-strict
audit can be re-checked afterwards.

**Why the figure check is one-sided.** It flags a figure only when it appears
nowhere in the evidence — never when it is absent from the *cited* source. A
claim that consolidates several facts states figures from all of them, and
judging those against one article is the bug that once emptied a correct brief
(7 of 11 claims removed while every figure was present in the evidence set). Its
job is fabrication; misattribution stays the model pass's job.

The check reads claims only (`news`, `hires`). `executive_summary.recommendation`
is a proposal — "seek a price reduction of 3–5%" names a figure no evidence should
contain, and that is the point of a recommendation.

## 7. Ordering

`services/report_order.py`. Fixed rule, applied in code rather than requested from
the model: key events first, then newest first, undated last. Measured case: dates
A,B,C,D,E where C and D are key renders as D,C,E,B,A.

Same-event consolidation uses `first_seen`: reports of one event over weeks become
one claim whose span is stated.

## 8. Caching

Three layers in the pipeline, plus the gateway. Each answers a different question.

| Layer | Key | Question | Where it is enforced |
|---|---|---|---|
| exact request | company + objective + **meeting day** | is this the same request? | `BriefingRepository.find_cached` (field match, not a key string) |
| evidence fingerprint | facts + postings + objective + description + own profile | is this the same brief? | `services/evidence_fingerprint.py` + `find_by_fingerprint` |
| invalidation | cross-set contradiction | may the stored brief still be served? | `services/cache_invalidation.py` |
| gateway | `x-bf-cache-key`, TTL from the evidence | may the model call be replayed? | `services/bifrost_headers.py` |

The request layer is a **field comparison** in the repository, not a hash: the
record already stores company, purpose and meeting time, so matching those three
columns is both correct and inspectable (`services/cache.py` holds the key
primitives and the entry TTL, not the lookup).

The evidence-keyed layer exists because the request layer missed whenever a
meeting moved by a day, even though nothing about the company had changed. It
deliberately excludes the meeting date and our own quote: the facts are already in
it, and our share price overnight does not change what the brief says.

Invalidation is the part a fingerprint cannot do on its own: the stored facts and
the new facts can each be internally consistent and still contradict each other.
A brief written while the company was hiring is wrong the moment a layoff is
reported. `detect_cross_set_conflicts` reuses the same rule engine and refuses
reuse rather than serving a stale reading. Missing stored evidence also refuses
reuse — an unverifiable cache hit is exactly what this prevents.

**Gateway headers.** The rules behind `x-bf-cache-key`, `x-bf-cache-ttl` and
`x-bf-content-class` live in `bifrost-cache-policy/`, a standalone package this
repository publishes: a gateway extension has to be usable without the briefing
pipeline, so the classifier, the evidence key and the cross-set check are
computed by code that knows nothing about `FactBase`. `services/bifrost_headers.py`,
`services/gateway_plugin.py` and `services/cache_invalidation.py` are the
briefing-shaped wrappers over it, and `services/keywords.py` re-exports the same
matcher so the extraction rules and the TTL classifier cannot diverge.

The TTL is thus a property of the evidence (volatile 24h / structural 72h /
primary 168h), classified by word-boundary matching on the fact texts, and sent
per request. `gateway_plugin.py` adds a near-duplicate test (token containment,
threshold 0.72) and returns one `GatewayDecision`.

The ceiling of `semantic_cache` is what drove this: configured with
`dimension: 1` it cannot discriminate between prompts, and it holds one global
TTL. Run in **direct-only** mode (`dimension: 1`, no `provider`) it is exact
hash matching, and the key and TTL arrive per request — verified against a stock
`maximhq/bifrost:v2.4.0` container: first request `x-bifrost-cache-hit=false`,
identical second request `true`, refreshed evidence or objective `false` again.
`bifrost/config.json` in this repository therefore leaves the plugin disabled;
enabling it is a deployment decision, documented in the package README.

## 9. LLM access

`services/llm_client.py`. One cached client per `(provider, model, key, base_url)`.
Callers build the client once per article; without reuse each page created a new
connection pool (measured: 100+ on one briefing). Provider priority:
`GATEWAY_BASE_URL` → `DEEPSEEK_API_KEY` → `OPENAI_API_KEY`.

Retry policy: authentication, permission, not-found and bad-request fail
immediately; validation, connection, timeout and rate-limit retry. A schema miss
feeds the error back into the conversation, because a plain retry would resend the
same prompt and repeat the same mistake.

## 10. Configuration

`core/config.py`. `.env` then `.env.example`, with real environment variables
winning. A bare `postgresql://` URL is pinned to `psycopg2`, because SQLAlchemy 2
resolves the bare scheme to psycopg 3, which needs libpq and fails at import on a
machine that has only psycopg2.

## 11. Testing

`backend/tests/`, 473 tests, plus `bifrost-cache-policy/tests/`, 74. What they are
for:

- **Live-run regressions, not invented cases.** Every hiring-filter test uses a
  line an actual run produced. Every careers-URL case is a shape measured on a
  live board.
- **Structural guarantees.** The prompt/schema consistency test fails when a
  prompt asks for a field the Pydantic model does not have, and when the model has
  a field the prompt must not request (`removed_claims`, `possible_facts`,
  `forward_looking`, `quote` are all produced by code, never by the model).
- **Rules as rules.** Ordering, window boundaries, cache reuse, invalidation,
  fingerprint stability, near-duplicate thresholds and long-document selection are
  all asserted directly.
- **The gateway package on its own terms.** `bifrost-cache-policy/tests/` runs
  without this backend at all, which is the point: it is a deliverable a
  separate Bifrost deployment installs. Six of its cases are marked `live` and
  drive a real gateway — first miss, second hit, refreshed evidence miss,
  changed objective miss, no-key bypass, clear-by-key — skipped unless
  `BIFROST_URL` is set so CI stays offline.

Known gap: a missing import in a helper that only real runs reach was found twice
in production-like runs and not by the suite. Where that has happened, a direct
test on the helper was added afterwards.
