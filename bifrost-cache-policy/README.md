# bifrost-cache-policy

**Evidence-derived cache keys, graded TTLs, and cross-set invalidation for the
[Bifrost](https://github.com/maximhq/bifrost) AI gateway.**

A stock Bifrost install gains all three rules by calling one function before the
request leaves your process. No modified binary, no compiled `.so`, no embedding
provider, no model call, no new dependency.

```python
from bifrost_cache_policy import build_cache_headers

headers = build_cache_headers(
    partition="acme-001",
    meeting_purpose="reduce price by 15%",
    fact_texts=["ACME reported Q3 earnings and cut guidance.", ...],
)
# {"x-bf-cache-key": "acme-001_45f29a8081bc_aaa66f08cfed78f3",
#  "x-bf-cache-ttl": "24h",
#  "x-bf-content-class": "volatile"}
```

Three headers in, cache behaviour out: `x-bf-cache-key` and `x-bf-cache-ttl`
out, `x-bifrost-cache-hit` back. The gateway stores, serves and expires; this
package decides **what may be reused and for how long** — and when a stored
answer must stop being served.

| | |
|---|---|
| Python | 3.10+ |
| Runtime dependencies | none |
| Test suite | 74 cases (68 offline, 6 against a live gateway) |
| Verified against | `maximhq/bifrost:v2.4.0`, direct (hash) cache mode |
| Distribution | install from this repository; not published to an index |

---

## Table of contents

- [Why this exists](#why-this-exists)
- [What Bifrost gives you, and what it does not](#what-bifrost-gives-you-and-what-it-does-not)
- [The three rules](#the-three-rules)
  - [1. TTL is a property of the evidence](#1-ttl-is-a-property-of-the-evidence)
  - [2. The cache key identifies the evidence, not the run](#2-the-cache-key-identifies-the-evidence-not-the-run)
  - [3. A contradiction invalidates the stored answer](#3-a-contradiction-invalidates-the-stored-answer)
- [Installation](#installation)
- [Integrating with a stock gateway](#integrating-with-a-stock-gateway)
- [API reference](#api-reference)
- [Worked example: a complete request path](#worked-example-a-complete-request-path)
- [Measured behaviour](#measured-behaviour)
- [Running the live gateway probe](#running-the-live-gateway-probe)
- [Testing](#testing)
- [Design decisions](#design-decisions)
- [Scope and limits](#scope-and-limits)
- [Troubleshooting](#troubleshooting)
- [License and provenance](#license-and-provenance)

---

## Why this exists

A gateway decides *whether to replay a stored response* **before** inference
happens. That single fact constrains every caching rule you could install:

| Constraint | Why | What Bifrost offers |
|---|---|---|
| It must be cheap | the decision runs on the request path, hit or miss | a key you supply; no callback |
| It must key on the evidence | two requests sharing a prompt template can differ in every fact | `x-bf-cache-key`, supplied per request |
| It must be able to refuse a stored answer | facts can each be true and still be overturned later | clear-by-key management endpoint |

Bifrost's `semantic_cache` plugin covers the storage and the replay. It does not
cover the *policy*: one global TTL, and a similarity test that needs an
embedding provider. This package is that missing policy, expressed as pure
deterministic functions with no runtime dependency — which is exactly what
something on the request path has to be.

The three operational failures it was written against, each observed on a real
news-grounded pipeline:

### Stale answers under a correct-looking key

Same company, same objective, refreshed evidence. A prompt digest appears
stable, but retrieval assembles facts in the order its concurrent lanes finish,
so a naive key that hashes the list as given changes on every run for identical
evidence. The cache hit rate reads as zero and nothing explains why.

→ The evidence digest **sorts and canonicalises** before hashing.

### One TTL for everything

A brief built from same-day market coverage and one built from an annual filing
have completely different shelf lives. A single global TTL either expires the
filing early (paying for inference you did not need) or serves a share-price
move for a week (a stale answer delivered with full confidence).

→ The TTL is graded from the evidence and sent per request as `x-bf-cache-ttl`.

### A cache that cannot see a contradiction

A brief written while the company was hiring is wrong the moment a layoff is
reported. Every individual sentence in it was true when written, so no
fingerprint over the text can flag it — the stored and the fresh evidence are
each internally consistent and disagree with each other.

→ `check_reuse` refuses the hit; `cache_key_to_clear` gives you the key to drop.

---

## What Bifrost gives you, and what it does not

Stated plainly, because the boundary is the whole reason this is a separate
package:

| Capability | Bifrost | This package |
|---|---|---|
| Store and replay a response | ✅ `semantic_cache` | — |
| Exact (hash) matching without embeddings | ✅ `dimension: 1`, no provider | — |
| Per-request cache partition | ✅ `x-bf-cache-key` | ✅ derives it from the evidence |
| Per-request TTL override | ✅ `x-bf-cache-ttl` | ✅ grades it from the evidence |
| Report whether a request hit | ✅ `x-bifrost-cache-hit` | — |
| Drop an entry | ✅ `DELETE /api/cache/clear-by-key/{key}` | ✅ decides *when* it must be dropped |
| Decide reuse from a contradiction | ❌ | ✅ `check_reuse` |
| Near-duplicate detection without embeddings | ❌ | ✅ `is_near_duplicate` |
| Evidence-set fingerprinting | ❌ | ✅ `evidence_digest` |
| Classify evidence to set a TTL | ❌ | ✅ `classify_fact_texts` |

### The extension points this depends on

All documented, all present in a stock install:

| Extension point | Used for | If it is absent |
|---|---|---|
| `plugins[]` entry `semantic_cache` with `dimension: 1` **and** no `provider` | exact (hash) cache storage and replay | nothing is cached |
| `vector_store` — Redis / Valkey, Qdrant, Weaviate or Pinecone | where entries live; **required even in direct mode** | the cache plugin will not load |
| `x-bf-cache-key` request header | the partition this package derives from the evidence | the request bypasses caching entirely |
| `x-bf-cache-ttl` request header | the graded TTL, per request | every entry gets the one configured default |
| `x-bifrost-cache-hit`, `-cache-id`, `-hit-type` response headers | the verdict, read back for logs and for invalidation | cache behaviour is unobservable |
| `DELETE /api/cache/clear-by-key/{cacheKey}` | removing an entry once `check_reuse` refuses it | a contradicted answer stays until it expires |

Nothing here requires a custom Go plugin. Native Bifrost plugins are `.so`
shared objects that must be built with the **same Go version and the same
architecture** as the gateway, cannot cross-compile, and therefore cannot be
shipped as a pip-installable policy. That is not a limitation of this package —
it is the reason it is a client-side policy rather than a plugin binary.

---

## The three rules

### 1. TTL is a property of the evidence

| Class | Signals | TTL |
|---|---|---|
| `volatile` | earnings, guidance, stock moves, recalls, lawsuits | `24h` |
| `structural` | hiring, appointments, product launches | `72h` |
| `primary` | annual reports, filings, press releases | `168h` |

Precedence runs `volatile` → `structural` → `primary`: a fact set is only as
cacheable as its **most perishable** member. Unclassified evidence falls back to
the **shortest** class. Fail closed — never keep something for a week because
nobody recognised it.

The vocabulary is data, and it is small on purpose (17 / 13 / 8 keywords). You
can widen it per call without forking the rule:

```python
from bifrost_cache_policy import CLASS_KEYWORDS, classify_fact_texts

vocabulary = dict(CLASS_KEYWORDS)
vocabulary["structural"] = (*vocabulary["structural"], "reorganisation", "restructuring")

classify_fact_texts(["The company announced a reorganisation."], vocabulary)
# 'structural'
```

Precedence, matching and TTL mapping stay shared — only the words change.

#### Matching is whole-word, and that is not cosmetic

Both of the following were measured on live press text, and both silently
corrupt a TTL:

| Text | Keyword | Substring match | Whole-word match |
|---|---|---|---|
| `third quarter` | `hir` (hiring stem) | **hits** → misread as hiring | no hit ✅ |
| `shareholders` | `shares` | **hits** → misread as a stock move | no hit ✅ |
| `mission` | `miss` | **hits** | no hit ✅ |
| `announced` | `announce` | hits | hits ✅ |
| `plunging` / `plunged` / `plunges` | `plunge` | hits | hits ✅ (final-e stems drop the `e`) |
| `rife` | `rif` (reduction in force) | **hits** → false layoff | no hit ✅ |
| `RIF` | `rif` | — | hits ✅ (case-insensitive) |

Two stem forms are supported:

- trailing `*` — a deliberate prefix stem (`regulat*` matches `regulatory`,
  `regulation`, `regulations`), used only where every extension is relevant;
- final `e` — drops the `e` before `-ing` / `-ed` / `-es`, so `plunge` matches
  `plunging`, `plunged`, `plunges`.

Note the asymmetry that the vocabulary already accounts for: `hiring` does **not**
match `hires` or `hired` (different stem), which is why all three appear in the
structural list separately.

### 2. The cache key identifies the evidence, not the run

```
x-bf-cache-key = <partition>_<objective-digest>_<evidence-digest>
                  variable       12 hex            16 hex
```

- **partition** — whatever scopes the cache for you: a company id, a tenant, a
  feature name. Lowercased, internal whitespace collapsed to `_`.
- **objective digest** — SHA-256 over the canonicalised objective, first 12 hex.
  A different objective on identical evidence must not reuse the same brief.
- **evidence digest** — SHA-256 over the **sorted, canonicalised** fact texts,
  first 16 hex.

Order independence is the load-bearing property:

```python
evidence_digest(["A", "B"]) == evidence_digest(["B", "A"])   # True
evidence_digest(["Revenue  rose 12%."]) == evidence_digest(["revenue rose 12%."])  # True
```

Keys are header- and URL-safe by construction: the digest components are hex,
and the partition has its whitespace collapsed. A key derived from an
ASCII-only objective is pure ASCII, so `DELETE /api/cache/clear-by-key/{key}`
needs no percent-encoding. A partition written in a non-Latin script passes
through as-is (Bifrost accepts it; URL-encode it if your client is strict).

### 3. A contradiction invalidates the stored answer

```python
from bifrost_cache_policy import check_reuse, cache_key_to_clear

verdict = check_reuse(stored_evidence, fresh_facts)
if not verdict.reusable:
    ...  # regenerate, then optionally clear:
    # httpx.delete(f"{BIFROST_URL}/api/cache/clear-by-key/{cache_key}")
```

Four rule families, each two keyword groups. A conflict is **one item hitting
group A while another hits group B**:

| Rule | Group A | Group B |
|---|---|---|
| `headcount_up_down` | layoff, reduction in force, rif, cut jobs, job cuts, cut roles, roles cut | hiring, recruiting, hiring spree, expanding headcount, open roles |
| `cost_up_down` | price hike, price increase, raising prices | price cut, price reduction, discount |
| `expand_contract` | expansion, new plant, new factory | shutdown, closing plant, facility closure |
| `profit_loss` | profitable, profit growth | loss, net loss, wider loss |

Guarantees the implementation holds:

- **Cross-item only.** One sentence stating both directions ("cut jobs in Q2
  but resumed hiring in Q3") is a description of a turn, not a contradiction
  with itself. Neither side of a pair may state both directions.
- **Identical text is not new evidence.** A repeated sentence is skipped.
- **One overturning pair per new item is enough** to invalidate; the scan stops
  for that item.
- **Absent stored evidence refuses reuse.** `check_reuse(None, ...)`,
  `check_reuse([], ...)` and `check_reuse({"hard_facts": []}, ...)` all return
  `reusable=False`. An unverifiable cache hit is precisely the failure this
  exists to prevent, so it fails closed rather than serving on trust.
- **Items are duck-typed.** An object with `.content` / `.source_url`, a mapping
  with `"content"` / `"source_url"`, or a bare string all work. The rule does not
  need to know your evidence model.
- **The rule table is injectable.** Pass `rules=(("supply_up_down",
  ("shortage",), ("glut",)),)` to compare against your own domain instead.
- **The attribution is kept.** Each conflict carries `old_fact`, `old_source`,
  `new_fact`, `new_source` (each text truncated to 200 chars) so a log line can
  name both sides.

`Verdict.reason` is a single readable sentence for logging:

| Situation | `reason` |
|---|---|
| reusable | `no cross-set contradiction` |
| conflicts found | `N fact(s) overturn the stored reading` |
| evidence absent / empty | `stored evidence could not be cross-checked` |

---

## Installation

From a checkout of the repository that vendors this package:

```bash
pip install -e bifrost-cache-policy
```

Requires Python 3.10+. **No runtime dependencies.** `httpx` is needed only for
the live gateway probe (`pip install "bifrost-cache-policy[live]"`), `pytest`
only for the suite.

As a dependency of another project, the cleanest form is a path reference in
`requirements.txt`:

```text
-e ../bifrost-cache-policy
```

The package is `src/`-layout, so `pip install` (editable or wheel) is the
supported way to consume it; adding `src` to `PYTHONPATH` also works if you
prefer not to install.

---

## Integrating with a stock gateway

### Step 1 — enable direct (hash) caching

The rules decide *what* may be cached and for how long; Bifrost does the storing.
Direct mode needs no embedding provider:

```json
{
  "vector_store": {
    "enabled": true,
    "type": "redis",
    "config": { "addr": "redis:6379" }
  },
  "plugins": [
    {
      "name": "semantic_cache",
      "enabled": true,
      "config": {
        "dimension": 1,
        "ttl": "5m",
        "cache_by_model": true,
        "cache_by_provider": true,
        "disable_identity_scoping": true
      }
    }
  ]
}
```

Three things this config gets right, each a documented trap if wrong:

1. **`dimension: 1` and no `provider` means hash mode.** `dimension: 1` *with* a
   provider is read as *semantic* mode with a one-dimensional embedding — whose
   similarity test maps every prompt to a single number, making it close to
   random. That specific misconfiguration is what this package exists to replace.
2. **A vector store is required even in direct mode.** That is where entries
   live. Without one, the `semantic_cache` toggle has nowhere to store and the
   plugin will not load.
3. **`ttl` here is only a default.** Every value this package sends overrides it
   per request, which is the entire point of rule 1.

> **Deployment note that costs an afternoon:** with a `config_store` enabled
> (SQLite or Postgres), `config.json` is a **seed and drift detector**, not the
> live configuration. Editing the file does not override a `semantic_cache` row
> that already exists in the store — change that row through the UI or the
> `/api/plugins` API. Set `source_of_truth: "config.json"` if you would rather
> have the file be authoritative and drive it from git.

### Step 2 — send the headers

```python
import httpx
from bifrost_cache_policy import build_cache_headers

headers = build_cache_headers(
    partition=company_id,
    meeting_purpose=meeting_purpose,
    fact_texts=[fact.content for fact in fact_base],
)

response = httpx.post(
    f"{BIFROST_URL}/v1/chat/completions",
    headers={"Content-Type": "application/json", **headers},
    json={"model": "deepseek/deepseek-chat", "messages": messages},
)
response.headers.get("x-bifrost-cache-hit")   # "true" / "false"
```

Caching is opt-in per request. With no `x-bf-cache-key` (and no
`default_cache_key` configured), the request bypasses the cache entirely — a
missed header costs money, never correctness. That is the safe direction for the
default to fail in.

### Step 3 — read the verdict and act on it

Every looked-up request carries:

| Header | Meaning |
|---|---|
| `x-bifrost-cache-hit` | `true` / `false` — the replay verdict |
| `x-bifrost-cache-id` | storage id of the entry |
| `x-bifrost-cache-hit-type` | `direct` / `semantic` on a hit |
| `x-bifrost-cache-hit-latency-ms` | lookup cost, on a hit |

To drop an entry once `check_reuse` refuses it:

```python
httpx.delete(f"{BIFROST_URL}/api/cache/clear-by-key/{decision.cache_key}")
```

Management endpoints are auth-gated. On the stock container used for the probe
this answered **403** even with the default admin basic credential, so treat
clearing as a capability you enable deliberately rather than something that
always works. The invalidation *decision* is this package's job; the *permission*
is your deployment's.

---

## API reference

32 names, one flat import surface. `import bifrost_cache_policy as bcp`.

### Headers

| Name | Signature / value | Purpose |
|---|---|---|
| `build_cache_headers` | `(partition, meeting_purpose, fact_texts, keywords=None) -> dict` | the three headers, ready to spread into a request |
| `build_cache_key` | `(partition, meeting_purpose, fact_texts) -> str` | just the key |
| `decide` | `(partition, meeting_purpose, fact_texts, keywords=None) -> CacheDecision` | one typed object instead of a dict |
| `cache_key_to_clear` | `(decision) -> str` | the value for `clear-by-key/{key}` |
| `CacheDecision` | frozen dataclass: `cache_key`, `ttl`, `content_class`, `headers` | |
| `CACHE_KEY_HEADER` / `CACHE_TTL_HEADER` / `CONTENT_CLASS_HEADER` | `"x-bf-cache-key"` / `"x-bf-cache-ttl"` / `"x-bf-content-class"` | constants, so a typo is an import error |

### TTL

| Name | Signature / value | Purpose |
|---|---|---|
| `classify_fact_texts` | `(texts, keywords=None) -> str` | the most perishable class present |
| `classify_ttl` | `(fact_class, ttl_by_class=None) -> str` | class name → Bifrost duration string |
| `FACT_CLASSES` | `("volatile", "structural", "primary")` | precedence order |
| `TTL_BY_CLASS` | `{"volatile": "24h", "structural": "72h", "primary": "168h"}` | |
| `CLASS_KEYWORDS` | `dict[str, tuple[str, ...]]` | the default vocabulary |
| `DEFAULT_CLASS` | `"volatile"` | the fail-closed fallback |

### Keys and similarity

| Name | Signature / value | Purpose |
|---|---|---|
| `evidence_digest` | `(texts) -> str` (16 hex) | order- and formatting-independent |
| `objective_digest` | `(meeting_purpose) -> str` (12 hex) | |
| `canonical` | `(text) -> str` | lowercase + whitespace collapse |
| `similarity` | `(left, right) -> float` | token containment, 0..1 |
| `is_near_duplicate` | `(left, right, threshold=0.72) -> bool` | |
| `request_signature` | `(prompt) -> str` (32 hex) | stable key over the token set |
| `NEAR_DUPLICATE_THRESHOLD` | `0.72` | see the calibration below |
| `STOPWORDS` | `frozenset[str]` | words with no discriminating power |

### Invalidation

| Name | Signature / value | Purpose |
|---|---|---|
| `check_reuse` | `(stored_evidence, new_items, rules=None) -> Verdict` | the reuse decision |
| `detect_cross_set_conflicts` | `(old_items, new_items, rules=None) -> list[dict]` | the raw conflicts |
| `Verdict` | frozen dataclass: `reusable`, `conflicts`, `.reason` | |
| `Conflict` | frozen dataclass with `.as_dict()` | |
| `CONFLICT_RULES` | `tuple[(name, group_a, group_b), ...]` | the shared rule table |
| `text_of` / `source_of` | `(item) -> str` | duck-typed field readers |

### Matching

| Name | Signature / value | Purpose |
|---|---|---|
| `match_word` | `(text, keyword) -> bool` | whole-word, inflection-aware |
| `matched_keyword` | `(text, keywords) -> str \| None` | first hit, for attribution |

---

## Worked example: a complete request path

Rewritten from the integration above, with invalidation and logging in place.

```python
import httpx
from bifrost_cache_policy import build_cache_headers, cache_key_to_clear, check_reuse, decide

BIFROST_URL = "http://localhost:8080"


def brief(company_id: str, purpose: str, facts: list[str], stored: dict | None) -> dict:
    # 1. May a previously stored answer be reused at all?
    if stored is not None:
        verdict = check_reuse(stored.get("evidence"), facts)
        if not verdict.reusable:
            log.info(
                "cache refused | company=%s | reason=%s | conflicts=%d",
                company_id, verdict.reason, len(verdict.conflicts),
            )
            # The rules said no: drop the entry so the next caller cannot hit it.
            httpx.delete(
                f"{BIFROST_URL}/api/cache/clear-by-key/"
                f"{cache_key_to_clear(decide(company_id, purpose, facts))}",
                timeout=30,
            )
            stored = None

    # 2. Build the headers. The TTL is graded from the evidence, never passed in.
    headers = build_cache_headers(company_id, purpose, facts)

    response = httpx.post(
        f"{BIFROST_URL}/v1/chat/completions",
        headers={"Content-Type": "application/json", **headers},
        json={"model": "deepseek/deepseek-chat", "messages": build_messages(facts)},
        timeout=90,
    )

    # 3. Record what the gateway actually did.
    log.info(
        "inference | company=%s | cacheHit=%s | class=%s | ttl=%s",
        company_id,
        response.headers.get("x-bifrost-cache-hit"),
        headers["x-bf-content-class"],
        headers["x-bf-cache-ttl"],
    )
    return response.json()
```

Note the ordering: **invalidation is checked before the key is built.** Reusing
a contradicted answer must fail even when the fresh evidence happens to hash to
the same key. `check_reuse` is the guard that a fingerprint structurally cannot
be, and it costs microseconds (measured below).

The block above is executable, not illustrative: `demo/worked_example.py` runs it
against a stub client and asserts the documented behaviour (cold miss → hit →
refusal with a clear-by-key → second miss). `tests/test_readme_contract.py`
runs it again, and additionally fails if this document drifts from the code —
an export that is undocumented, an API name that does not exist, a
table-of-contents link that resolves nowhere, or a TTL / rule table in prose
that disagrees with the constants. Documentation drift is a red test here,
not something a reader discovers six months later.

```bash
python demo/worked_example.py
# LOG: inference | company=acme-001 | cacheHit=false | class=volatile | ttl=24h
# LOG: inference | company=acme-001 | cacheHit=true  | class=volatile | ttl=24h
# LOG: cache refused | company=acme-001 | reason=1 fact(s) overturn the stored reading | conflicts=1
# LOG: inference | company=acme-001 | cacheHit=false | class=volatile | ttl=24h
# DELETE: .../api/cache/clear-by-key/acme-001_45f29a8081bc_3356f4403fb53b55
```

---

## Measured behaviour

All figures produced on the machine that wrote this document, Python 3.15,
warm cache, `timeit` averages. They are orders of magnitude, not promises.

| Operation | Input size | Cost |
|---|---|---|
| `build_cache_headers` | 60 fact texts | **34 µs** |
| `is_near_duplicate` | one prompt pair | **8.7 µs** |
| `check_reuse` | 10 × 10 items | 2.0 ms |
| `check_reuse` | 30 × 30 items | 18 ms |
| `check_reuse` | 136 × 136 items | 375 ms |

The invalidation check is pairwise and therefore quadratic — roughly **20 µs per
pair**. A 30-fact set against a 30-fact set is 18 ms; a 136-fact set against a
136-fact set is 375 ms, which is real money if it sits on a request path. Two
consequences worth designing for:

- Run `check_reuse` **only on a would-be cache hit**, never on a cold request.
  It is a safety check on reuse, not part of building a response.
- If your evidence sets are large, pre-filter candidate pairs (by shared
  company, date bucket, or a Bloom filter over the rule keywords) before calling
  it. The function is honest about being `O(old × new)`; nothing here hides that.

### Near-duplicate threshold calibration

`NEAR_DUPLICATE_THRESHOLD = 0.72`, chosen between measured cases:

| Pair | Containment | Jaccard | Verdict at 0.72 |
|---|---|---|---|
| same request, words reordered | **0.833** | 0.714 | duplicate ✅ |
| same objective, lightly rephrased | **1.000** | 0.833 | duplicate ✅ |
| different objective, same company | 0.500 | 0.250 | distinct ✅ |
| different company, same objective | 0.667 | 0.400 | distinct ✅ |

Both metrics separate the cases, but containment is used because **Jaccard scores
a pure reordering at 0.714 — a false miss under the same threshold** (0.72).
Containment (the smaller token set's overlap ratio) treats "the same question,
worded longer" as the same question, which is what a cache partition should do.

---

## Running the live gateway probe

The 6 `live` cases are skipped unless `BIFROST_URL` is set, so CI stays offline.
To run them against a throwaway gateway:

```powershell
docker network create bc-probe-net

docker run -d --name bc-probe-redis --network bc-probe-net `
  -e REDIS_ARGS="--save '' --appendonly no" `
  redis/redis-stack-server:7.4.0-v0

docker run -d --name bc-probe-bifrost --network bc-probe-net -p 8089:8080 `
  -v "$PWD/demo/probe-config.json:/app/data/config.json:ro" `
  maximhq/bifrost:v2.4.0

$env:BIFROST_URL   = "http://localhost:8089"
$env:BIFROST_MODEL = "deepseek/deepseek-chat"
python -m pytest tests -m live -v
```

`demo/probe-config.json` is exactly the config used for the probe run: stock
image, Redis Stack vector store, direct-only `semantic_cache`, identity scoping
disabled so hits are observable across the probe's requests.

What the run establishes, and nothing more:

| Assertion | Result |
|---|---|
| first request on a fresh key | `x-bifrost-cache-hit: false` |
| identical second request | `x-bifrost-cache-hit: true` |
| key rebuilt from a different evidence set | `false` — **the key really is evidence-derived** |
| key rebuilt from a different objective | `false` |
| no key sent | header absent — the request bypasses the cache |
| `DELETE /api/cache/clear-by-key/{key}` | **403** on this container |

The clear call is auth-gated and was not unlocked with the default credential,
so the test asserts the key *shape* (200 / 401 / 403) rather than proving
removal. Removal on a deployment with the right credential is Bifrost's
documented behaviour — it is not claimed here as measured.

Two traps baked into that command sequence, both hit while writing it:

- The Redis host in `probe-config.json` is the **container name**
  (`bc-probe-redis`). Giving the Bifrost container a `redis-bifrost` network
  alias makes it resolve that name to **itself**, and the vector store connection
  fails at startup with `fatal: failed to connect to vector store`.
- `redis-stack-server` needs `-e REDIS_ARGS="--save '' --appendonly no"`.
  Passing `--save ''` as an image argument is read as an executable and the
  container refuses to start.

---

## Testing

```bash
pip install -e bifrost-cache-policy
pip install pytest
python -m pytest tests -v                # 68 passed, 6 skipped
python -m pytest tests -m live -v        # 6 passed (needs BIFROST_URL)
python demo/worked_example.py            # the README's worked example still runs
```

74 cases, **zero of them invented**. Every offline case is either a shape
measured on real press text, or a live-run regression:

| File | Cases | Covers |
|---|---|---|
| `test_ttl.py` | 15 | precedence, most-perishable-member, the whole-word traps (`third quarter` / `shareholders`), fail-closed fallbacks, caller-supplied vocabulary |
| `test_keys.py` | 17 | order / case / whitespace insensitivity, empty evidence, evidence and objective digests, containment threshold and its override, signature stability across punctuation |
| `test_headers.py` | 15 | the three-header contract, header- and URL-safe keys, Bifrost duration format (`^\d+h$`), value types, decision object, custom vocabulary |
| `test_invalidate.py` | 17 | every rule fires, both-sides attribution, single-sentence rule, identical-text rule, duck-typed items, bare strings, injected rule table, absent-evidence refusal |
| `test_readme_contract.py` | 4 | this README against the code: exports documented, API names real, TOC anchors resolve, prose TTL / rule tables matching the constants, worked example still runs |
| `test_live_gateway.py` | 6 | against a running gateway — the table in the section above |

The suite deliberately tests the **rules**, not an application around them:
there is no fixture that builds a briefing, no database, no mock LLM. If you want
to see what this package promises without reading any of it, run the tests.

---

## Design decisions

Recorded because a reviewer will ask, and "it seemed reasonable" is not an answer.

**Why a client-side package instead of a Bifrost plugin.**
Native plugins are Go `.so` files requiring the gateway's exact Go version and
architecture, with no cross-compilation. Shipping one means shipping a build
matrix, and it would tie a cache policy to a specific image digest. The three
rules need nothing from inside the gateway: they read the evidence, produce two
headers, and read the verdict back. That is a client-side computation, so it is
published as one.

**Why direct (hash) matching rather than semantic.**
Semantic matching needs an embedding provider and pays an embedding call on
**every direct miss**, hit or miss — on a news-grounded pipeline where each run
assembles a fresh fact set, nearly every request is a miss, so the embedding
cost is paid nearly every time for a benefit that rarely materialises. Worded
differently, the request *is* different: the facts changed. What actually needs
forgiving is trivial rewording of the same question, and that is answered in
microseconds by `is_near_duplicate` on the client, before a key is built.

**Why the TTL is graded rather than configured.**
A configured TTL is one number chosen for the whole deployment, which means it is
wrong for most requests. The class is a property of the evidence that is already
in hand; computing it is a keyword scan, not a model call. Sending it per request
converts the gateway's single global default into a correct per-request value.

**Why a fingerprint cannot replace invalidation.**
A fingerprint answers "is the evidence the same". Invalidation answers "may the
answer still be true". They are different questions, and the second is not
derivable from the first: stored and fresh fact sets can hash differently while
contradicting each other, or hash identically on the facts that matter while one
new sentence overturns the conclusion. Hence two mechanisms, both deterministic.

**Why conflict detection is keyword rules, not a model.**
The check runs on the request path and must be explainable. A model call would be
neither cheap nor auditable, and a semantic judgement over evidence is exactly
what this pipeline forbids elsewhere. Four rule families with whole-word matching
are narrow, and narrowness is the point: every conflict it reports can be read
and verified by a human in one line. The false-positive class that matters — a
single sentence describing a turn ("cut jobs in Q2 but resumed hiring in Q3") —
is excluded by construction rather than by tuning.

**Why `dimension: 1` is called out so loudly.**
`dimension: 1` is overloaded in Bifrost: alone it means direct hash mode, and
with a `provider` it means semantic mode over a one-number embedding, whose
similarity test is effectively random. One line of config separates a working
exact cache from a cache that returns unrelated answers. A README that does not
say this will cost its reader a day.

**Why no dependencies.**
The package sits on the request path of someone else's service. Pulling in an
HTTP client or a validation library to compute a SHA-256 over sorted strings
would be indefensible. `httpx` appears only in the optional `live` extra.

---

## Scope and limits

Stated plainly, in the same spirit as the calibration tables above.

- **Direct (hash) matching only.** No embedding provider, no semantic lookup.
  Cross-wording similarity is `is_near_duplicate`'s job, applied by the caller.
  A gateway cannot run a client-side token test, and this package does not
  pretend otherwise.
- **`x-bf-content-class` is observability, not control.** Bifrost reads the key
  and the TTL. The class label exists so a log or dashboard can show *why* a TTL
  is what it is. Sending it changes nothing about caching.
- **The conflict rules are English-language.** Four families, whole-word
  matching. A deployment in another language must supply its own `rules` table
  and its own `CLASS_KEYWORDS`; neither will transfer.
- **The conflict scan is `O(old × new)`.** 20 µs per pair, 375 ms at 136 × 136.
  Run it on would-be hits only, or pre-filter pairs. See
  [Measured behaviour](#measured-behaviour).
- **Invalidation decisions are not enforced by the gateway.** `check_reuse` tells
  *you* not to reuse. Whether the entry is actually dropped depends on your
  deployment's credential for the management endpoint.
- **Identity scoping is the operator's decision.** The demo config sets
  `disable_identity_scoping: true` so a probe's hits are observable. A
  multi-tenant deployment should leave it off and let each virtual key keep its
  own entries.
- **Not published to an index.** Install from the repository. Version `0.1.0`
  means the API is stable enough to review, not frozen.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No cache headers on any response | the request carried no `x-bf-cache-key` | send the headers; caching is opt-in per request |
| `x-bifrost-cache-hit` is always `false` | `semantic_cache` not enabled, or no vector store | enable both in the live config, not just in `config.json` |
| Config edits appear to do nothing | `config_store` enabled — the file only seeds the store | edit the plugin row via `/api/plugins` or set `source_of_truth: "config.json"` |
| Gateway exits with `failed to connect to vector store` | Redis hostname resolves to the gateway itself | use the store's real container name, drop the self-referential network alias |
| Cache returns an unrelated answer | `dimension: 1` **with** a `provider` — semantic mode over one number | remove `provider` and `embedding_model` entirely |
| The same evidence produces different keys | the evidence list is being hashed unsorted by a hand-rolled key | use `evidence_digest`, which sorts |
| `clear-by-key` returns 403 | management endpoints are auth-gated | configure the admin credential; it is not a package problem |
| A filing got a 24-hour TTL | substring matching in a custom classifier (`third quarter` → `hir`) | use `classify_fact_texts`; it matches whole words |
| `redis-stack-server` will not start | `--save ''` passed as image args | pass `-e REDIS_ARGS="..."` |

---

## License and provenance

This package was extracted from the BriefCortex pre-meeting briefing pipeline,
where the three rules were written and tested against live news coverage before
anything reused them. It is vendored in that repository under
`bifrost-cache-policy/` and depends on nothing from it: no import of the
application, no `FactBase`, no database, no settings object. The extraction is
the deliverable — a cache policy is useful to any gateway deployment, and only
one of those deployments needs a briefing pipeline attached.

MIT, per the [`LICENSE`](LICENSE) in this directory.

