# Frontend task list — BriefCortex

> Hand-off list for the frontend owner. The backend is complete and passing
> **677 tests**; the frontend builds and lints (`npm run build`, `npm run lint`)
> but has no test suite. Items are ordered by user value; each has an acceptance
> check you can run without reading the backend.

---

## What already exists (do not rebuild)

- `app/` routes: `/` → `/briefing`, `/briefing`, `/debug`, `/evidence-probe`.
- `src/components/briefing/`: form, report, evidence panel, side panel, loading/error states.
- `src/hooks/briefing/use-briefing.ts`: submit → poll → report, side panel, clone, abort.
- Polling is already hardened: **no client deadline**, slow-run notice after 5 min,
  transient-failure retry (5 consecutive failures), 20s axios timeout.
- `src/api/axios.ts` base URL comes from `NEXT_PUBLIC_API_BASE_URL` (default
  `http://localhost:8000/api`).
- Backend CORS accepts `localhost`/`127.0.0.1` on ports 3000/3001 while `DEBUG=true`.

## API contract you can rely on

```http
POST /api/briefings
{ "company_name": str, "meeting_time": ISO8601, "meeting_purpose": str,
  "company_description": str = "", "ticker": str = "",
  "own_profile_name": str = "", "own_profile_inline": str = "",
  "own_company_name": str = "", "own_company_ticker": str = "",
  "own_company_source": "retrieval" | "internal" }
→ 202 { "id": uuid, "status": "accepted" }

GET /api/briefings            → { items: [{ id, company_name, meeting_time, meeting_purpose, status, created_at }] }
GET /api/briefings/{id}       → { id, status: pending|processing|completed|failed,
                                  company_name, meeting_time, meeting_purpose,
                                  report, evidence, error, created_at, updated_at }

POST /api/briefings/candidates
{ "company_name": str, "description": str = "", "round": int = 1, "rejected": [str] }
→ { candidates: [{ name, matching_facts, reason, evidence_url, confidence }] }
```

`evidence` (only when completed) now carries `hiring`, `coverage`, `conflicts`,
`hard_facts`, `analyst_opinions`, `incidental_facts`. `report` carries
`news`, `hires`, `analysis`, `executive_summary`, and (when present)
`changes_since_previous`, `removed_claims`, `possible_facts`, `forward_looking`,
`quote`.

---

## P0 — correctness and the missing confirmation step

### 1. “Confirm the company” before generating
The product's first step is choosing the right entity. Right now the form only
collects a name; for a name like **Toro** the registry returns several entities
(US, FR, NL, a shipping company) and the brief can be built about the wrong one.

- [ ] Add `POST /api/briefings/candidates` to `src/api/briefing.ts` with types in
      `src/types/briefing.ts`.
- [ ] On submit (or a “Check company” button next to the name): call candidates
      with `{ company_name, description: company_description, round: 1 }`.
- [ ] 0 candidates → generate as today. 1+ candidates → show a confirmation card
      list (name, `matching_facts`, `reason`, `evidence_url`, `confidence`), with
      “None of these” that re-queries with `round+1` and `rejected` accumulated.
- [ ] Selection must persist its identity into the request. **Backend dependency:**
      `BriefingRequest` currently has no `preferred_jurisdiction`/entity pin —
      ask the backend to add one (e.g. `preferred_jurisdiction`, `preferred_legal_name`)
      and expose the resolved `identity` on `GET /api/briefings/{id}` so the UI can
      show “Briefed as: THE TORO COMPANY (US-DE)”. Until then, append the chosen
      candidate's name/country to `company_description` and show a warning.
- [ ] The brief header/side panel shows the resolved identity once available.

**Acceptance:** typing `Toro` shows at least The Toro Company (US) as a choice;
choosing it produces a brief whose header names that entity, not a French TORO.

### 2. Optional fields the backend already accepts
- [ ] Add **Company description** (textarea, optional, helper text: “clarify when
      the name is ambiguous — what the company does, where it is based”).
- [ ] Add **Ticker** (optional, uppercased, 1–12 chars; hint: “only for listed
      companies; without it no quote is fetched”).
- [ ] Include both in `BriefingRequest` and preserve them on clone/select.
- [ ] Validation: description ≤ 500 chars; ticker `[A-Z0-9.\-]{1,12}`.

**Acceptance:** both fields survive a failed submit, a clone, and a select from the
side panel; the request body carries them (`company_description`, `ticker`).

### 3. Coverage and evidence completeness
The backend delivers coverage explicitly so a thin brief cannot read as clean.
Types are missing it today.

- [ ] Add to `src/types/briefing.ts`:
      `EvidenceSummary.hiring: EvidenceItem[]`,
      `EvidenceSummary.coverage: CoverageItem | null` with fields `level`,
      `total_facts`, `distinct_sources`, `can_support_leverage`, `lanes_missing`,
      `notes`, `checks_to_run`, `search_failures`.
- [ ] Render a coverage banner above the report: level badge
      (`none`/`thin`/`partial`/`strong`), lanes that returned nothing,
      `can_support_leverage === false` (“numeric asks unsupported”),
      `search_failures` list (“queries refused by the search API”).
- [ ] Render `checks_to_run` as a checklist in the evidence panel.
- [ ] Add a **Hiring** section to the evidence panel (`hiring` was missing).

