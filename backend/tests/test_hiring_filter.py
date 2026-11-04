"""Tests for the line-level hiring filter, using lines from a real run."""
from __future__ import annotations

import pytest

from app.services.hiring import is_careers_url, is_hiring_line


# Real roles and their structured fields. These must survive.
ROLE_LINES = (
    "Zydus Lifesciences Limited is inviting eligible candidates for opportunities "
    "at its API Manufacturing Plant, Ankleshwar Unit 2.",
    "**Qualification:** B.Sc. Chemistry or full-time Diploma in Chemical Engineering.",
    "**Experience:** Freshers may apply.",
    "**Requirement:** Experience managing GMP documentation and compliance.",
    "**Job location** Ankleshwar, Gujarat**",
    "Job Opportunities for Scientists: Walk-In Interview at Zydus Lifesciences Limited",
)

# Noise that the page-level filter used to admit (measured: 30 of 30 items,
# 22 of them noise).
NOISE_LINES = (
    "After a week of sharp selling pressure, the Indian stock market staged a strong recovery",
    "According to Vinod Nair, Head of Research at Geojit Investments, US PMI readings, jobs data and the outcome",
    "The Nifty 50 rallied 453 points, or 2 percent, during the week to close at 23,053",
    "This title was summarized by AI from the post below.",
    "📌 Follow @SashPharma for pharma job updates, fresher vacancies, interview questions and career guidance.",
    "#sashpharma #vacancy #hiring #pharma",
    "▶️ YouTube Channel / @knowaboutpharmacy9619",
    "📢 Telegram – Pharma Notes & Updates https://t.me/notesondeMands/1",
    "🔔 Stay Connected with KnowAboutPharmacy",
    "Learn • Grow • Get Hired • Stay Updated",
    "Planning your chemistry career? Your location can shape your opportunities.",
    "⚠️ Important: This is a general hiring-awareness post, not confirmation of current vacancies.",
)


@pytest.mark.parametrize("line", ROLE_LINES)
def test_role_lines_are_kept(line):
    assert is_hiring_line(line) is True


@pytest.mark.parametrize("line", NOISE_LINES)
def test_noise_lines_are_dropped(line):
    assert is_hiring_line(line) is False


def test_market_column_mentioning_jobs_data_is_not_hiring():
    # This is the exact line that used to file a whole markets column as hiring.
    assert is_hiring_line(
        "According to Vinod Nair, Head of Research at Geojit Investments, "
        "US PMI readings, jobs data and the outcome of the US midterm elections"
    ) is False


def test_hashtag_spam_is_dropped():
    assert is_hiring_line(
        "Walk-In Interview for Zydus Lifesciences Limited. #Zydus #Careers #Hiring"
    ) is False


def test_empty_line_is_not_hiring():
    assert is_hiring_line("") is False
    assert is_hiring_line("   ") is False


def test_careers_urls_are_recognized():
    assert is_careers_url("https://careers.zydus.com/jobs") is True
    assert is_careers_url("https://www.zyduslife.com/careers") is True
    assert is_careers_url("https://example.com/vacancies/123") is True


def test_article_urls_are_not_careers_urls():
    assert is_careers_url("https://www.reuters.com/business/zydus-earnings") is False
    assert is_careers_url("https://www.moneycontrol.com/news/market-week-ahead") is False
