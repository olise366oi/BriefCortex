"""The article subject must be the real headline, not a routing label.

A live run crashed here on a missing import, which the suite missed because no
test exercised the headline path. This covers it directly.
"""
from __future__ import annotations

from app.services.incidental_harvest import _article_subject

ARTICLE = "Zocdoc announced a partnership with Instinct to book appointments."


def test_the_headline_is_used_as_the_subject():
    subject = _article_subject(
        {"title": "Zocdoc brings booking to Instinct - Reuters"}, ARTICLE
    )

    assert subject == "Zocdoc brings booking to Instinct"


def test_the_outlet_suffix_is_stripped():
    for title in (
        "Instinct raises $1B — TechCrunch",
        "Instinct raises $1B | Reuters",
        "Instinct raises $1B - Bloomberg",
    ):
        assert _article_subject({"title": title}, ARTICLE) == "Instinct raises $1B"


def test_a_title_without_an_outlet_is_kept_whole():
    subject = _article_subject({"title": "NVIDIA reports record revenue"}, ARTICLE)

    assert subject == "NVIDIA reports record revenue"


def test_a_missing_title_falls_back_to_the_opening_sentence():
    subject = _article_subject({}, ARTICLE)

    assert subject
    assert "Zocdoc" in subject


def test_an_empty_result_does_not_crash():
    assert _article_subject({}, "") == ""


def test_the_routing_label_is_never_returned():
    # The old code stored "primary" or "passing mention" here, which told a
    # reader nothing about whose article the sentence came from.
    subject = _article_subject({"title": "Zocdoc and Instinct"}, ARTICLE)

    assert subject not in ("primary", "passing mention")
