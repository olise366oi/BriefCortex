"""Prompts that are rendered with .format() must not carry bare JSON braces.

Measured failure: LOCAL_QUERY_PROMPT ended with {"local_name": ...}, so calling
.format(language_name=...) raised KeyError('local_name'), the local query lane
never ran, and the only symptom was "Local query generation failed" in the log.
"""
from __future__ import annotations

import pytest

from app.services.media_trust import (
    LANGUAGE_NAMES,
    LOCAL_QUERY_PROMPT,
    MEDIA_LIST_PROMPT,
    LANGUAGE_ONLY_PROMPT,
    _language_name,
)


def test_local_query_prompt_renders_without_a_key_error():
    rendered = LOCAL_QUERY_PROMPT.format(language_name="Japanese", max_queries=3)

    assert "Japanese" in rendered
    assert "3" in rendered
    # The JSON shape survives, with single braces after substitution.
    assert '{"local_name": str, "queries": [str]}' in rendered


def test_local_query_prompt_is_not_rendered_elsewhere():
    # The media list and language prompts are sent as-is, so braces are safe there.
    assert "{" in MEDIA_LIST_PROMPT
    assert "{" in LANGUAGE_ONLY_PROMPT


def test_language_names_cover_the_major_briefing_markets():
    for code in ("ja", "zh", "de", "fr", "es", "it", "nl", "ko", "en"):
        assert code in LANGUAGE_NAMES, code


def test_an_unknown_language_falls_back_to_its_code():
    assert _language_name("xx") == "xx"
    assert _language_name("") == "the local language"


@pytest.mark.parametrize(
    "prompt_name,needs_format",
    [
        ("LOCAL_QUERY_PROMPT", True),
        ("MEDIA_LIST_PROMPT", False),
        ("LANGUAGE_ONLY_PROMPT", False),
    ],
)
def test_only_the_format_rendered_prompt_escapes_its_braces(prompt_name, needs_format):
    import app.services.media_trust as media

    text = getattr(media, prompt_name)
    if needs_format:
        # Rendered prompts must escape literal braces.
        assert '"local_name"' in text
        assert '{{"local_name"' in text
    else:
        # Unrendered prompts must keep single braces.
        assert "{{" not in text
