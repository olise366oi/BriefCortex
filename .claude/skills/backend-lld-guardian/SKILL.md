---
name: backend-lld-guardian
description: "Enforce clean, modular backend low-level design (LLD) before implementation and block hacky, spaghetti-prone code. Use before writing or changing backend modules, services, repositories, models, schemas, or data-access code; before adding or choosing a design pattern; before a large refactor or a feature that touches multiple modules; and when reviewing AI-generated code that is becoming tangled. Covers Python/FastAPI and TypeScript/Node backends. Produces a short LLD design draft, runs a modularity gate, then a post-implementation self-check. Not for frontend UI work, one-line typo fixes, or whole-system architecture strategy."
---

# Backend LLD Guardian

## Overview

This skill keeps backend changes modular and reviewable. Before implementation, force a short low-level design (LLD) that states module boundaries, contracts, and affected surface. During implementation, hold the line on layering, single responsibility, and dependency direction. After implementation, run a checklist against the actual diff.

The goal is to stop AI-generated code from rotting into spaghetti: no silent hacks, no hidden cross-module reach-ins, no pattern added without a reason.

## Change-size triage

Classify the change before writing any code. Match the rigor to the size; do not run the full LLD gate on a trivial fix.

| Size | Signal | Required process |
|------|--------|------------------|
| Trivial | Typo, config value, single-function bug fix; no signature or boundary change | Lightweight checklist only (`review-checklist.md`, scope + hacks sections) |
| Standard | One module, one clear responsibility; existing patterns reused | LLD sketch (`lld-template.md`, sections 1–4, 7–8) + full checklist |
| Large | Cross-module change, new abstraction, new design pattern, data-model or persistence change, public contract change | Full LLD draft through the gate, then implementation, then full checklist |

If you cannot tell which tier the change is, treat it as the larger tier.

## Existing debt is not your task

These rules gate the code you are about to write or change, not the codebase you inherited. Most repos already contain units that exceed the size signals or that reach across a layer; that is background debt, not a reason to refactor on sight.

- Do not expand scope to clean up untouched modules. If a violation sits outside the diff, report it and move on.
- A pre-existing violation inside the affected surface does not block the change by itself, but you must not add a new one next to it, and you must not copy the existing shortcut into new code.
- When a clean fix genuinely requires touching legacy structure, put it in the LLD's affected surface first, as its own step, rather than folding it into the feature change.

Running `scripts/check_metrics.py` over an old tree will report every legacy offender at once. Point it at the files you changed, or read its output as a map of existing debt rather than a verdict on this change.

## Repo mapping (when the project is a FastAPI + Next.js tree)

If `backend/app/` exists, the abstract layers land here:

| Layer | Path |
|-------|------|
| Handlers | `backend/app/api/endpoints/` (wire in `routers.py`) |
| Services | `backend/app/services/` |
| Repositories | `backend/app/repositories/` |
| Models | `backend/app/db/models.py`, wire schemas in `backend/app/models/` |
| Tests | `backend/tests/`, mirroring `app/` |

Read the repo's own `CLAUDE.md` / `.cursorrules` / `docs/KARPATHY.md` first: when a project rule and this skill disagree, the project rule wins for placement, and this skill governs boundaries and hack policy.

## Workflow

### 1. Recon
- Read the existing modules, callers, and callers' callers before proposing anything.
- Map the current dependency direction. Note where business logic, SQL, and validation live today.
- Do not write implementation code in this step.

### 2. LLD draft
- Fill the relevant sections of `references/lld-template.md`.
- State goals and non-goals, affected surface, module boundaries, interface contracts, failure paths, and test plan.
- If a design pattern is involved, justify it against `references/pattern-catalog.md`: name the problem shape, the chosen pattern, and why a simpler solution does not fit.
### 3. Gate review (blocking)
- The draft must pass the modularity rules in `references/modularity-rules.md` before any implementation code is written.
- Check: dependency direction has no cycles; each new unit has one responsibility; no module reaches into another's internals; no block from the hack list is planned.
- If the gate fails, revise the draft first. Do not start coding around a failed design.

### 4. Implement
- Stay inside the affected surface listed in the LLD. Any file, module, or dependency outside that surface requires updating the LLD first, not an in-flight patch.
- Reuse existing project patterns before introducing a new one.
- Keep dependencies explicit and injected; do not reach for globals or singletons to shortcut wiring.

### 5. Self-check
- Run `references/review-checklist.md` against the actual diff.
- Report each item as pass / fail / N/A with a reason. List every failed item with file and line. Do not mark the change done while a blocking item fails.
- Deterministic metrics (function length, nesting depth, file size) can be checked with `scripts/check_metrics.py`.

## Hard rules at a glance

Full rules and code examples live in `references/modularity-rules.md`. The non-negotiable points:

- Dependencies point inward: handlers → services → repositories → models. No SQL in services, no business logic in handlers.
- One responsibility per unit; one reason to change.
- Depend on abstractions, inject dependencies; do not construct collaborators deep inside a function.
- No global mutable state shared across modules.
- No type escape (`any`, casts used to silence errors), dead commented-out code, magic values, or shipped TODO placeholders.
- Extract or abstract instead of patching around an existing structure.

## Pattern selection

Before adding Builder, Factory, Strategy, Observer, Adapter, Repository, Service, DTO, Unit of Work, or DI, open `references/pattern-catalog.md`. Each entry states when to use it, when not to use it, structural rules, and a bad/good example. A pattern must solve a stated problem; do not add one "for extensibility" with no concrete extension in scope.

## References

| File | Read when |
|------|-----------|
| `references/lld-template.md` | Producing the design draft in step 2 |
| `references/modularity-rules.md` | Applying the gate in step 3, and checking hacks during implementation |
| `references/pattern-catalog.md` | Choosing or validating a design pattern |
| `references/review-checklist.md` | Running the post-implementation self-check in step 5 |
| `scripts/check_metrics.py` | Measuring size signals on the files you changed |
| `tests/test_check_metrics.py` | Changing the guardrail itself |
| `assets/cursor-rules.mdc` | Copy into a Cursor project as `.cursor/rules/backend-lld.mdc` |
| `assets/claude-instructions.md` | Paste into `CLAUDE.md` for a prose-only setup |
