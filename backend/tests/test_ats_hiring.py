"""An employer's own board must reach the hiring lane, not the fact base.

Measured failure: a brief for Canada Goose reported `hiring=0` while the company's
own Workday board listed 96 roles. Two causes, both fixed here:

1. `myworkdayjobs.com` was on the refused-host list. It reads like a job board, but
   `canadagoose.wd3.myworkdayjobs.com` **is** Canada Goose's careers site — the
   company's own openings, published by the company, reached through a vendor's
   front end.
2. The board's HTML carries no markup at all (7,446 bytes, zero JobPosting blocks),
   so the schema.org reader found nothing; Workday's own JSON listing answers with
   all 96.
"""
from __future__ import annotations

import json

import pytest

from app.services import job_postings
from app.services.hiring import is_careers_url
from app.services.media_trust import is_refused_host
from app.services.job_postings import (
    _postings_from_workday,
    _workday_cx_endpoint,
    postings_from_url,
)

WORKDAY_URLS = (
    "https://canadagoose.wd3.myworkdayjobs.com/CanadaGooseCareers",
    "https://canadagoose.wd3.myworkdayjobs.com/en-US/CanadaGooseCareers",
    "https://canadagoose.wd3.myworkdayjobs.com/CanadaGooseCareers/job/Denver",
)

ATS_URLS = (
    "https://jobs.lever.co/acme",
    "https://boards.greenhouse.io/acme",
    "https://careers.smartrecruiters.com/AcmeInc1",
    "https://acme.icims.com/jobs",
)

AGGREGATOR_URLS = (
    "https://ca.indeed.com/viewjob?jk=1",
    "https://www.eluta.ca/jobs-at-canada-goose",
    "https://www.glassdoor.com/Jobs/Acme.htm",
)


# --- classification ------------------------------------------------------

@pytest.mark.parametrize("url", WORKDAY_URLS + ATS_URLS)
def test_an_employers_own_board_is_a_careers_page(url):
    assert is_careers_url(url) is True


@pytest.mark.parametrize("url", WORKDAY_URLS + ATS_URLS)
def test_an_employers_own_board_is_not_refused(url):
    assert is_refused_host(url) is False


@pytest.mark.parametrize("url", AGGREGATOR_URLS)
def test_a_third_party_aggregator_is_still_refused(url):
    # Removing the vendor hosts must not re-admit the aggregators they were
    # confused with.
    assert is_refused_host(url) is True


# --- the listing endpoint ------------------------------------------------

@pytest.mark.parametrize(
    "url",
    WORKDAY_URLS,
)
def test_the_cx_endpoint_names_the_tenant_and_the_site(url):
    endpoint = _workday_cx_endpoint(url)

    assert endpoint is not None
    assert endpoint.endswith("/wday/cxs/canadagoose/CanadaGooseCareers/jobs")


def test_a_locale_prefix_does_not_become_the_site_name():
    # "/en-US/CanadaGooseCareers" parses as site=en-US, path=CanadaGooseCareers;
    # sending "en-US" as the site returns 502 from Workday.
    endpoint = _workday_cx_endpoint(
        "https://canadagoose.wd3.myworkdayjobs.com/en-US/CanadaGooseCareers"
    )

    assert "en-US" not in endpoint


def test_a_non_workday_url_has_no_cx_endpoint():
    assert _workday_cx_endpoint("https://www.canadagoose.com/en-us/pages/careers") is None


# --- reading the board ---------------------------------------------------

def _patch_workday(monkeypatch, payload: dict):
    class _Response:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        job_postings.urllib.request,
        "urlopen",
        lambda *_a, **_k: _Response(json.dumps(payload).encode()),
    )


def test_postings_are_read_from_the_json_listing(monkeypatch):
    _patch_workday(
        monkeypatch,
        {
            "total": 96,
            "jobPostings": [
                {
                    "title": "QC In Line Inspector",
                    "locationsText": "Winnipeg, Manitoba, CAN",
                },
                {"title": "Lead, Operations", "locationsText": "Banff, Alberta, CAN"},
            ],
        },
    )

    postings = _postings_from_workday("https://x/wday/cxs/x/y/jobs")

    assert len(postings) == 2
    assert postings[0].title == "QC In Line Inspector"
    assert postings[0].location == "Winnipeg, Manitoba, CAN"


def test_a_row_without_a_title_is_skipped(monkeypatch):
    _patch_workday(
        monkeypatch,
        {"jobPostings": [{"locationsText": "nowhere"}, {"title": "Analyst"}]},
    )

    postings = _postings_from_workday("https://x/wday/cxs/x/y/jobs")

    assert [p.title for p in postings] == ["Analyst"]


def test_an_unreadable_board_yields_nothing(monkeypatch):
    def _boom(*_a, **_k):
        raise OSError("no network")

    monkeypatch.setattr(job_postings.urllib.request, "urlopen", _boom)

    assert _postings_from_workday("https://x/wday/cxs/x/y/jobs") == []


def test_postings_from_url_prefers_the_workday_listing(monkeypatch):
    # The HTML reader must not be reached when the JSON listing answers.
    calls: list[str] = []

    monkeypatch.setattr(
        job_postings,
        "_postings_from_workday",
        lambda endpoint: [
            job_postings.JobPosting(
                title="Read from the board", description="", location=None,
                employment_type=None, posted_date=None,
            )
        ],
    )
    monkeypatch.setattr(
        job_postings, "fetch_page", lambda _url: calls.append("html") or ""
    )

    postings = postings_from_url(WORKDAY_URLS[0])

    assert [p.title for p in postings] == ["Read from the board"]
    assert calls == [], "the page HTML should not be fetched at all"


def test_a_page_that_delegates_to_a_board_is_followed(monkeypatch):
    # Measured: canadagoose.com/en-us/pages/careers is a Shopify page whose only
    # job links are two absolute Workday URLs and whose own text lists no roles.
    # The board behind that link carried 96.
    store_html = (
        "<html><body>"
        '<a href="https://canadagoose.wd3.myworkdayjobs.com/CanadaGooseCareers">'
        "Search jobs</a>"
        '<a href="https://canadagoose.wd3.myworkdayjobs.com/CanadaGooseCareers">'
        "Careers</a>"
        "</body></html>"
    )

    monkeypatch.setattr(job_postings, "fetch_page", lambda _url: store_html)
    monkeypatch.setattr(
        job_postings,
        "_postings_from_workday",
        lambda _endpoint: [
            job_postings.JobPosting(
                title="QC In Line Inspector", description="",
                location="Winnipeg, Manitoba, CAN",
                employment_type=None, posted_date=None,
            )
        ],
    )

    postings = postings_from_url("https://www.canadagoose.com/en-us/pages/careers")

    assert [p.title for p in postings] == ["QC In Line Inspector"]


def test_a_board_link_is_not_mistaken_for_a_detail_page(monkeypatch):
    # The Shopify stylesheet and a WhatsApp `wa.me` link were the two "detail pages"
    # the old reader found on that page; neither is a job.
    store_html = (
        '<html><body><a href="https://cdn.shopify.com/extensions/x/app.css">css</a>'
        '<a href="https://wa.me/18339974728">chat</a></body></html>'
    )

    monkeypatch.setattr(job_postings, "fetch_page", lambda _url: store_html)
    monkeypatch.setattr(job_postings, "_detail_links", lambda *_a: [])

    assert postings_from_url("https://www.canadagoose.com/en-us/pages/careers") == []
