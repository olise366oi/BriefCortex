# bifrost-cache-policy

**Evidence-derived cache keys, graded TTLs and cross-set invalidation for the
[Bifrost](https://github.com/maximhq/bifrost) AI gateway.**

Any plain Bifrost install gains these rules by calling one function before the
request leaves your process. Nothing here needs a modified Bifrost binary, a
compiled `.so`, an embedding provider, or a model call.

```bash
pip install -e bifrost-cache-policy        # from a checkout of this repository
```

Requires Python 3.10+. No runtime dependencies; `httpx` is only needed for the
live gateway probe. Not published to an index — install it from the repo.

---

## The problem this solves

A gateway decides *whether to replay a response* before inference happens. That
places three constraints on any caching rule worth installing:

| Constraint | Why it matters | What Bifrost gives you |
|---|---|---|
| It must be cheap | the decision runs on every request | a key you supply, plus a TTL override |
| It must know the evidence | a digest of the prompt is the wrong unit — two briefs can share a template and differ in every fact | a per-request cache key |
| It must be able to say *no* to a stored answer | all facts can be individually true and still be overturned by newer ones | a clear-by-key endpoint |

Bifrost ships `semantic_cache`, which covers the storage and the replay. What it
does not ship is the *policy*: it has one global TTL, and its similarity test
needs embeddings. This package is that policy, as data plus one function.

Concretely, three operational failures in a news-grounded pipeline:

1. **Stale answers with a correct key.** The same company, the same objective,
   a refreshed fact base: the prompt digest is identical only if the facts are
   the same, and a naive key hashes whatever order the retriever returned.
   → `build_cache_key` digests the **canonicalised, sorted** evidence.
2. **One TTL for everything.** A brief built from same-day market coverage and
   one built from a filing have completely different shelf lives; a single
   global TTL either expires filings early or serves a price move for a week.
   → `classify_fact_texts` grades the TTL from the evidence, sent per request as
   `x-bf-cache-ttl`.
3. **A cache that cannot see a contradiction.** A brief written while the
   company was hiring is wrong the moment a layoff is reported, and the
   fingerprint cannot tell — every fact in it was true when written.
   → `check_reuse` refuses the hit; `cache_key_to_clear` removes the entry.

---

## The three rules

### 1. TTL is a property of the evidence

| Class | Signals | TTL |
|---|---|---|
| `volatile` | earnings, guidance, stock moves, recalls, lawsuits | `24h` |
| `structural` | hiring, appointments, product launches | `72h` |
| `primary` | annual reports, filings, press releases | `168h` |

A fact set is only as cacheable as its most perishable member, so precedence
runs `volatile` → `structural` → `primary`. Unclassified evidence falls back to
the **shortest** TTL: fail closed, never keep something for a week because
nobody recognised it.

Matching is whole-word, with regular inflections. This is not cosmetic —
`"third quarter"` contains `"hir"`, and `"shareholders"` contains `"shares"`.
Both were measured on live press text; substring matching downgrades a filing to
a 24-hour entry in one case and mislabels a filing class as volatile in the
other.

### 2. The key identifies the evidence, not the run

```python
from bifrost_cache_policy import build_cache_headers

headers = build_cache_headers(
    partition="nio-abc",              # company, tenant, feature — whatever scopes your cache
    meeting_purpose="reduce price by 15%",
    fact_texts=["NIO reported Q3 earnings and cut guidance.", ...],
)
# {
#   "x-bf-cache-key":     "nio-abc_<objective-digest>_<evidence-digest>",
#   "x-bf-cache-ttl":     "24h",
#   "x-bf-content-class": "volatile",
# }
```

The evidence digest sorts and canonicalises the fact texts, so a re-run whose
lanes finished in a different order still hits. The objective digest is
separate, so a different objective on identical evidence is a different brief.

### 3. A contradiction invalidates the stored answer

```python
from bifrost_cache_policy import check_reuse

verdict = check_reuse(stored_evidence, fresh_facts)
if not verdict.reusable:
    ...   # regenerate; optionally clear the entry by key
```

Cross-item only: one sentence stating both directions ("cut jobs in Q2 but
resumed hiring in Q3") is a description of a turn, not a contradiction. Absent
stored evidence is **not** reusable — an unverifiable hit is the failure this
exists to prevent. Items are duck-typed (objects with `.content`, mappings, or
bare strings), so the rule does not need to know your evidence model.

---

## The Bifrost extension points this depends on

All documented, all present in a stock install:

| Extension point | Used for | Consequence if absent |
|---|---|---|
| `plugins[].semantic_cache` with `dimension: 1` and no `provider` | exact (hash) cache storage and replay | no cache at all |
| `vector_store` (Redis / Valkey / Qdrant / Weaviate / Pinecone) | where entries live — required even in direct mode | the cache plugin cannot load |
| `x-bf-cache-key` request header | the partition this package derives from the evidence | request bypasses the cache |
| `x-bf-cache-ttl` request header | the graded TTL, per request | every entry gets the one configured default |
| `x-bifrost-cache-hit` / `-cache-id` response headers | the verdict, read back for logs and for invalidation | cache behaviour is unobservable |
| `DELETE /api/cache/clear-by-key/{key}` | dropping an entry once `check_reuse` refuses it | a contradicted answer stays until it expires |

Nothing here needs a custom `.so`, a dynamically linked Bifrost binary, or a
rebuild — those paths require the same Go version and architecture as the
gateway and cannot be shipped as a pip-installable policy.

---

## Installing it on a stock gateway

### 1. Enable direct (hash) caching

The rules decide *what* may be cached and for how long; Bifrost does the
storing. Direct mode needs no embedding provider:

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

`dimension: 1` **and** no `provider` means hash mode. (Recall the documented
trap: `dimension: 1` *with* a provider is read as semantic mode with a
one-dimensional embedding, whose similarity test is close to random — that is
the configuration this package exists to avoid.) `ttl` here is only a default;
every value this package sends overrides it per request.

A vector store is required even in direct mode — that is where entries live.

One deployment note that costs an afternoon if unknown: with a `config_store`
enabled (SQLite/Postgres), `config.json` is a **seed and drift detector**, not the
live config. Editing the file does not override a `semantic_cache` row that
already exists in the store; change that row through the UI or the
`/api/plugins` API. `source_of_truth: "config.json"` makes the file authoritative
if you would rather drive it from git.

### 2. Send the headers with every request

```python
import httpx
from bifrost_cache_policy import build_cache_headers

response = httpx.post(
    f"{BIFROST_URL}/v1/chat/completions",
    headers={"Content-Type": "application/json", **build_cache_headers(...)},
    json={"model": "deepseek/deepseek-chat", "messages": [...]},
)
response.headers.get("x-bifrost-cache-hit")   # "true" / "false"
```

Caching is opt-in per request: with no `x-bf-cache-key` (and no
`default_cache_key` configured), the request bypasses the cache entirely. That is
the safe default — a missed header costs money, never correctness.

### 3. Read the verdict back, and clear when the rules say so

Every looked-up request carries `x-bifrost-cache-hit`, `x-bifrost-cache-id` and
`x-bifrost-cache-hit-type`. To drop an entry once `check_reuse` refuses it:

```python
httpx.delete(f"{BIFROST_URL}/api/cache/clear-by-key/{decision.cache_key}")
```

Management endpoints are auth-gated. On the stock container used for the probe
this endpoint answered 403 without an admin credential, so treat clearing as a
deployment capability you enable rather than something that always works.

> **What the live probe actually showed** against a stock
> `maximhq/bifrost:v2.4.0` container with the config above: `x-bifrost-cache-hit`
> `false` on the first request, `true` on an identical second request, `false`
> again after changing the evidence set or the objective, and absent entirely when
> no key was sent. The `DELETE /api/cache/clear-by-key/{key}` call answered **403**
> on that container — management endpoints are auth-gated, and basic auth with
> the default credential did not unlock it either — so the test asserts the key
> shape (200/401/403) rather than proving removal. Clearing an entry on a
> deployment with the right admin credential is documented by Bifrost, not
> claimed here as measured. See `demo/` and `tests/test_live_gateway.py`.

---

## Demo

`demo/probe-config.json` is the config used for the live probe: a stock
`maximhq/bifrost:v2.4.0` container, a Redis Stack vector store, direct-only
`semantic_cache`.

```powershell
docker network create bc-probe-net
docker run -d --name bc-probe-redis --network bc-probe-net `
  -e REDIS_ARGS="--save '' --appendonly no" redis/redis-stack-server:7.4.0-v0
docker run -d --name bc-probe-bifrost --network bc-probe-net -p 8089:8080 `
  -v "$PWD/demo/probe-config.json:/app/data/config.json:ro" `
  maximhq/bifrost:v2.4.0

$env:BIFROST_URL   = "http://localhost:8089"
$env:BIFROST_MODEL = "deepseek/deepseek-chat"
python -m pytest tests -m live -v
```

Note that the Redis host in `probe-config.json` is the container name
(`bc-probe-redis`): giving the Bifrost container a `redis-bifrost` network alias
makes it resolve that name to itself and fail to connect.

---

## What is in the tests

`tests/` — 70 cases: 64 run offline by default, 6 are marked `live` and skipped
unless `BIFROST_URL` is set:

| File | Covers |
|---|---|
| `test_ttl.py` | class precedence, most-perishable-member, whole-word traps, fallbacks, caller-supplied vocabulary |
| `test_keys.py` | order/case/whitespace insensitivity, evidence and objective digests, near-duplicate threshold and signature |
| `test_headers.py` | the three-header contract, header-safe keys, Bifrost duration format, decision object |
| `test_invalidate.py` | every conflict rule fires, both-sides attribution, single-sentence rule, duck-typed items, absent-evidence refusal |
| `test_live_gateway.py` | against a running gateway: first miss → second hit, refreshed evidence misses, changed objective misses, no-key bypasses, clear-by-key drops the entry |

Every offline case is a shape measured on real text or a live run, not an
invented one. The live cases are skipped unless `BIFROST_URL` is set, so CI
stays offline.

---

## Scope and limits

- **Direct (hash) matching only.** This package provides no embedding provider.
  Semantic similarity across differently-worded requests is `is_near_duplicate`'s
  job, applied by the caller before the key is built — the gateway cannot run a
  client-side token test.
- **`x-bf-content-class` is observability, not control.** Bifrost reads the key
  and the TTL; the class label exists so a log or dashboard can show *why* a TTL
  was what it was.
- **The conflict rules are English-language keyword rules.** They are
  deliberately narrow and deterministic: four rule families, whole-word
  matching. A pipeline covering other languages must supply its own `rules` table
  (`detect_cross_set_conflicts(..., rules=...)`) rather than expect these to
  transfer.
- **Identity scoping is the operator's decision.** The demo config sets
  `disable_identity_scoping: true` so the probe's cache hits are observable;
  a multi-tenant deployment should leave it off and let each virtual key keep its
  own entries.
