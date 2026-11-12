# Pattern Catalog

Each entry: intent, when to use, when not to use, structural rules, and a bad/good example. Pick a pattern only after naming the problem shape in the LLD. A pattern with no concrete variation in scope is over-engineering.

## Selection table

| Problem shape | Pattern |
|---|---|
| Object assembled from many optional/conditional parts | Builder |
| Which concrete implementation to create depends on input or config | Factory |
| A family of interchangeable algorithms/rules selected at runtime | Strategy |
| State change must notify multiple decoupled subscribers | Observer |
| External/legacy interface does not match what your code expects | Adapter |
| Persistence details must stay behind a domain-facing collection interface | Repository |
| Coordinating a multi-step business workflow across repositories | Service |
| Data crossing a boundary with a stable, explicit shape | DTO |
| Multiple writes must commit or roll back as one atomic unit | Unit of Work |
| Collaborators must be swappable and testable, not constructed inline | Dependency Injection |

---

## 1. Builder

**Intent:** separate construction of a complex object from its representation; assemble optional parts step by step.

**When to use:** the target has many optional fields or conditional sections; construction order matters; a readable fluent sequence beats a 15-argument constructor.

**When NOT to use:** the object has a fixed small field set; a plain constructor or dataclass covers it.

**Structure rules:** builder holds mutable assembly state; `build()` returns an immutable validated object; no business rules live in the builder.

Bad:
```python
Briefing(company=c, goal=g, window=30, sources=None, quote=None,
         sections=[], tone="neutral", locale="en", ...)  # 10+ optional args
```

Good:
```python
briefing = (
    BriefingBuilder(company=c, goal=g)
    .with_window(30)
    .including_quote()
    .with_locale("ja")
    .build()
)
```

## 2. Factory

**Intent:** choose and create the right concrete implementation without exposing creation logic to callers.

**When to use:** selection depends on input, config, or runtime type; several implementations share one interface; callers should depend on the abstraction.

**When NOT to use:** there is one implementation today and no planned second; a direct constructor call is enough.

**Structure rules:** factory returns the abstraction; selection logic lives in one place; each product implements the same interface.

Bad:
```python
if channel == "news":
    client = NewsSearch()
elif channel == "local":
    client = LocalSearch()
# repeated in every caller
```

Good:
```python
client = SearchClientFactory.for_channel(channel)  # callers depend on SearchClient
```

## 3. Strategy

**Intent:** define a family of interchangeable algorithms, each in its own unit, selected at runtime.

**When to use:** the same workflow has multiple variants that differ by a rule (pricing, scoring, routing, filtering); variants must be added without editing the core flow.

**When NOT to use:** one fixed algorithm; a branch that will never gain a second case.

**Structure rules:** one common interface; each strategy implements it; the core flow depends on the interface, not on concrete variants.

Bad:
```python
def score(company, mode):
    if mode == "coverage": return len(urls)
    elif mode == "recency": return recency_score(...)
    # next mode adds another branch here
```

Good:
```python
scorer = ScorerFactory.for_mode(mode)
score = scorer.score(company)  # CoverageScorer / RecencyScorer each implement Scorer
```

## 4. Observer

**Intent:** notify subscribers when state changes, without coupling the publisher to its subscribers.

**When to use:** one event must fan out to several decoupled side effects (audit log, notification, metrics); subscribers can be added/removed independently.

**When NOT to use:** a single direct call after an action; synchronous inline continuation that already fits the flow.

**Structure rules:** publisher holds a list of subscriber callbacks/interfaces; it emits events, it does not know subscriber internals; avoid subscriber chains that call back into the publisher.

Bad:
```python
def publish(result):
    save_audit(result)
    send_email(result)
    update_metrics(result)  # every new side effect edits this function
```

Good:
```python
publisher.subscribe(AuditSink())
publisher.subscribe(NotificationSink())
publisher.publish(result)
```

## 5. Adapter

**Intent:** wrap an external or legacy interface so it matches the interface your code expects.

