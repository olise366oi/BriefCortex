"""Article routing must not put a hundred excerpts in one model call.

Measured on a Wipro run: the reading stage took 60 seconds and the classifier
call inside it was the largest single contributor, because every article's
opening was concatenated into one request. The batch also bounds the damage —
a malformed reply now costs one batch instead of every article in the run.
"""
from __future__ import annotations

import asyncio

from app.services import article_subject


class _Choice:
    def __init__(self, content: str) -> None:
        self.message = type("M", (), {"content": content})()


class _Response:
    def __init__(self, content: str) -> None:
        self.choices = [_Choice(content)]


def test_every_batch_is_asked_concurrently():
    in_flight = 0
    peak = 0

    class _Completions:
        async def create(self, **_kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return _Response('{"items": []}')

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    original = article_subject.build_client
    article_subject.build_client = lambda: (_Client(), "test-model")
    try:
        openings = [f"Article number {i} reports on the company." for i in range(60)]
        asyncio.run(article_subject.classify_article_subjects(openings, "Wipro"))
    finally:
        article_subject.build_client = original

    assert peak > 1, f"batches ran one at a time (peak in flight = {peak})"


def test_a_batch_keeps_the_original_indices():
    """Routes must line up with the caller's list, not restart per batch."""
    seen_indices: list[int] = []

    class _Completions:
        async def create(self, **_kwargs):
            # Echo back every index the batch mentioned, as "primary".
            text = _kwargs["messages"][1]["content"]
            for token in text.split("["):
                head = token.split("]")[0]
                if head.isdigit():
                    seen_indices.append(int(head))
            items = [
                {"index": int(token.split("]")[0]), "subject": "primary"}
                for token in text.split("[")
                if token.split("]")[0].isdigit()
            ]
            import json

            return _Response(json.dumps({"items": items}))

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    original = article_subject.build_client
    article_subject.build_client = lambda: (_Client(), "test-model")
    try:
        openings = [f"Opening {i} names Wipro." for i in range(45)]
        routes = asyncio.run(
            article_subject.classify_article_subjects(openings, "Wipro")
        )
    finally:
        article_subject.build_client = original

    assert len(routes) == 45
    # The last article is in a later batch and still carries its own index.
    assert routes[-1].index == 44
    assert routes[-1].subject == "primary"


def test_a_failed_batch_falls_back_to_comparison_for_that_batch_only():
    calls = {"n": 0}

    class _Completions:
        async def create(self, **_kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("model unavailable")
            return _Response('{"items": [{"index": 20, "subject": "primary"}]}')

    class _Client:
        chat = type("Chat", (), {"completions": _Completions()})()

    original = article_subject.build_client
    article_subject.build_client = lambda: (_Client(), "test-model")
    try:
        # Two batches: 20 excerpts each at this batch size.
        openings = [f"Opening {i} names the company." for i in range(
            article_subject.SUBJECT_BATCH_SIZE * 2
        )]
        routes = asyncio.run(
            article_subject.classify_article_subjects(openings, "Wipro")
        )
    finally:
        article_subject.build_client = original

    # The failed batch is "comparison" — an unknown subject must not write the
    # fact base — and the batch that answered is still used.
    assert routes[0].subject == "comparison"
    assert any(route.subject == "primary" for route in routes)


def test_an_empty_opening_list_makes_no_call():
    original = article_subject.build_client
    article_subject.build_client = lambda: (_ for _ in ()).throw(
        AssertionError("must not build a client")
    )
    try:
        assert asyncio.run(article_subject.classify_article_subjects([], "Wipro")) == []
    finally:
        article_subject.build_client = original


def test_a_blank_company_makes_no_call():
    original = article_subject.build_client
    article_subject.build_client = lambda: (_ for _ in ()).throw(
        AssertionError("must not build a client")
    )
    try:
        routes = asyncio.run(
            article_subject.classify_article_subjects(["Some opening."], "  ")
        )
    finally:
        article_subject.build_client = original

    assert routes[0].subject == "unrelated"
