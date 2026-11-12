# BriefCortex

**Time-sliced competitive intelligence for high-stakes commercial meetings.**

BriefCortex turns a company name and a meeting objective into a cold, evidence-backed negotiation brief in seconds — not a generic news digest.

> **Also in this repository, and usable on its own:**
> [`bifrost-cache-policy/`](bifrost-cache-policy/README.md) — a standalone,
> pip-installable package that gives any [Bifrost](https://github.com/maximhq/bifrost)
> gateway evidence-derived cache keys, graded TTLs and cross-set invalidation. It
> depends on nothing in the pipeline below, so a deployment that never runs
> BriefCortex can adopt it. **Start at
> [`bifrost-cache-policy/README.md`](bifrost-cache-policy/README.md)** for its
> install steps, API reference and measured behaviour.

---

## The Problem

Traditional briefing tools fail deal teams in two specific ways:

1. **They have no sense of time.** A layoff reported 14 months ago is treated the same as one reported last week. Negotiation leverage decays; most tools don't model that decay. BriefCortex slices evidence by recency and weights it accordingly.
2. **They cannot separate fact from opinion.** Analyst chatter, rumored executive departures, and confirmed filings get flattened into one undifferentiated blob. A deal lead cannot tell what is safe to say in the room and what is a rumor that will blow up in their face.

Most tools therefore optimize for volume and fluency, not for whether a specific claim is safe to act on in a specific meeting. That is the gap BriefCortex targets.

---

## What BriefCortex Does

Given `{company, meeting_purpose}`, BriefCortex produces a structured `BriefingReport`:

- **Both sides are read**: the counterparty through the full pipeline, the caller's own company through its market position (quote plus the coverage that moved it).
- **Three-stage generation with the audit in between**: findings, then audit, then analysis, then recommendation. A claim the audit removed cannot reach the reasoning behind the advice.
- **Per-publisher scoping**: 5 categories covering 17 domains, each with its own path and body-boundary rules, so navigation trees, related-article blocks and paywalls never become facts.
- **30-day counterparty freshness, 60-day own-company baseline**: both the publication date and the reporting period inside each sentence are checked.
- **Hard facts first**: facts are what the model may turn into leverage; unverified commentary, job postings and forward-looking statements sit in their own lanes with their own rules.
- **A dated timeline**: every claim carries its date, reports of one event over weeks consolidate into one claim with a stated span, and key events lead.
- **Long documents are read selectively**: a 10-K is segmented, scored against the objective and kept to the sections that answer it.
- **Recommendation with numeric asks**, derived from the assessment — or a directional read when no objective was supplied.
- **A deterministic conflict-detection lane** that flags contradictory facts with both sources attached — and is *never* fed into the LLM.
- **Three cache layers, plus the gateway** keyed on request, on evidence, and on cross-set contradiction — and a content-aware TTL sent to the gateway — so a brief is reused when the facts match and invalidated when newer facts overturn it.
- **A coverage ledger** that reports what retrieval actually reached — and, when it reached almost nothing, says so instead of letting an empty result read as a clean one.

For the build-level detail of each of these — stage contracts, audit rules, measured
thresholds, per-platform link shapes — see **[ENGINEERING.md](ENGINEERING.md)**.

---

## Architecture Highlights

<!-- ARCH_DIAGRAM -->

One request path: four retrieval lanes run in parallel, a gate turns them into a
fact base, four cache decisions sit on the path, and generation runs three
stages with the audit between them. Per-module detail is in
**[ENGINEERING.md](ENGINEERING.md)**.

```mermaid
flowchart LR
    UI["Web UI<br/>submit · poll"] --> API["FastAPI<br/>202 + background job"]

    API --> L1{"L1 request cache<br/>company + objective + meeting day"}
    L1 -- hit --> OUT(["Brief delivered"])
    L1 -- miss --> LANES

    LANES["Retrieval<br/>4 lanes · asyncio.gather<br/>news · open · jobs · local"] --> GATE["Gate<br/>window · trust · subject"]
    GATE --> FB[("FactBase<br/>facts · hiring · conflicts")]

    FB --> L2{"L2 evidence fingerprint"}
    L2 -- hit --> L3{"L3 contradiction check"}
    L3 -- stands --> OUT
    L2 -- miss --> GEN
    L3 -- contradicted --> GEN

    GEN["Generation<br/>findings → audit → analysis → recommendation"] --> L4{"L4 gateway cache<br/>key + graded TTL"}
    L4 -- miss --> LLM["Model<br/>schema-validated · retried"]
    L4 -- hit --> SAVE
    LLM --> SAVE[("Report + evidence<br/>+ fingerprint")]
    SAVE --> OUT

    classDef cache fill:#fff4d6,stroke:#b58b00,color:#3a2f00
    classDef stage fill:#e7f0fc,stroke:#3b6ea8,color:#10243d
    classDef store fill:#e8f5e9,stroke:#2e7d32,color:#10240f
    class L1,L2,L3,L4 cache
    class LANES,GATE,GEN,LLM stage
    class FB,SAVE store
```

**The four cache decisions.** L1 is an exact-request key (company + objective +
meeting day); L2 is the evidence fingerprint (the same facts produce the same
brief even when the meeting moved); L3 refuses reuse when new evidence
contradicts the stored facts; L4 is the Bifrost gateway key and evidence-graded
TTL. A hit at L1 serves the stored brief, and a hit at L2/L3 serves it only
while it still stands. Nothing about the evidence is re-fetched that is already
stored, and no claim reaches the recommendation without passing the audit.

### 1. Freshness: A 30-Day Window, Checked Twice

Two clocks are enforced over a 30-day window: the article's **publication date**
(parsed from Tavily's RFC 2822 format, with a URL-slug and page-fetch fallback),
and its **reporting period** (an article published today can still describe Q3
2025 — those sentences are dropped, while comparisons like "lowest since April
2025" are kept because they describe where things stand now).

The reporting-period check reads a date however it is written, because a measured
YKK run kept a 2013 docket in a 30-day window: `26 Aug 2013`, `Aug. 2013` and
`7/30/2020` were all invisible to a pattern that only knew full month names.
A court filing's caption — `Defendants.`, `No.`, `564.`, a comma run of corporate
names — is dropped as document numbering rather than a statement.

A 2023 layoff cannot be used as a 2026 negotiation lever.

### 2. Retrieval: Plan, Scope, Then Read

Retrieval is not one query against a flat domain list. Four lanes run
**concurrently**, then merge: registry news (dated, publisher-scoped), open
domain (regulators and careers pages, undated), LinkedIn postings, and queries
written in the company's own country's language against that country's press.

**Both sides of a negotiation are read.** The counterparty (B) gets the full
pipeline. The caller's own company (A) is scanned for **market position only** —
its quote and the coverage that moved it, over 60 days rather than the 30 a
counterparty gets, because one month is too short to characterise the trend every
deal is argued against. Our own hiring is deliberately not searched: it says
nothing about them. A caller who would rather not be read from public coverage
supplies internal material instead (`POST /api/briefings/own-company/material`),
which reaches the recommendation and never becomes evidence.

**The other party named in the objective is read too.** "Buy zipper supplies from
YKK" needs evidence about YKK as much as about the caller, but every query used to
carry only the company being briefed — a relationship query returns the briefed
company's coverage and mentions the supplier in passing. Measured: a brief
concluded "silent on YKK", which was true and was the pipeline's own fault. The
objective is now read for the other party's name (`services/counterparty.py`,
capitalised phrases after *from / with / against / vs / to*), and a lane of
queries about **that company's own condition** — its revenue, capacity, pricing
pressure, customer contracts — runs alongside the rest.

Nothing is filtered by sentiment on either side — positive and negative coverage
are both kept, because a company that only remembers its good news cannot be
assessed.

**The trusted publisher lists.** Every domain searched inside a category is
published here, because "trusted" has to be checkable rather than claimed:

| Category | Domains |
|---|---|
| `financial_wire` | `reuters.com`, `bloomberg.com` |
| `financial_business` | `wsj.com`, `ft.com`, `cnbc.com`, `finance.yahoo.com`, `businessinsider.com`, `markets.businessinsider.com` |
| `asia_market` | `nikkei.com` |
| `auto_industry` | `autonews.gasgoo.com`, `cnevpost.com`, `insideevs.com`, `electrek.co` |
| `official_primary` | `sec.gov`, `prnewswire.com`, `globenewswire.com`, `businesswire.com` |

17 domains across 5 categories. The model chooses *categories*, never domains, so
a fabricated host cannot reach the search API.

**The company's own country decides its press.** A company is covered where it
lives: YKK is Japanese, and the international wire list returns a decade of US
litigation filings about it while missing Nikkei entirely. So the national press
of the company's registered country is learned once and stored
(`services/media_trust.py`):

- The country comes from the legal-entity registry; failing that, from the
  company's own domain suffix; failing that, the model is asked which country the
  company sits in.
- The press list is **generated by the model**, then validated: a social handle, a
  job board, a content farm or a local edition of an international brand is
  discarded rather than stored. A wrong list would silently narrow every future
  brief in that country, so the failure mode is "store nothing", never "store
  something wrong".
- The list is held on disk under `backend/data/` and reused, so generation costs
  one model call per country rather than one per request. A stored entry that lacks
  a newer field is upgraded in place: the validated domains are kept and only the
  missing part is asked for again.

**The query is written in the country's language.** An English query restricted to
Japanese domains returns the English edition of one title; a query in Japanese
against the same domains returned nine usable bodies including the national wire
(measured). So the country's press list also carries the language its business
press publishes in and a set of topic terms in that language, and a fourth
retrieval lane runs those queries against the national domains only. Keeping the
domain scope tight is what makes them worth running: in the local language an
open search returns local blogs and message boards, while the same query against
established press returns reporting. Measured effect on a Japanese company: 24 →
41 hard facts.

**Some hosts are never press, anywhere.** Forums, social platforms, job boards,
profile directories and price-aggregator pages are refused before anything else
runs — they never reach the fact base whatever they say or whoever links them.
The full refusal list is `REFUSED_HOSTS` in `services/media_trust.py`. Rumour is
a claim about a claim, not a source.

**An employer's own board is not a job board.** Workday, Lever, Greenhouse and
similar applicant-tracking hosts read like aggregators, but
`canadagoose.wd3.myworkdayjobs.com` *is* Canada Goose's careers site: the company's
openings, published by the company, reached through a vendor's front end. Those
hosts are recognised as careers pages (`hiring.is_careers_url`) and their roles are
read from the vendor's own JSON listing rather than from HTML — the board's HTML is
a shell (measured: 7,446 bytes, zero postings) while the listing endpoint answered
with all 96. Third-party aggregators (Indeed, Glassdoor, Eluta) stay refused.

**The board may be behind the page, not on it.** An employer's own careers page
often lists nothing and links out to its vendor board — measured,
`canadagoose.com/en-us/pages/careers` is a Shopify page whose only job links are
two absolute Workday URLs. When a careers page yields no postings, the board it
points at is read instead; that is where the 96 roles came from.

**LinkedIn's jobs index is a job board, and it is padded.** `linkedin.com` is
refused as a *source of reporting* — a feed post is a claim about a claim — but
the refusal used to run before the careers test, so the LinkedIn lane returned
zero usable results on every run. It is now judged first, and its index is read as
the `ItemList` it actually is rather than as a `JobPosting` it never publishes.
The index mixes employers: a page for "Canada Goose jobs" carried 60 entries and
only 40 were Canada Goose, the rest Callaway Golf, Acushnet and Cole Haan. LinkedIn
names the employer in `disambiguatingDescription` and puts the *city* in
`description`, so a role is kept only when its employer matches the company being
briefed.

**A symbol is not a name.** `Boreo Oyj (FRA:YKK) Q3 2026 Earnings Call Transcript`
names Boreo — "YKK" there is Boreo's Frankfurt symbol, not YKK the company. A
measured YKK run filed nine of the brief's thirty-seven hard facts from that
transcript: Boreo's revenue, EBIT and cash flow, attributed to a Japanese zipper
maker. The name check strips exchange-prefixed tickers (`FRA:YKK`, `NASDAQ:ACME`)
before matching, because a three-letter code belongs to unrelated businesses on
different venues — the same collision the price resolver below already refuses.

**A price needs a ticker, and a ticker needs an identity.** The quote is fetched
from a symbol resolved from the company name (`services/ticker.py`), so a listed
company no longer depends on the caller typing one — measured, five stored briefs
for Canada Goose (GOOS on NYSE, GOOS.TO on Toronto) carried no price at all. But a
symbol is not an identity: searching a private group's name answers with symbols
belonging to unrelated businesses (measured, "YKK" matched two Finnish companies).
A candidate is accepted only when its registered name matches at the same bar the
legal-entity registry uses, and the **company's own country decides the venue**:
Toyota resolves to `7203.T` (Tokyo) rather than `TM` (NYSE), Canada Goose to
`GOOS.TO`, Zydus to `ZYDUSLIFE.NS`, while a company with no listing — or no
confident match — has no quote at all rather than somebody else's.

The remaining publications are ranked: filings and newswires (`primary`), the
company's own national press (`national`), international wires (`global`), then
every other real publication (`fallback`, read last and labelled). The ranking
orders the reading budget; it does not drop a real publication. An unknown
jurisdiction gets no national tier at all rather than a guess.

**When the list is not enough.** A regulator's case page, a careers site and a job
board are not publishers. A deliberately small second round runs outside the
registry — jurisdiction-aware legal searches, hiring, LinkedIn — because the trade
is explicit: outside the list, editorial standards are unknown and an unverified
claim is likelier. Those results pass the same routing and the same pre-delivery
audit, and are marked by source rather than trusted more for having been found.

**Reading.** Hard facts are the opening three paragraphs plus the closing one;
middle paragraphs contribute their first sentence only when it carries a negative
signal. Each paragraph is then split into sentences, so one stored fact is one
statement rather than a bundle — a claim can point at the sentence behind it, and
a figure cannot hide inside a block nobody can slice. A body larger than 12k
characters — a 10-K, an IR page — goes through the long-document reader, which
keeps the sections that answer the meeting objective and drops boilerplate.
Measured on a filing: 37,863 characters reduced to 836, retaining
results-of-operations and liquidity, dropping forward-looking statements and
exhibit indexes.

#### Retrieval limits, and when it stops

Retrieval is bounded four ways. All four are published because a reader has to be
able to tell "the search converged" from "the search was cut off".

| Bound | Value | What it protects against |
|---|---|---|
| Opening round | one per lane, **concurrent** (news, open, LinkedIn, local) | a single query list deciding coverage |
| Follow-up rounds | `MAX_SEARCH_ROUNDS = 8`, `MAX_EXTRA_QUERIES = 6` per round | a runaway query plan |
| Wall clock | `SEARCH_BUDGET_SECONDS = 45` | outliving the client's poll window |
| Diminishing returns | `MAX_STALE_ROUNDS = 2` — two consecutive rounds adding no new URL | paying Tavily for the same results again |
| Per query | `TAVILY_MAX_RESULTS = 20`, then the publisher filter | an unfiltered result set |

**The 45-second budget bounds the search loop, not the request.** Everything the
pipeline does after retrieval — publish-date recovery, subject routing, the
own-careers probe, the incidental harvest and its corroboration, semantic
deduplication — used to run one stage after another, so a 172-fact run spent 523
seconds inside ingestion alone against a 180-second client poll window. Two
changes cut that to 229 seconds on the same company:

- **Semantic dedup went from 335s to 37s.** The merge walked one fact at a time
  and asked the model about its partners, then re-asked about the remainder
  after every accept — one model round per accepted fact. Candidate pairs do not
  depend on merge order, so they are now proposed once across the whole set,
  confirmed in concurrent batches, and grouped by union-find.
- **Three stages that never read each other now run together.** The quote, the
  own-careers probe and the incidental harvest all depend only on what retrieval
  already produced; run in sequence they cost 15.8 + 15.2 + 18.0 seconds.
  Body extraction moved to a worker pool at the same time, because
  markdown → trafilatura → plain-text rebuild is CPU-bound and running it inline
  blocked the event loop for the whole reading stage.

What is left is latency, not slack: the search round (50s), the reading stage
(60s), and one model call per dedup/audit batch at 7–15 seconds each.

The loop exits on whichever bound is reached first, and it is the **diminishing-returns
bound that actually fires**. Measured on a real four-lane run (28 planned queries):

| Round | Queries | Usable | New URLs | Running total |
|---|---|---|---|---|
| opening (4 lanes) | 11 | 101 | 75 | 75 |
| 2 | 6 | 4 | **0** | 75 |
| 3 | 6 | 3 | **2** | 77 |
| 4 | 6 | 5 | **0** | 77 |
| 5 | 5 | 0 | **0** | 77 → stop |

So the opening concurrent round produced 75 of the 77 URLs, and the four
follow-up rounds bought two articles. The round cap and the 45-second budget were
never reached — the plan ran dry first (`pending_queries=0`). The limits are
therefore not the constraint on coverage; **the supplied query list is**. That is
why the planner is asked for queries that name the meeting objective rather than
generic financial terms, and why the counterparty lane exists.

`MIN_USABLE_PER_ROUND = 1` and `REFILL_MIN_USABLE_RESULTS = 8` are the other two:
a round counts as productive only when it yields a result that carries an article
body, and the body-refill round is skipped once enough results already have one.

#### When the opening round comes back thin

If the opening lanes return **fewer than six usable bodies**, the plan is asked
to think again — **once**, and only then. `services/search_planner.py::replan_after_empty`
receives the queries that ran, the domains that were searched and the result
counts, and proposes a different angle rather than a paraphrase: the company's
legal name, ticker or parent; a broader topic; a different kind of source; or a
plain statement that nothing was published inside the window.

The supplement searches the **trusted national press list first** and the open web
behind it. That list is the model's own reading of which publications cover a
company in its own country, learned once per country and stored; a company
invisible to the international wire list is usually covered where it lives.
Measured: an English query against Japanese titles returned the English edition of
one paper, while a query in Japanese against the same titles returned nine usable
bodies.

It is not a retry. A query already tried is dropped from the reply, because
repeating a search that returned little returns little again. And an empty answer
is a **valid** answer: when the model concludes the company has no coverage — a
private firm, a name collision, a dormant entity — no further search runs. The
brief then says so and hands the reader the five manual checks (`checks_to_run`),
which is cheaper and more honest than paying for a second empty round.

The bound is one attempt per request, so a company nobody writes about does not
spend a model call and a Tavily round on every run.

→ Scoping tables, per-publisher body boundaries, depth budgets, body recovery and
deduplication: **[ENGINEERING.md §3](ENGINEERING.md#3-retrieval)**.

### 3. Two Report Modes

The report follows the meeting objective when one is supplied: numeric asks,
leverage, trades. With no objective it is a *fundamentals* brief — business
direction, what is demonstrably working, what deserves caution — and the model is
forbidden from inventing a target or emitting numeric demands.

### 4. Fact / Opinion Separation

`FactBase` carries `hard_facts` and `analyst_opinions` as separate blocks, and the
prompt forbids using unverified commentary as leverage. If hard facts contain no
negative signal the model must output exactly:

> "Evidence insufficient; recommend standard outreach."

No fabrication, no manufactured aggression.

### 5. Three Stages, With the Audit In Between

This is the structural answer to "why is a wrong claim in my brief".

| Stage | Reads | Writes |
|---|---|---|
| findings | evidence, postings | `news`, `hires` |
| **audit** | findings + evidence | removes failing claims |
| analysis | **audited** findings, quote, our own market position | `analysis` |
| recommendation | **analysis only** + the caller's own profile | `executive_summary` |

A claim the audit removed can never reach the reasoning that produces the advice,
and the advice reasons over an assessment rather than a parallel reading. The
caller's own **profile** reaches only the recommendation; our own **market
position** reaches the analysis, because "how much room do we have" is part of
what the evidence amounts to.

**Three passes, two of them free.** An unknown source and a figure the evidence
never states are caught by string comparison before any model is asked; only the
subject/support judgement costs a call. The figure check is deliberately
one-sided — it flags a number that appears **nowhere** in the evidence, never one
missing from the single cited article — because a claim that consolidates several
facts legitimately quotes figures from all of them, and judging those against one
article is what once emptied a correct brief.

What the analysis does with our own position is state how it bears on *this*
counterparty — a weak share price reduces our room to walk away or hold out. It is
never written as "they think we are weak": nobody in the evidence said that.

Every claim carries a date. Reports of one event over several weeks become one
claim with a stated span (`first_seen .. date`), and news is ordered key events
first, then newest.

→ Full contract, prompts and audit rules: **[ENGINEERING.md §5](ENGINEERING.md#5-staged-generation)**.

### 6. Conflict Detection Stays Off the LLM Path

`detect_fact_conflicts()` is a deterministic regex engine over `hard_facts` only.
Its output is **never** fed into the prompt: semantics cannot override evidence,
and evidence cannot be reinterpreted by semantics.

### 7. Cache: Three Different Questions, and a Fourth Answer

| Layer | Key | Question |
|---|---|---|
| exact request | company + objective + **meeting day** | same request? |
| evidence fingerprint | facts + postings + objective + description + own profile | same brief? |
| invalidation | cross-set contradiction | may it still be served? |
| gateway (`semantic_cache`) | this request's `x-bf-cache-key`, TTL from the evidence | may the *model call* be replayed? |

The first three decide whether a **brief** may be reused; the fourth decides
whether an **HTTP request** may be. They are independent: the gateway can replay a
stage call, and the pipeline can serve a stored brief without any model call at
all.

The fingerprint layer exists because the request layer missed whenever a meeting
moved by a day, even though nothing about the company changed. The invalidation
layer exists because a fingerprint cannot see a contradiction: a brief written
while the company was hiring is wrong the moment a layoff is reported, and each
individual fact in it was still correct.

Content-aware TTL travels as headers (`x-bf-cache-key`, `x-bf-cache-ttl`,
`x-bf-content-class`), classified by word-boundary matching on the fact texts, and
attached to **every** stage call:

| Class | Signals | TTL |
|---|---|---|
| `volatile` | earnings, guidance, stock moves, recalls, lawsuits | 24h |
| `structural` | hiring, appointments, product launches | 72h |
| `primary` | annual reports, filings, press releases | 168h |

→ Fingerprint inputs, invalidation engine, gateway decision: **[ENGINEERING.md §8](ENGINEERING.md#8-caching)**.

The gateway rules — cache key, graded TTL, cross-set invalidation — are published
as a standalone package so a stock Bifrost install can adopt them without this
pipeline: **[`bifrost-cache-policy/`](bifrost-cache-policy/README.md)**. Install
it from the repo, add the three headers before the request leaves your process,
and the gateway stores, serves and expires them. Verified against a stock
`maximhq/bifrost:v2.4.0` container in direct (hash) mode: first request
`x-bifrost-cache-hit=false`, identical second request `true`, refreshed evidence
`false` again.

### 8. What a Brief Costs

Token cost is measured, not guessed. Every model call reports the provider's
`usage`, accumulated per briefing and logged once when the job ends:

```text
Briefing token usage | id=… | calls=… | prompt_tokens=… | completion_tokens=… | total_tokens=…
```

Three properties keep the bill down, and each is a deliberate trade-off:

- **The findings stage reads a bounded slice of the evidence.** It returns at
  most eight claims, so a run with 170 facts ships the newest 80
  (`MAX_FINDINGS_FACTS`; `0` disables the cap) and `<COVERAGE>` states
  `facts shown: 80 of 170`. The prompt tells the model never to read a truncated
  list as the whole record.
- **Dedup verdicts are cached across runs.** The same pair of syndicated
  sentences is answered once; a cached verdict also keeps the merged fact set
  stable, which is what lets the evidence fingerprint hit on a later run.
- **Cache misses are named.** `Request cache miss … reason=no record|expired`
  and `Evidence-set cache miss …` make the hit rate explainable from the log
  instead of an unexplained bill.
- **Mechanical stages do not reason.** deepseek-flash bills reasoning as
  completion tokens: a five-token JSON verdict cost 99 completion tokens with
  reasoning on and 5 with it off. Dedup confirmation and subject routing opt out
  (`reasoning_effort: "none"`); the three generation stages keep it.
- **The provider's prompt cache is visible.** The usage line carries
  `cached_prompt_tokens`, so a run that reuses a repeated system prompt shows
  what the provider served from its own cache.

The evidence set itself is kept stable, because a moving fact set is what forces
regeneration: stored facts are pruned to the merged set after each run (a
superseded telling is one claim, not two), stored postings are reloaded as
evidence, and the invalidation check only considers facts added since the stored
brief. Comparing a set with itself once "overturned" ten unchanged facts.
Measured on Blue Star after those fixes: a later meeting day with unchanged
evidence served the stored brief in **7.3s with zero model calls**.

Misses themselves are reduced in three places: the request cache (same company,
objective and meeting day), the evidence fingerprint (same facts, whatever the
meeting date), and the gateway request cache. The gateway layer is **opt-in**:
`bifrost/config.json` in this repository ships `plugins: []`, so L4 replays
nothing until `semantic_cache` is enabled (direct mode, `dimension: 1`, with a
vector store). The enabling recipe is in the
[policy package README](bifrost-cache-policy/README.md).

### 9. Tested, Not Vibes

The repo ships with `pytest` coverage for the prompt/schema contract, the conflict engine, the extraction rules and the cache classifier, including:

- Prompt output schema must match the real Pydantic `BriefingReport` schema.
- No ghost fields (`verified`, `negotiation_levers`, `target_concessions`) may appear.
- `\brif\b` must not match `tariff`.
- Single-fact internal contradictions must not be reported as cross-item conflicts.
- Reverse-direction pairs must produce a description with the correct fact on each side.
- Negative-signal keywords match whole words, so `miss` cannot match `mission`.
- Two distinct facts sharing an opening clause are both kept; only a long word-for-word extension replaces the shorter telling.
- A deduplicated chain collapses transitively, the fuller telling wins, every source survives, and nothing merges without the model's confirmation.
- Subject routing and semantic deduplication are asserted to run their batches concurrently, and a failed subject batch degrades only that batch.
- The process-wide model-call cap bounds total in-flight calls, and a failed call returns its slot.
- Findings truncation keeps the newest facts and discloses the ratio; dedup verdicts are reused across runs; per-run token usage is summed from the provider's own report.
- The cache TTL classifies evidence by whole word, so `third quarter` is not read as a hiring signal.

---

## Repository Layout

```
BriefCortex/
├── frontend/                 # Next.js App Router UI
│   ├── app/                  # Routes (`/` redirects to `/briefing`)
│   └── src/                  # Components, hooks, API clients, types
├── backend/                  # FastAPI service
│   ├── app/
│   │   ├── api/              # Route handlers
│   │   ├── core/             # Settings + DB engine
│   │   ├── db/               # SQLAlchemy ORM models
│   │   ├── repositories/     # Data access (Postgres)
│   │   ├── models/           # Pydantic schemas (briefing report + fact base)
│   │   ├── services/         # Retrieval, identity, lanes, analysis, job orchestration
│   │   └── mock/             # Demo briefing payload
│   ├── migrations/           # Alembic revisions
│   └── tests/
├── docs/                     # CLAUDE, KARPATHY
├── .github/workflows/        # CI: backend, frontend and gateway-policy suites
├── bifrost-cache-policy/     # Standalone gateway cache rules (pip-installable)
└── docker-compose.yml        # Postgres, Bifrost gateway, Redis Stack
```

---

## Module Map

### Backend services

| Area | Responsibility |
|------|----------------|
| `services/briefing_service.py` | Async job accept, cache lookup, status orchestration, stale-job recovery |
| `services/ingestion.py` | Retrieval orchestration; parallel lanes, fact assembly, window filtering |
| `services/search_planner.py` | Model-driven search plan, constrained to a source registry |
| `services/company_identity.py` | Registry lookup for jurisdiction and legal form (GLEIF) |
| `services/entity_search.py` | Turns the caller's description into confirmable company candidates |
| `services/entity_disambiguation.py` | Rejects possible facts that belong to a different same-named company |
| `services/counterparty.py` | Reads the objective for the other company and adds queries about its own condition |
| `services/legal_signals.py` | Jurisdiction-aware searches for legal and regulatory exposure |
| `services/source_rules.py` | Publisher registry and per-publisher extraction rules |
| `services/publisher_filter.py` | URL scoping, article-body location, text extraction, plain-text recovery |
| `services/long_document.py` | Segments a filing, scores blocks against the objective, keeps the sections that answer it |
| `services/media_trust.py` | Country → press list and language, refused hosts, national-press trust ordering |
| `services/media_list_store.py` | The on-disk store those learned press lists are persisted in |
| `services/fact_extraction.py` | Inverted-pyramid split, dedup, source ordering |
| `services/coverage.py` | Coverage ledger: levels, missing lanes, manual checks, search failures |
| `services/freshness.py` | Publication dates, date fallback, reporting-period checks |
| `services/article_subject.py` | Routes each article by who it is about, using the caller's description |
| `services/sentence_classifier.py` | Fact vs opinion labeling for incidental sentences |
| `services/incidental.py`, `incidental_harvest.py` | Facts about us found inside other companies' coverage |
| `services/corroboration.py` | Targeted follow-up search for an incidental fact |
| `services/hiring.py` | Line-level hiring detection, careers-URL matching, LinkedIn and hiring queries |
| `services/job_postings.py` | Reads roles from a careers page's schema.org `JobPosting` markup, including per-platform detail-link discovery |
| `services/dedup.py` | Two-stage fact merging that keeps every source |
| `services/own_company_scan.py` | The caller's own market position: quote plus the coverage that moved it |
| `services/own_profile.py` | The caller's own company profile, stored and read back for the recommendation |
| `services/internal_material.py` | Caller-supplied internal material, used in the recommendation and never as evidence |
| `services/retention.py`, `retention_runner.py`, `scheduler.py` | Evidence expiry and URL decay, run on a daily loop |
| `services/cache.py` | Exact-request cache key and TTL |
| `services/evidence_fingerprint.py` | Evidence-set fingerprint, so a brief is reused when the facts match |
| `services/cache_invalidation.py` | Cross-set contradiction check that invalidates a reusable brief |
| `services/analysis.py` | LLM report generation, prompt assembly, audit and ordering |
| `services/report_stages.py` | The three generation stages and the prompt/schema contract each one owns |
| `services/report_audit.py` | Pre-delivery check: a claim about another company, an unknown source, or a figure the evidence never states, is removed |
| `services/number_check.py` | Deterministic figure check: every number a claim states must appear somewhere in the evidence |
| `services/report_order.py` | Key events first, then newest — the reading order of `news` |
| `services/market_data.py` | Timestamped market quote for a listed company, fetched separately from the news |
| `services/ticker.py` | Resolves a company name to its exchange symbol, and refuses to guess when the name matches another business |
| `services/forward_looking.py` | Separates stated intent from reported events so a promise never becomes evidence |
| `services/gateway_plugin.py` | Briefing-shaped gateway decision: one `GatewayDecision` per request |
| `services/conflict_detection.py` | Deterministic in-report conflict lane, on the shared rule table |
| `services/bifrost_headers.py` | Content-aware cache headers, wrapping the standalone policy package |
| `services/llm_client.py` | Provider selection, retry policy, the cached client every caller shares, and the process-wide model-call cap |
| `services/token_usage.py` | Per-run token accounting, summed from each call's reported `usage` |
| `services/keywords.py`, `urls.py`, `window.py` | Shared matchers, URL parsing, meeting-anchored window |
| `services/tavily_search.py` | The single search call, dated and undated, plus targeted body extraction |
| `services/logger_service.py` | Structured logging factory |
| `repositories/` | Postgres data access only |
| `core/` | Settings, DB engine/session, connection checks |

### Frontend

| Area | Responsibility |
|------|----------------|
| `app/` | Next.js App Router pages; `/` redirects to `/briefing` |
| `src/components/briefing/` | Form, report states, evidence panel, side panel |
| `src/hooks/briefing/` | Submit, poll, list, select, clone (client-only) |
| `src/api/` | Axios clients talking to FastAPI |
| `src/types/`, `src/lib/` | Shared contracts and pure helpers |

---

## Design Decisions

1. **Async accept + poll** — generation takes seconds; `202` + polling keeps the UI responsive without websockets. Jobs are held in a task set so the event loop cannot collect them mid-run, and a job stuck in `processing` is failed on next startup.
2. **Subject routing, not name matching** — an article that merely names the company is not about the company. Each article is routed by its subject before any fact is trusted, which is what stopped a competitor's reporting being filed as ours.
3. **Hiring is its own lane** — a posting states intent, not outcome, so it informs the model without being asserted as a reported fact. The decision is made per line and per structured posting, never per page: a markets column mentioning "jobs data" and a careers page's apply buttons are both furniture. Postings are found by reading schema.org `JobPosting` blocks, because a careers board renders its listings in JavaScript and its index page usually carries none (measured: 0 on Lever, Greenhouse, SmartRecruiters and iCIMS indexes). Detail pages do carry them, so the board index is crawled for per-platform link shapes — Lever `/{org}/{uuid}`, SmartRecruiters `/{org}/{digits}-{slug}`, iCIMS `/jobs/{digits}`, slug boards `/jobs/{dept}/{city}/{role-slug}` — and a few detail pages are opened. Hiring queries run **undated**: a posting has no publish date, so restricting them to the evidence window excluded almost every real posting (measured: 1 careers URL with the window versus 9 without).
4. **One fact, every source** — the same fact from five outlets is stored once with all five links. Coverage count is not the claim's value.
5. **Conflict detection stays off the LLM path** — conflicts are computed independently and served beside the report, never inside the prompt.
6. **Postgres + repository** — each briefing is a row keyed by UUID; the report lives in one JSONB column.
7. **One configuration channel** — every key is read through `app/core/config.py`. The `.env` path is resolved against the module, and `.env.example` is read as a fallback so a clean checkout runs without anyone copying it first; real environment variables still win. A bare `postgresql://` URL is pinned to the installed driver, because SQLAlchemy 2 resolves it to psycopg 3, which needs libpq and fails at import on a machine that has psycopg2.
8. **Schema changes go through Alembic** — `init_db()` only checks connectivity and warns when the revision is behind; it never alters tables.

## Anti-patterns

- Do not call FastAPI directly from React components — go through `src/api/`.
- Do not put business logic in route handlers — keep it in services.
- Do not put SQL in services — go through repositories.
- Do not feed conflict-detection output into the LLM prompt.
- Do not let unverified commentary become a numeric ask in `executive_summary.recommendation`.
- Do not gate an article on whether the company name appears; ask what the article is about. When the name *is* used as a cheap pre-filter, match every name the company is known by — a ticker-only check silently discards "International Business Machines" when the caller searched "IBM".

---

### Backend

```bash
# optional: local Postgres
docker compose up -d db

cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000) — the app redirects to `/briefing`.

### API contract

- `POST /api/briefings` — accepts `{ company_name, meeting_time, meeting_purpose }`, returns `202` with `{ id, status }`
- `GET /api/briefings` — list saved briefings (newest first)
- `GET /api/briefings/{id}` — fetch/poll a briefing by id until `status` is `completed` or `failed`

---

## Running Tests

```bash
cd backend
python -m alembic upgrade head
python -m pytest tests/ -v
```

Expected: 671 passed.

The gateway policy package ships its own suite, runnable without this backend:

```bash
cd bifrost-cache-policy
pip install -e .
pip install pytest
python -m pytest tests -v              # 68 passed, 6 skipped (live cases)
```

> Test count is maintained locally while the frontend suite is still being
> written; the shared baseline will be the sum of this backend count and whatever
> the frontend contributes.

> Note: on Python 3.15 the Tsinghua mirror does not ship a `pytest` wheel. Install from the official index: `pip install pytest --index-url https://pypi.org/simple`.

---

## Limitations

This is a deliberately honest section. BriefCortex reads the open web; it is not
a database of companies, and it cannot manufacture coverage that does not exist.
Knowing where it stops is part of using it safely.

### What it cannot do

| Limitation | Why | What the product does about it |
|---|---|---|
| **Small and private companies are mostly invisible** | A private manufacturer with no press coverage, no filings and no listing produces no articles. There is nothing to retrieve. | The coverage ledger reports `level: none` and states that an empty result is a statement about coverage, not about the company. |
| **No jurisdiction-specific registry is integrated** | Registries such as Malaysia's SSM, India's MCA or Italy's Registro Imprese are gated behind local identity, fees or captchas. Wiring each one in would cost more than it returns. | Identity comes from the free GLEIF LEI index when the entity has one; otherwise the brief says identity was unresolved and lists what to check manually. |
| **Legal exposure is inferred from coverage, not from filings** | Regulator portals are not reachable programmatically. | Legal searches run jurisdiction-aware queries and are marked as leads, never as verified findings. |
| **Non-English coverage is under-represented** | Publishers, queries and cleansing rules are built for English-language financial press. | The gap is visible in the lane counts rather than hidden: a company covered only in local-language press will look thin. |
| **A thin brief cannot be turned into numbers** | There is no defensible price, term or volume to ask for when the fact base is nearly empty. | When `coverage.can_support_leverage` is false the model is instructed to withhold numeric asks and say what is missing, even if a meeting objective was supplied. |
| **Cost bounds coverage** | Every run pays for retrieval and inference. | The exact-request cache removes repeated cost; the search budget caps a single run. |

### The rule that matters

> **"No reporting found" is not the same as "no problems found."**

Those two statements are opposite and only one of them is safe to act on. The
`coverage` block exists to keep them apart: it names the coverage level, which
lanes returned nothing, and the checks a reader should run before signing.

When coverage is thin, the brief ships with a checklist instead of a verdict:

- Company registration status and legal form, from the local registry
- Litigation, insolvency or winding-up record in the jurisdiction
- Sanctions and export-control screening for the entity and its owners
- Latest filed accounts or audited financial statements
- Beneficial ownership and any state or group affiliation

### Scope it is designed for

BriefCortex is built for **companies that are written about**: listed groups,
regulated industries, and firms large enough to have press coverage in the last
30 days. For a small counterparty the honest output is the coverage ledger and
the checklist, not a negotiation strategy.

### Company registration records are not reachable

This is a limit worth stating plainly, because a reader may otherwise assume the
brief has checked it.

Local business registries — a state companies house, a chamber of commerce, a
ministry of trade — almost never expose a queryable API to a crawler. The pages
that do exist are usually behind a form, a captcha or a paid account, and the
greppable ones are often stale. So this product **cannot confirm registration
status, incorporation date, paid-up capital, directors, shareholders or filing
history from the registry of record** in most jurisdictions.

What it does instead:

- GLEIF is consulted for a **legal entity identifier**, which yields jurisdiction
  and legal form for entities that have one. Many private companies do not.
- When no registry match is found, the identity is **withheld rather than
  guessed**, and the coverage ledger says so: no jurisdiction-specific source was
  consulted, and corporate structure, ownership and filing history are
  *unchecked*, not clean.
- The checklist delivered with a thin brief names exactly these documents — filed
  accounts, beneficial ownership, registration extract — as items for the reader
  to obtain from the counterparty.

A brief that reports a confident identity claim on a private company is reporting
something this product did not establish.

---

## Database Migrations

Schema changes are owned by Alembic. `init_db()` only checks connectivity and
warns when the database revision is behind the code; it never alters tables.

```bash
cd backend
python -m alembic upgrade head        # bring a database up to date
python -m alembic revision --autogenerate -m "describe the change"
python -m alembic check               # fail if models and migrations disagree
```

---

## Deployment Safety

This is an **authless demo**. Three consequences follow, and none of them is
hidden behind configuration:

- **No authentication.** Every endpoint is open to anyone who can reach it, and a
  stored briefing is readable by anyone holding its UUID
  (`GET /api/briefings/{id}`).
- **Loopback by default.** `uvicorn app.main:app` binds `127.0.0.1`; keep it that
  way. Before exposing the service beyond the local machine, put it behind a
  reverse proxy that authenticates and rate-limits, and serve it over TLS.
- **The debug console is a development tool.** `/api/debug` is mounted only when
  `DEBUG=true`; it exposes whole pipeline internals and bypasses the job queue, so
  a deployment other people can reach must run with `DEBUG=false`.

Provider keys belong in `backend/.env` (gitignored) or machine-level environment
variables — never in `backend/.env.example`.

---

## Design Constraints

Three constraints are enforced in code, not just in prose:

- **Hard facts are the only source of negotiation leverage.** Unverified commentary is rendered in a separate prompt block and cannot be promoted into the numeric asks of `executive_summary.recommendation`.
- **Conflict detection is not part of the LLM path.** `detect_fact_conflicts()` returns structured conflicts to callers; it is never serialized into the model input. The two outputs are consumed independently.
- **The pipeline mode is explicit.** `MOCK_DATA` defaults to `false`; the mock path must be switched on deliberately and logs a warning at startup, and a missing `TAVILY_API_KEY` or LLM key is reported as an error instead of silently serving sample data.

---

## Contributing

1. Branch from `main` with a `feature/`, `fix/`, or `chore/` prefix.
2. Follow conventions in `docs/CLAUDE.md` and the coding behavior contract in `docs/KARPATHY.md`.
3. Keep route handlers thin; put business logic in services.
4. Open a PR with a clear summary and test notes.
