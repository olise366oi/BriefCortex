# Claude setup for Backend LLD Guardian

Choose one of the two options below.

## Option A — Skills directory (recommended)

Copy the whole `backend-lld-guardian/` folder into the project's `.claude/skills/` directory:

```
.claude/skills/backend-lld-guardian/
├── SKILL.md
├── references/
└── scripts/
```

Claude loads `SKILL.md` when the description matches a backend design or refactor task. Keep the `references/` and `scripts/` folders next to it so relative links resolve.

## Option B — Paste into CLAUDE.md

If you do not use a skills directory, paste the block below into the project root `CLAUDE.md` (or a `backend-lld.md` imported from it).

---

## Backend LLD Guardian

Before writing or changing backend modules, services, repositories, models, schemas, or data-access code, produce a short low-level design and enforce clean modular boundaries. Block hacky, spaghetti-prone implementations.

### Triage

- **Trivial** (typo, config, single-function fix): check scope and the hack list only.
- **Standard** (one module, one responsibility): short design sketch, then implement, then self-check.
- **Large** (cross-module, new abstraction/pattern, data-model or contract change): full design draft and gate review before any implementation code.

### Workflow

1. **Recon:** read existing modules, callers, and dependency direction. Do not write code yet.
2. **Design draft:** goal/non-goals, affected files, module boundaries, signatures/types, failure paths, test plan. If a pattern is used, name the problem shape and why a simpler solution does not fit.
3. **Gate:** the draft must pass the rules below before coding. Revise the draft rather than coding around a failed design.
4. **Implement:** stay inside the listed affected surface. Anything outside requires updating the design first. Reuse existing patterns; inject collaborators.
5. **Self-check:** review the actual diff; report each item pass/fail/N/A with a reason. Report failures with file and line. Do not mark done while a blocking item fails.

### Layering and modularity

- Dependencies point inward: handlers → services → repositories → models. No reverse calls, no cycles.
- Handlers parse input and call one service; services own business rules and call repositories; repositories own queries; models hold plain structures.
- No SQL in services/handlers; no business logic in repositories/models.
- One responsibility per unit; one reason to change.
- Depend on abstractions; inject collaborators through constructor or parameters. Never construct concrete clients/repositories deep inside a function.
- No global mutable state shared across modules; config and secrets are injected from the composition root.

### Blocked in shipped code

- Type escape: `any`, unchecked casts, type-ignore used to silence the checker. Validate with a schema instead.
- Commented-out dead code. Delete it; history lives in git.
- Magic numbers/strings repeated across modules. Name the constant.
- Shipped TODO stubs, fake returns, `NotImplementedError` on a live path.
- Copy-paste blocks across modules. Extract the shared logic.
- Reaching into another module's internals. Extend its public interface instead.
- Bare/over-broad exception swallowing. Catch typed expected errors; re-raise unexpected ones.
- Special-case branches bolted onto a structure instead of changing the structure. Stop and revise the design.

### Size signals

- Function > ~50 lines: split unless explicitly justified.
- Nesting > 3 levels: extract a helper or early-return.
- More than ~5 parameters: group into a request/command object.

### Pattern selection

Add a pattern only for a stated problem: optional assembly → Builder; runtime-selected implementation → Factory; swappable algorithm → Strategy; fan-out side effects → Observer; external interface mismatch → Adapter; storage behind a collection interface → Repository; multi-step workflow → Service; wire-boundary shape → DTO; multi-write atomicity → Unit of Work; swappable collaborators → DI. Do not add a pattern "for future extensibility" with no concrete case.

### Self-check

Scope (diff matches design, no drive-by cleanup) · Modularity (one responsibility, correct direction, no reach-in) · Patterns (justified, no over-engineering) · Hacks (blocked list clean) · Readability (length, nesting, names) · Tests (happy path, failure paths, edge cases).

---

## Notes

- The full reference versions (LLD template, detailed modularity rules, pattern catalog with examples, checklist) live in this skill's `references/` folder. If pasting only the block above, keep these rules in mind; copy the matching reference file when a detailed pattern or template is needed.
- Deterministic size checks for Python: `python scripts/check_metrics.py <path>`.