**When to use:** third-party SDK, legacy module, or wire format differs from your internal contract; you want the rest of the system insulated from that shape.

**When NOT to use:** you control the other side and can change it directly; a thin pass-through with no mapping adds nothing.

**Structure rules:** adapter translates between external and internal models; it does not add business decisions; one adapter per external boundary.

Bad:
```python
# service reaches into raw SDK response everywhere
raw = tavily.search(q)
score = raw["answer"]["entities"][0]["relevance"]
```

Good:
```python
results = TavilyAdapter(client).search(q)  # returns internal SearchResult list
```

## 6. Repository

**Intent:** encapsulate persistence behind a collection-like interface; the domain never writes queries itself.

**When to use:** data is loaded/saved through a stable interface; you may swap storage or fake it in tests; query details must not leak upward.

**When NOT to use:** a one-off read with no reusable access path; a data dump script outside the service layer.

**Structure rules:** repository exposes domain-oriented methods; it returns domain models; SQL/ORM details stay inside; services do not build queries.

Bad:
```python
# service opens a session and writes ORM filters
rows = session.query(Hiring).filter(Hiring.company_id == cid, ...).all()
```

Good:
```python
hires = hiring_repo.find_openings_for(company_id=cid)
```

## 7. Service

**Intent:** coordinate a business workflow across repositories and clients, owning the decision rules.

**When to use:** an operation spans multiple data sources or steps; the workflow has a named business capability.

**When NOT to use:** a pure passthrough to one repository with no rules; put that call on the handler or repository instead.

**Structure rules:** service orchestrates, does not persist directly; depends on repository/client abstractions; returns domain results, not HTTP shapes.

Bad:
```python
# handler contains the workflow: fetch, rank, filter, build response
@app.get("/briefing/{cid}")
def briefing(cid):
    rows = repo.find(cid)
    ranked = [r for r in rows if r.date >= cutoff]
    return JSONResponse({"items": ranked})
```

Good:
```python
briefing = briefing_service.prepare(company_id=cid, meeting_date=date)
return presenter.to_response(briefing)
```

## 8. DTO

**Intent:** give data crossing a boundary an explicit, stable shape separate from persistence or internal models.

**When to use:** request/response payloads, cached records, messages on a queue; the shape must not change when the internal model changes.

**When NOT to use:** data stays inside one unit; internal entities passed between close layers do not need a separate DTO.

**Structure rules:** DTO is a plain validated structure; mapping to/from domain models happens at the boundary; no behavior on the DTO.

Bad:
```python
# ORM model serialized straight to the API, leaking columns and relations
return JSONResponse(model_to_dict(orm_row))
```

Good:
```python
return BriefingResponse.model_validate(briefing)  # Pydantic DTO defines the wire shape
```

## 9. Unit of Work

**Intent:** group multiple writes that must succeed or fail together, committing one transaction at the end.

**When to use:** an operation updates several rows/aggregates; partial commit would leave inconsistent state.

**When NOT to use:** a single write; no transaction boundary to manage.

**Structure rules:** UoW coordinates repositories and one transaction; commit/rollback at the boundary; repositories share the UoW session.

Bad:
```python
repo_a.save(x)
repo_b.save(y)   # if this fails, x is already persisted
```

Good:
```python
with unit_of_work:
    repo_a.save(x)
    repo_b.save(y)
    unit_of_work.commit()
```

## 10. Dependency Injection

**Intent:** pass collaborators in from the outside so units stay decoupled and testable; no inline construction.

**When to use:** a collaborator can be replaced in tests, swapped by config, or reused across callers.

**When NOT to use:** a pure function with no collaborators; injecting a trivial constant adds ceremony.

**Structure rules:** dependencies arrive through constructor or parameters; the composition root wires concrete implementations; units depend on abstractions.

Bad:
```python
class BriefingService:
    def __init__(self):
        self.repo = PostgresRepository()   # hard-wired concrete
        self.client = TavilyClient()
```

Good:
```python
class BriefingService:
    def __init__(self, repo: ReportRepository, search: SearchClient):
        self.repo = repo
        self.search = search
```
