# LLD Draft Template

Fill this template before implementation. Mark each section as required by the change-size triage in `../SKILL.md`. Keep the draft short: it is a design contract, not a document for its own sake.

## 1. Goal and non-goals

- **Goal:** what this change accomplishes, in one or two sentences.
- **Non-goals:** what is explicitly out of scope this round.
- **Problem shape:** one line describing the structural problem being solved (e.g., "construction of a report with optional sections", "swappable pricing rule").

## 2. Affected surface

- **Files/modules to change:** list each path.
- **Callers affected:** who calls the changed code today; what breaks if a signature moves.
- **New dependencies:** any new package, service, table, or external endpoint introduced.
- **Out of scope:** anything looked at but deliberately untouched.

> Scope guard: if implementation reaches a file or dependency not listed here, stop and update this draft before continuing. Do not patch around the boundary.

## 3. Module boundaries and responsibilities

- **New/changed units:** name each module, class, or function.
- **Responsibility:** one sentence per unit, stating its single reason to change.
- **Ownership boundary:** what this unit may do, and what it must delegate to another unit.
- **Reuse:** existing project code reused; anything duplicated that should instead be extracted.

## 4. Interface contracts

- **Public signatures:** function/method names, parameters, return types, raised errors.
- **Types/schemas:** DTO or model shape; validation rules that belong at this layer.
- **Error contract:** which errors propagate to the caller, which are handled internally, and which are wrapped.
- **Backward compatibility:** whether existing callers keep working; any deprecation or migration needed.

## 5. Data model and persistence changes

- **Schema changes:** new/changed tables, columns, indexes, constraints.
- **Migration:** forward migration and rollback; whether old rows need backfill.
- **Read/write path:** where queries live; which repository owns them.

Skip for changes that touch no persistence.

## 6. Dependencies and direction

- **Depends on:** list each collaborator and its abstraction.
- **Depended on by:** who uses this unit.
- **Direction check:** confirm no cycle forms against the layering rules in `modularity-rules.md`.
- **Wiring:** where the dependency is injected; no collaborator constructed inside a deep function.

## 7. Failure paths and edge cases

- **Failure modes:** invalid input, missing data, external call failure, partial state.
- **Behavior under each:** retried, wrapped, rejected, or surfaced; where the boundary sits.
- **Edge cases:** empty sets, duplicates, timezone/date boundaries, concurrency.

## 8. Test plan

- **Unit cases:** happy path, each failure path from section 7, boundary values.
- **Integration cases:** contract with collaborators; persistence round-trip where relevant.
- **What is not tested:** gaps stated explicitly rather than assumed covered.

## 9. Rollback and migration

- **Rollback:** how to revert if the change ships broken.
- **Migration order:** code and schema deployment sequence if both change.

## 10. Open questions

- Unresolved decisions, alternatives considered, and what needs confirmation before coding starts.
