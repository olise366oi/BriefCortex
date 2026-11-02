# BriefCortex

**Time-sliced competitive intelligence for high-stakes commercial meetings.**

BriefCortex turns a company name and a meeting objective into a cold, evidence-backed negotiation brief in seconds — not a generic news digest.

---

## The Problem

Traditional briefing tools fail deal teams in two specific ways:

1. **They have no sense of time.** A layoff reported 14 months ago is treated the same as one reported last week. Negotiation leverage decays; most tools don't model that decay. BriefCortex slices evidence by recency and weights it accordingly.
2. **They cannot separate fact from opinion.** Analyst chatter, rumored executive departures, and confirmed filings get flattened into one undifferentiated blob. A deal lead cannot tell what is safe to say in the room and what is a rumor that will blow up in their face.

Most tools therefore optimize for volume and fluency, not for whether a specific claim is safe to act on in a specific meeting. That is the gap BriefCortex targets.

---

## What BriefCortex Does

Given `{company, meeting_purpose}`, BriefCortex produces a structured `BriefingReport`:

- **Hard facts vs. analyst opinions**, kept in separate lanes end to end.
- **Executive summary with numeric negotiation asks**, derived only from hard facts.
- **A deterministic conflict-detection lane** that flags contradictory facts with both sources attached — and is *never* fed into the LLM.
- **Dual-layer caching** so the second identical request returns in milliseconds.

---

## Architecture Highlights

<!-- ARCH_DIAGRAM -->

*(Architecture diagram: see pitch deck.)*

### 1. Bifrost Time-Slicing

Evidence is not a flat list. Bifrost partitions retrieved material into time slices (e.g. last 7 days / last 90 days / older), so the prompt can distinguish "fresh leverage" from "stale context". A 2023 layoff does not get used as a 2026 negotiation lever.

### 2. Fact / Opinion Separation

`FactBase` carries two distinct lists: `hard_facts` and `analyst_opinions`. They are rendered into the LLM prompt as separate, labeled blocks (`<HARD_FACTS>`, `<ANALYST_OPINIONS>`). The system prompt forbids using unverified commentary as negotiation leverage. If hard facts contain no negative signal, the model is required to output the exact fallback sentence:

> "Evidence insufficient; recommend standard outreach."

No fabrication, no manufactured aggression.

### 3. Conflict Detection as an Independent, Auditable Lane

`detect_fact_conflicts()` runs a deterministic regex rule engine over `hard_facts` only. It returns structured conflicts with both sides' `source_url`, and its output is **never fed into the LLM prompt**.

LLM output and conflict detection are two independent tracks by design: semantics cannot override evidence, and evidence cannot be reinterpreted by semantics.

### 4. Dual-Layer Cache

- **Layer 1 — exact-request cache:** identical `{company, meeting_purpose}` returns the stored report instantly.
- **Layer 2 — evidence cache:** Tavily retrieval results are cached per company with a TTL, so a re-run with a different `meeting_purpose` skips retrieval entirely.

Demo implication: the second run is a millisecond response, not a 30-second LLM call.

### 5. Tested, Not Vibes

The repo ships with `pytest` coverage for both the prompt/schema contract and the conflict engine, including:

- Prompt output schema must match the real Pydantic `BriefingReport` schema.
- No ghost fields (`verified`, `negotiation_levers`, `target_concessions`) may appear.
- `\brif\b` must not match `tariff`.
- Single-fact internal contradictions must not be reported as cross-item conflicts.
- Reverse-direction pairs must produce a description with the correct fact on each side.

---

## Repository Layout

```
pre-meeting-agent/
├── services/
│   ├── ingestion.py           # Tavily retrieval, cleaning, FactBase assembly, conflict wiring
│   ├── analysis.py            # SYSTEM_PROMPT, LLM call, BriefingReport schema
│   └── conflict_detection.py  # Deterministic regex rule engine
├── tests/
│   ├── test_prompt_schema_consistency.py
│   └── test_conflict_detection.py
└── mock/
    └── nio_briefing_report.json   # Schema-valid demo payload
```

---

## Running Tests

```bash
python -m pytest tests/ -v
```

Expected: 20 passed.

> Note: on Python 3.15 the Tsinghua mirror does not ship a `pytest` wheel. Install from the official index: `pip install pytest --index-url https://pypi.org/simple`.

---

## Design Constraints

Two constraints are enforced in code, not just in prose:

- **Hard facts are the only source of negotiation leverage.** Unverified commentary is rendered in a separate prompt block and cannot be promoted into the numeric asks of `executive_summary.recommendation`.
- **Conflict detection is not part of the LLM path.** `detect_fact_conflicts()` returns structured conflicts to callers; it is never serialized into the model input. The two outputs are consumed independently.
