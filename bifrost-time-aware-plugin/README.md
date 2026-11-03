# Bifrost Time-Aware Plugin (Content-Aware TTL)

## The Problem

Bifrost supports TTL (time-to-live) caching headers, but its caching logic is
static. It cannot tell whether the evidence behind a request is same-day market
material (which decays in hours) or a filing (which does not move at all). A
static TTL therefore either pays for fresh tokens it does not need, or serves
stale evidence.

## The Solution

`backend/app/services/bifrost_headers.py` classifies the evidence in the prompt
and injects a content-aware TTL plus a cache key into the LLM request headers.

### Behaviour

Each fact text is matched against three word-boundary rule sets. Matching is
whole-word, so `third quarter` is not read as a hiring signal and `shares` does
not fire on `shareholders`.

| Class | Signals | TTL |
|---|---|---|
| `volatile` | earnings, guidance, stock moves, recalls, lawsuits | 24h |
| `structural` | hiring, appointments, product launches | 72h |
| `primary` | annual reports, filings, press releases | 168h |

Classes are applied by precedence: a fact set is only as cacheable as its most
perishable member, so any volatile signal pins the TTL at 24h.

### Cache key

`{company_id}_{purpose_digest}_{evidence_digest}`. The key covers both halves of
the request, so a different meeting objective or a refreshed fact base produces
a different key instead of a stale hit.

### Zero intrusion

The headers (`x-bf-cache-key`, `x-bf-cache-ttl`, `x-bf-content-class`) are
standard HTTP headers attached to the outgoing request, so Bifrost needs no
source changes.
