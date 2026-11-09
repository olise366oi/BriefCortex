"""The company's own careers page is probed when no search result produced roles."""
from __future__ import annotations

import asyncio

from app.services import ingestion
from app.services.job_postings import JobPosting


def _run(coro):
    return asyncio.run(coro)


def _fetched(host: str) -> list[dict]:
    return [{"url": f"https://{host}/news/x", "raw_content": "text"}]


class _Tracker:
    def __init__(self) -> None:
        self.urls: list[str] = []


def test_the_company_host_is_taken_from_results_already_fetched():
    fetched = _fetched("www.canadagoose.com") + _fetched("reuters.com")

    host = ingestion._company_host_from(fetched, "Canada Goose")

    assert host == "canadagoose.com"


def test_an_unrelated_host_is_not_mistaken_for_the_company():
    fetched = _fetched("reuters.com")

    assert ingestion._company_host_from(fetched, "Canada Goose") == ""


def test_the_probe_returns_roles_from_the_first_board_that_answers(monkeypatch):
    tracker = _Tracker()

    def _fake_postings(url: str):
        tracker.urls.append(url)
        if "canadagoose.com/careers" in url:
            return [
                JobPosting(
                    title="QC In Line Inspector", description="",
                    location="Winnipeg, Manitoba, CAN",
                    employment_type=None, posted_date=None,
                )
            ]
        return []

    monkeypatch.setattr(ingestion, "postings_from_url", _fake_postings)
    # postings_from_url is called through asyncio.to_thread, so monkeypatching the
    # module attribute is enough as long as the call site reads it lazily.
    monkeypatch.setattr(
        ingestion, "postings_from_url", _fake_postings, raising=True
    )

    items = _run(
        ingestion._probe_own_careers(
            "Canada Goose", _fetched("www.canadagoose.com"), None
        )
    )

    assert [item.content for item in items][0].startswith("QC In Line Inspector")
    assert tracker.urls, "at least one candidate URL was tried"


def test_the_probe_stops_at_the_first_board_that_answers(monkeypatch):
    tried: list[str] = []

    def _fake_postings(url: str):
        tried.append(url)
        return [
            JobPosting(
                title="Analyst", description="", location=None,
                employment_type=None, posted_date=None,
            )
        ]

    monkeypatch.setattr(ingestion, "postings_from_url", _fake_postings)

    _run(ingestion._probe_own_careers("Canada Goose", _fetched("www.canadagoose.com"), None))

    assert len(tried) == 1, "the first shape answered, the rest must not run"


def test_no_domain_means_no_probe(monkeypatch):
    called: list[str] = []

    def _fake_postings(url: str):
        called.append(url)
        return []

    monkeypatch.setattr(ingestion, "postings_from_url", _fake_postings)

    items = _run(ingestion._probe_own_careers("QQ", [], None))

    assert items == []
    assert called == [], "a name too short to form a domain must not be probed"


def test_an_unreadable_candidate_moves_on_to_the_next_shape(monkeypatch):
    seen: list[str] = []

    def _fake_postings(url: str):
        seen.append(url)
        if len(seen) == 1:
            raise OSError("connection refused")
        return [
            JobPosting(
                title="Read on the second shape", description="", location=None,
                employment_type=None, posted_date=None,
            )
        ]

    monkeypatch.setattr(ingestion, "postings_from_url", _fake_postings)

    items = _run(
        ingestion._probe_own_careers("Acme", _fetched("acme.com"), None)
    )

    assert items
    assert "second shape" in items[0].content
