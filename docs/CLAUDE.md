# CLAUDE.md — AI Editor Guide for BriefCortex

> This file helps AI coding assistants understand this codebase and work on it effectively.

## Project Overview

BriefCortex is a full-stack pre-meeting intelligence app. The Next.js frontend submits a briefing request; the FastAPI backend accepts it with `202`, persists it in Postgres, processes asynchronously, and the UI polls until a `BriefingReport` is ready. A left side panel lists saved briefings and supports client-side clone.

> **For AI contributors:** Before writing any code, read `docs/KARPATHY.md`.

## Tech Stack

- **Frontend**: Next.js 15 (App Router) + React 19 + TypeScript + Tailwind CSS + Axios + clsx
- **Backend**: FastAPI + Pydantic v2 + SQLAlchemy + PostgreSQL + Uvicorn
- **Auth**: none (authless demo)

## Repository Structure

```
BriefCortex/
├── frontend/
│   ├── app/                         # Routes only
│   └── src/
│       ├── api/                     # HTTP clients (axios)
│       ├── components/briefing/     # Form, report, side panel, clone menu
│       ├── components/ui/           # Clay primitives
│       ├── hooks/briefing/          # Submit + poll + list/select/clone
│       ├── lib/
│       ├── types/
│       └── styles/globals.css
├── backend/
│   ├── app/
│   │   ├── api/endpoints/           # briefing.py, debug.py
│   │   ├── core/                    # config, database, alembic check
│   │   ├── db/models.py             # BriefingRecord, EvidenceFact, …
│   │   ├── repositories/            # BriefingRepository, EvidenceRepository
│   │   ├── models/                  # Pydantic schemas (briefing, fact_base)
│   │   ├── services/                # retrieval · cleaning · facts · generation · audit · cache
│   │   └── mock/
│   ├── migrations/                  # Alembic revisions
│   └── tests/
├── bifrost-cache-policy/            # standalone gateway cache rules
├── docs/
└── docker-compose.yml               # Postgres + Bifrost gateway + Redis Stack
```

## How to Run Locally

```bash
# Postgres (Docker or local service). The compose file also runs the Bifrost
# gateway and Redis Stack; only `db` is needed to start the API.
docker compose up -d db

# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env          # optional: .env.example alone is loaded as a fallback
python -m alembic upgrade head     # schema is managed by migrations, not create_all
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
cp .env.example .env.local
npm install
npm run dev

# Tests (keep the local Postgres up: repository cases open a real session)
cd backend
python -m pytest -q            # 653 tests
```

## Where to Add Features

| Change | Put it here |
|--------|-------------|
| New API endpoint | `backend/app/api/endpoints/` + wire in `routers.py` |
| Business logic | `backend/app/services/` |
| DB queries | `backend/app/repositories/` |
| ORM tables | `backend/app/db/models.py` |
| Request/response shapes | `backend/app/models/` |
| New UI section | `frontend/src/components/briefing/` |
| Client API call | `frontend/src/api/` |
| Polling / form / panel state | `frontend/src/hooks/` |

## Frontend ↔ Backend

1. UI posts `BriefingRequest` to `POST /api/briefings`
2. Backend returns `202` + `{ id }`
3. Hook polls `GET /api/briefings/{id}` every 1.5s
4. Side panel loads history via `GET /api/briefings`
5. Clone copies form fields locally — no API call until submit

## Gotchas

- The schema is managed by Alembic (`python -m alembic upgrade head`). `init_db()`
  does **not** create tables; it checks that the migrations have been applied and
  points at the command when they have not.
- Report payload is stored as JSONB and only read back by id.
- Evidence is persisted separately (`evidence_facts`, `evidence_coverage`), so a
  re-run searches only the days not yet covered instead of the whole window.
- Conflict detection must never be serialized into the LLM prompt.
- Configuration precedence is real env vars > `backend/.env` > `backend/.env.example`
  (the example is loaded as a fallback so a clean checkout runs). Never put keys in
  the committed `.env.example`.
- `/api/debug` is mounted only while `DEBUG=true`; it exposes pipeline internals and
  bypasses the job queue.
- There is no auth by design: the service is loopback/demo-scoped, not public-ready.

## What NOT to Do

- Don't put fetch/axios calls inside presentational components
- Don't put SQL in route handlers or services — use repositories
- Don't invent auth for this app
- Don't use TypeScript `any` without a comment explaining why