**Acceptance:** a `level: none` brief shows the banner and checklist before any
recommendation; a hiring-only run shows hiring items in the evidence panel.

### 4. Long-run behaviour (already coded — verify and polish)
- [ ] Show elapsed time while processing (“2m 14s”), using the slow-run notice
      state already returned by the hook (`notice`).
- [ ] After the 5-minute notice, keep polling (do not show an error) and offer
      “Open from the list” which calls `selectBriefing(id)`.
- [ ] On completion, the report replaces the skeleton even if the notice is shown.
- [ ] On unmount/switch, the poll is aborted (already implemented — cover it with a test).

**Acceptance:** a mocked 6-minute poll keeps loading with a notice and then renders
the report; switching briefings does not leak a timer or an extra request.

### 5. Error taxonomy and retry
- [ ] Map failures: 422/400 → field-level errors; 5xx/timeout → “backend
      unreachable, retrying”; job `failed` → show `error` with retry button;
      404 on select → refresh the list and select nothing.
- [ ] Do not surface a network error on the first failed poll — the 5-retry policy
      in the hook should be visible as “reconnecting…” rather than as failure.

**Acceptance:** killing the backend during a run shows “reconnecting…” for five
intervals, then a retryable error; restarting the backend and pressing retry
finishes the brief.

### 6. Side panel robustness
- [ ] Status badge per item (`pending`/`processing`/`completed`/`failed`) and
      relative time.
- [ ] Refresh action; while any item is `processing`, refresh every ~5s (or on the
      active poll's completion) so the list does not go stale.
- [ ] Empty state and error state for the list (today failures are swallowed).
- [ ] Keyboard selection and `aria-selected` on the active row.
- [ ] Optional deep link: `?briefing=<id>` selects that brief on load.

**Acceptance:** a side-panel item transitions processing → completed without a
manual reload; no selected item shows a stale status.

### 7. Dev pages gating
- [ ] `/debug` and `/evidence-probe` are development tools (they call
      `/api/debug/*` and expose internals). Hide their navigation links and
      redirect in production builds (e.g. `NEXT_PUBLIC_ENABLE_DEV_PAGES`).

**Acceptance:** `next build && next start` (without the dev flag) renders no link
to those pages and they redirect to `/briefing`.

---

## P1 — tests, accessibility, robustness

### 8. Test suite (the README still says “frontend suite is being written”)
- [ ] Add `vitest`, `@testing-library/react`, `@testing-library/jest-dom`, `jsdom`;
      `npm test` script; `vitest.config.ts` with the `@/` alias.
- [ ] Hook tests (mock `src/api`): no deadline (poll past 180s), slow notice at
      5 min, transient retry up to 5, abort on unmount, clone/select state.
- [ ] Form tests: required fields, optional description/ticker validation,
      submit payload shape.
- [ ] API tests: error mapping (422/500/timeout), list/get/create wiring.
- [ ] Report component tests: empty news, `removed_claims` block, coverage banner,
      long claim text.
- [ ] Add the test job to `.github/workflows/tests.yml` (frontend job already runs
      `npm ci` + lint + build; add `npm test`).

**Acceptance:** `npm test` passes in CI; a regression in the poll policy (e.g.
re-adding a hard deadline) fails a test.

### 9. Accessibility and responsiveness
- [ ] Every input has a label; errors are `aria-describedby`; state changes
      announce via `aria-live` (notice, errors, completion).
- [ ] Keyboard focus after state changes (error → first invalid field; report →
      report heading).
- [ ] Mobile: form stacks above the report; side panel collapses; no horizontal
      scroll at 375px.

### 10. Types and runtime validation
- [ ] Sync `EvidenceSummary` with the backend (hiring, coverage; note
      `coverage` is nullable).
- [ ] Validate responses defensively (at least `report`/`evidence` presence when
      `status === "completed"`) so a schema change fails visibly, not silently.

---

## P2 — product polish (only after P0/P1)

- [ ] Export/print the brief (print stylesheet is enough; the report layout is
      already sectioned).
- [ ] Copy-summary action and shareable link to the selected brief.
- [ ] Advanced “our side” fields: `own_company_name`, `own_company_ticker`,
      `own_company_source` (retrieval/internal) and the internal-material upload
      flow (`POST /api/briefings/own-company/material`).
- [ ] Frontend error reporting (opt-in) — only if the product needs telemetry.

---

## Backend dependencies to request (blockers for P0.1)

1. `BriefingRequest.preferred_jurisdiction` (or `preferred_legal_name`) so the
   confirmed candidate can pin identity instead of relying on the model's choice.
2. `BriefingStatusResponse.identity = { legal_name, jurisdiction } | null` so the
   UI can display what was briefed and the report header can say so.
3. Optional: `POST /api/briefings` returns 202 only; the candidate check is a
   separate call today. If that call feels slow in the form, cache candidates per
   name in the client for the session.

---

## Definition of done

- P0 complete: confirmation step exists, coverage/hiring render, long runs never
  show a false timeout, errors are recoverable, dev pages are gated.
- `npm run lint`, `npm run build`, `npm test` all pass; CI runs all three.
- A brief for an ambiguous name (Toro) shows the correct entity in the header.
- A thin/empty brief shows the coverage banner and the manual checklist before the
  recommendation.
