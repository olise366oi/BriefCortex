# Post-Implementation Checklist

Run this against the actual diff after implementation. For every item, mark **pass**, **fail**, or **N/A** with a one-line reason. A failing blocking item means the change is not done. Report failures with file and line.

## Scope

- [ ] The diff matches the affected surface in the LLD; no file or module was changed outside it.
- [ ] No out-of-scope refactor, rename, or drive-by cleanup was bundled into this change.
- [ ] Any new dependency added in code appears in the LLD's dependency list.

## Modularity

- [ ] Each new/changed unit has one responsibility and one stated reason to change.
- [ ] Dependency direction follows handlers → services → repositories → models; no reverse dependency or cycle.
- [ ] No SQL/ORM query lives in a service or handler; no business rule lives in a repository or model.
- [ ] Collaborators are injected; nothing concrete is constructed deep inside a function.
- [ ] No module reaches into another's private internals; access goes through the public interface.

## Patterns and design

- [ ] Every introduced pattern matches a problem shape in `pattern-catalog.md`, and the simpler alternative was considered.
- [ ] No pattern was added speculatively "for future extensibility" without a concrete case.
- [ ] Shared logic duplicated across modules has been extracted; no copy-paste blocks remain.

## Hack blocklist

Check each entry against `modularity-rules.md`. None may be present in shipped code:

- [ ] No `any`/unchecked cast/`type: ignore` used to silence the type checker.
- [ ] No commented-out dead code left in the diff.
- [ ] No unnamed magic numbers/strings; shared values are named constants.
- [ ] No TODO stub, fake return, or `NotImplementedError` on a live path.
- [ ] No bare or over-broad exception swallowing; expected errors are typed, unexpected ones re-raised.
- [ ] No global mutable state introduced for cross-module sharing.

## Size and readability

- [ ] Functions stay under ~50 lines; nesting stays within 3 levels; long parameter lists are grouped into an object.
- [ ] Names express intent; no abbreviations that hide meaning.
- [ ] Comments explain why, not what the code already says.
- [ ] Run `scripts/check_metrics.py` if the project is Python; address any reported out-of-range unit, or justify it in the LLD.

## Tests

- [ ] Happy path covered.
- [ ] Each failure path from the LLD has a test.
- [ ] Boundary/edge cases (empty set, duplicate, date boundary) covered or explicitly marked N/A with reason.
- [ ] Persistence changes have a round-trip test and migration/rollback checked.

## Output format

End the self-check with a short block:

```
Scope:        pass / fail / N/A — reason
Modularity:   pass / fail / N/A — reason
Patterns:     pass / fail / N/A — reason
Hacks:        pass / fail / N/A — reason
Readability:  pass / fail / N/A — reason
Tests:        pass / fail / N/A — reason

Blocking failures:
- file:line — item — what is wrong, what must change
```

If there are no blocking failures, state that explicitly. Do not summarize away a fail by calling it minor.
