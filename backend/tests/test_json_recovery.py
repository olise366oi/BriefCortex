"""Tests for the JSON recovery used when a model reply carries extra text."""
from __future__ import annotations

import json

from app.services.analysis import _json_payload

VALID = {"executive_summary": {"summary": "s", "recommendation": "r", "confidence": "c"}}


def test_a_clean_object_is_returned_unchanged():
    content = json.dumps(VALID)

    assert _json_payload(content) == content


def test_trailing_prose_after_the_object_is_trimmed():
    # Measured failure: the reply ended with a sentence after the closing brace,
    # so the whole response was discarded as invalid JSON.
    content = json.dumps(VALID) + " Those areas that were not checked are clean."

    recovered = _json_payload(content)

    assert json.loads(recovered) == VALID


def test_leading_prose_before_the_object_is_trimmed():
    content = "Here is the brief:\n" + json.dumps(VALID)

    assert json.loads(_json_payload(content)) == VALID


def test_text_without_braces_is_returned_as_is():
    # Let the validator report the real problem rather than hiding it.
    assert _json_payload("no json here") == "no json here"


def test_nested_objects_are_not_truncated():
    content = json.dumps(
        {
            "news": {"items": [{"claim": "a", "source_url": "https://x"}]},
            "executive_summary": {"summary": "s", "recommendation": "r", "confidence": "c"},
        }
    )

    payload = json.loads(_json_payload(content + " done"))

    assert payload["news"]["items"][0]["claim"] == "a"
