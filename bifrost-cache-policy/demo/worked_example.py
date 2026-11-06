"""Executes the README's worked example against a stub client.

If the README's code stops matching the real API, this file fails.
"""
from __future__ import annotations

import sys

from bifrost_cache_policy import build_cache_headers, cache_key_to_clear, check_reuse, decide

BIFROST_URL = "http://localhost:8080"


class _Resp:
    def __init__(self, headers):
        self.headers = headers

    def json(self):
        return {"choices": [{"message": {"content": "ok"}}]}


class _Client:
    def __init__(self):
        self.posts = []
        self.deletes = []
        self._seen = set()

    def post(self, url, headers=None, json=None, timeout=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        # Mimic the real gateway: the same key only hits after it was stored
        # under that key, which is what makes a differentiated key observable.
        key = (headers or {}).get("x-bf-cache-key")
        hit = "true" if key in self._seen else "false"
        self._seen.add(key)
        return _Resp({"x-bifrost-cache-hit": hit})

    def delete(self, url, timeout=None):
        self.deletes.append(url)
        self._seen.discard(url.rsplit("/", 1)[-1])
        return _Resp({})


class _Log:
    def __init__(self):
        self.lines = []

    def info(self, fmt, *args):
        self.lines.append(fmt % args)


httpx = _Client()
log = _Log()


def build_messages(facts):
    return [{"role": "user", "content": "\n".join(facts)}]


# --- the README block, verbatim -----------------------------------------

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

# ------------------------------------------------------------------------


def main() -> int:
    facts = ["ACME reported Q3 earnings and cut guidance."]

    brief("acme-001", "reduce price by 15%", facts, stored=None)
    brief("acme-001", "reduce price by 15%", facts, stored={"evidence": {"hard_facts": [
        {"content": "ACME reported Q3 earnings and cut guidance.", "source_url": "https://x/1"}
    ]}})
    # Stored while ACME was hiring; the fresh fact is a layoff. Same key would
    # otherwise replay a brief that says the opposite of the news.
    brief(
        "acme-001",
        "reduce price by 15%",
        ["ACME announced layoffs across engineering."],
        stored={"evidence": {"hard_facts": [
            {"content": "ACME is hiring across engineering.", "source_url": "https://x/2"}
        ]}},
    )

    for line in log.lines:
        print("LOG:", line)
    print("posts:", len(httpx.posts), "deletes:", len(httpx.deletes))
    for url in httpx.deletes:
        print("DELETE:", url)

    assert len(httpx.posts) == 3, len(httpx.posts)
    assert len(httpx.deletes) == 1, httpx.deletes
    assert httpx.posts[0]["headers"]["x-bf-cache-ttl"] == "24h"
    assert httpx.posts[0]["headers"]["x-bf-content-class"] == "volatile"
    assert "x-bf-cache-key" in httpx.posts[0]["headers"]
    print("OK: worked example runs and produces the documented behaviour")
    return 0


if __name__ == "__main__":
    sys.exit(main())
