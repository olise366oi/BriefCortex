"""A headline that names the company settles the article's subject.

Measured on NVIDIA: an article whose headline named the company came back from
the classifier as "comparison" because the body also discussed peers, so its
facts were offered as unconfirmed leads instead of as news.
"""
from __future__ import annotations

from app.services.ingestion import title_names_company


def test_a_headline_naming_the_company_counts():
    result = {"title": "NVIDIA reports record data-centre revenue"}

    assert title_names_company(result, "NVIDIA") is True


def test_a_headline_naming_a_peer_only_does_not_count():
    result = {"title": "AMD takes share from Intel in server chips"}

    assert title_names_company(result, "NVIDIA") is False


def test_a_headline_using_the_legal_name_counts():
    result = {"title": "NVIDIA Corporation announces quarterly results"}

    assert title_names_company(result, "NVIDIA", aliases=["NVIDIA CORPORATION"]) is True


def test_word_boundaries_are_respected():
    # "NVIDIA" must not match inside a longer token.
    result = {"title": "NVIDIAn chip roadmap analysed"}

    assert title_names_company(result, "NVIDIA") is False


def test_an_empty_title_does_not_count():
    assert title_names_company({}, "NVIDIA") is False
    assert title_names_company({"title": "   "}, "NVIDIA") is False


def test_a_headline_naming_a_different_same_name_business_still_counts():
    # The headline rule is about the name, and entity separation happens later
    # in the description filter. Documenting the limit of this check keeps it
    # honest: it is a subject test, not an identity test.
    result = {"title": "Instinct EMR rolls out to veterinary hospitals"}

    assert title_names_company(result, "Instinct") is True
