# CLAUDE.md — AI Editor Guide for BriefCortex

> This file helps AI coding assistants understand this codebase and work on it effectively.

## Project Overview

BriefCortex is a full-stack pre-meeting intelligence app. The Next.js frontend submits a briefing request; the FastAPI backend accepts it with `202`, processes asynchronously, and the UI polls until a `BriefingReport` is ready.

> **For AI contributors:** Before writing any code, read `KARPATHY.md`.

## Tech Stack

- **Frontend**: Next.js 15 (App Router) + React 19 + TypeScript + Tailwind CSS + Axios
- **Backend**: FastAPI + Pydantic v2 + Uvicorn
- **Auth**: none (authless demo)

## Repository Structure

```
BriefCortex/
├── frontend/
│   ├── app/                         # Routes only
│   │   ├── page.tsx                 # redirects to /briefing
│   │   └── briefing/page.tsx
│   └── src/
│       ├── api/                     # HTTP clients (axios)
│       ├── components/
│       │   ├── briefing/            # Feature UI
│       │   └── ui/                  # Reusable primitives
│       ├── hooks/briefing/          # Submit + poll hook
│       ├── lib/                     # Env + pure utils
│       ├── constants/
│       ├── types/
│       └── styles/globals.css       # Clay design tokens
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/endpoints/briefing.py
│   │   ├── core/config.py
│   │   ├── models/briefing.py
│   │   ├── services/                # ingestion, analysis, conflicts, briefing
│   │   └── mock/
│   └── tests/
├── ARCHITECTURE.md
├── KARPATHY.md
└── README.md
```

## How to Run Locally

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000

# Frontend
cd frontend
cp .env.example .env.local
npm install
npm run dev
```

## Where to Add Features

| Change | Put it here |
|--------|-------------|
| New API endpoint | `backend/app/api/endpoints/` + wire in `routers.py` |
| Business logic | `backend/app/services/` |
| Request/response shapes | `backend/app/models/` |
| New UI section | `frontend/src/components/briefing/` |
| Client API call | `frontend/src/api/` |
| Polling / form state | `frontend/src/hooks/` |
| Shared TS types | `frontend/src/types/` |

## Conventions

- Frontend: PascalCase components, camelCase hooks/utils, `@/` imports from `src/`
- Backend: snake_case files, thin routes, Pydantic for all I/O
- API routes under `/api/...`
- No secrets in source — use `.env` / `.env.local` from `.env.example`

## Frontend ↔ Backend

1. UI posts `BriefingRequest` to `POST /api/briefings`
2. Backend returns `202` + `request_id`
3. Hook polls `GET /api/briefings/{request_id}` every 1.5s
4. When `status === "completed"`, render the clay report

## Gotchas

- Tests import `app.services.*`; run pytest from `backend/` with the package root on `PYTHONPATH` (conftest handles this).
- `BriefingRequest` / `BriefingReport` exist both as HTTP models (`app.models.briefing`) and pipeline models (`app.services.*`). Keep HTTP contracts in `models/` when changing the API.
- Conflict detection must never be serialized into the LLM prompt.

## What NOT to Do

- Don't put fetch/axios calls inside presentational components
- Don't add a database unless explicitly asked
- Don't invent auth for this app
- Don't use TypeScript `any` without a comment explaining why
