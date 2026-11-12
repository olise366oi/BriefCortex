# Modularity Rules

Use this file for the gate review (step 3) and for checking hacks during implementation. Rules are language-neutral; code examples are Python (FastAPI/Pydantic). Apply the same boundaries to TypeScript/Node backends.

## Layering

Dependencies point inward, one direction only:

```
handlers (routes/controllers)  →  services (business logic)  →  repositories (data access)  →  models
```

- **Handlers** parse input, validate the request shape, call one service, map the result to a response. No business rules, no SQL, no external orchestration here.
- **Services** own business decisions and workflow. They do not write SQL; they call repositories. They do not know how HTTP responses are shaped.
- **Repositories** own persistence and queries. No business policy, no cross-entity orchestration.
- **Models** are plain structures/types. No behavior that reaches out to databases or services.

Reverse dependencies and shared layer calls are defects. A service importing a router, or a repository calling a service, inverts the arrow.

## Single responsibility

- Each module, class, and function has one reason to change.
- A function doing two unrelated things (e.g., fetching data and shaping a notification) is split along those reasons.
- Limits to treat as signals, not hard compiler rules:
  - Function longer than ~50 lines: split unless the reason is explicit and documented.
  - Nesting deeper than 3 levels: extract a helper or early-return.
  - More than ~5 parameters: group into a request/command object.
- A "god object" that many unrelated features write to is split by responsibility, not by adding more flags to it.

## Dependency inversion and injection

- Depend on an abstraction (protocol/interface), not a concrete implementation, when the collaborator can vary or be swapped in tests.
- Inject collaborators through the constructor or function parameters. Do not instantiate them deep inside the call chain.

Bad:
```python
def report(company_id: str):
    db = SessionLocal()                      # concrete connection created inline
    client = TavilyClient(api_key=...)       # external client constructed inline
    rows = db.query(...)
```

Good:
```python
def report(company_id: str, repo: ReportRepository, search: SearchClient):
    rows = repo.find_for(company_id)
```

## State and coupling

- No global mutable state shared across modules. Module-level caches, singletons holding mutable state, or context objects passed everywhere create hidden coupling.
- Configuration and secrets are read at the composition root and injected, not imported ad hoc inside functions.
- Do not reach into another module's internals (private attributes, internal tables, non-exported functions). Go through its public interface.
- Avoid conditional sprawl: a switch/if on entity type repeated across modules signals a missing abstraction (see Strategy / Visitor in `pattern-catalog.md`).

## Hack blocklist

Each entry lists the pattern, why it rots the codebase, and the acceptable way out. An escape hatch exists only when stated; otherwise remove the hack.

### 1. Type escape to silence errors

- **Pattern:** `any`, unchecked casts, `# type: ignore`, or exceptions swallowed to make a checker stop complaining.
- **Why it rots:** the type system stops catching real drift; errors surface later at runtime in the caller.
- **Bad:** `data = cast(dict, payload)` then reading keys that may not exist.
- **Good:** validate the payload with a schema (Pydantic model / Zod), raise a typed validation error on mismatch.
- **Escape:** none for shipped code. A local, commented workaround with a tracked issue is allowed only temporarily.

### 2. Commented-out dead code

- **Pattern:** blocks of old code left commented "in case we need it".
- **Why it rots:** readers cannot tell what is live; history lives in version control, not in source.
- **Good:** delete it. Recover from git history if it returns.

### 3. Magic values

- **Pattern:** literal numbers/strings repeated across modules (timeouts, status codes, thresholds, field names).
- **Why it rots:** one value drifts out of sync; intent is invisible.
- **Good:** name the constant at the layer it belongs to; reuse it everywhere.

### 4. Shipped TODO placeholders

- **Pattern:** `# TODO: implement`, `pass`, a stub returning fake data, or a `NotImplementedError` left in a shipped path.
- **Why it rots:** callers build on a branch that does nothing; the gap is invisible in review.
- **Good:** finish the path, or gate it behind an explicit disabled flag with the missing behavior documented.

### 5. Copy-paste blocks

- **Pattern:** the same logic duplicated across two or more modules with small edits.
- **Why it rots:** a fix lands in one copy and misses the others; drift accumulates.
- **Good:** extract the shared logic to one module; parameterize the real differences.

### 6. Reaching across boundaries

- **Pattern:** a service reading another service's tables directly, a handler building repository queries, a model importing a client.
- **Why it rots:** layers fuse; changing one internals breaks everyone who reached in.
- **Good:** extend the public interface of the owning module; call through it.

### 7. Patch around the structure

- **Pattern:** special-case branches bolted onto an existing unit to work around its shape, instead of changing the shape.
- **Why it rots:** branches multiply; the original abstraction stops matching reality.
- **Good:** stop and revise the design in the LLD; extract or abstract the varying part.

### 8. Over-broad exception handling

- **Pattern:** bare `except:` or `except Exception` that swallows everything and continues.
- **Why it rots:** failures disappear; partial state looks like success.
- **Good:** catch the expected typed errors, handle or re-raise; log and re-raise unexpected ones.

## Refactor-first rule

When the quickest fix feels like a small hack, stop and name the underlying structural gap. If the gap fits an existing pattern or extraction, record it in the LLD and do the clean version. Hacks are accepted only for a documented, time-boxed emergency with a tracked follow-up; they are not the default implementation.
