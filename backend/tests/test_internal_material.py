"""Internal material is an intake path, not an analysis.

A caller who supplies internal material instead of letting us read the market
gets it stored and available to the recommendation. It must never be treated as
evidence about the counterparty, and it must not grow without bound.
"""
from __future__ import annotations

from app.services.internal_material import (
    MAX_MATERIALS_PER_COMPANY,
    MaterialStore,
    build_material,
    render_materials,
)


def test_material_is_stored_and_listed_by_company():
    store = MaterialStore()
    store.add(build_material("Canada Goose", "board pack", "Our margin targets."))

    items = store.list("Canada Goose")

    assert len(items) == 1
    assert items[0].body == "Our margin targets."


def test_the_company_key_ignores_case_and_spacing():
    store = MaterialStore()
    store.add(build_material("Canada Goose", "", "note"))

    assert store.list("  canada   goose ") == store.list("Canada Goose")


def test_a_submission_without_a_body_is_rejected():
    assert build_material("Canada Goose", "title", "   ") is None
    assert build_material("", "title", "body") is None


def test_only_the_most_recent_materials_are_kept():
    store = MaterialStore()
    for index in range(MAX_MATERIALS_PER_COMPANY + 2):
        store.add(build_material("Canada Goose", f"note {index}", f"body {index}"))

    items = store.list("Canada Goose")

    assert len(items) == MAX_MATERIALS_PER_COMPANY
    # The oldest are superseded, not additive.
    assert items[-1].body == f"body {MAX_MATERIALS_PER_COMPANY + 1}"


def test_rendering_joins_titles_and_bodies():
    store = MaterialStore()
    store.add(build_material("Canada Goose", "board pack", "margin targets"))
    store.add(build_material("Canada Goose", "sales plan", "Q1 volume"))

    rendered = render_materials(store.list("Canada Goose"))

    assert "[board pack]" in rendered
    assert "margin targets" in rendered
    assert "[sales plan]" in rendered


def test_clearing_removes_only_that_company():
    store = MaterialStore()
    store.add(build_material("Canada Goose", "", "a"))
    store.add(build_material("YKK", "", "b"))

    removed = store.clear("Canada Goose")

    assert removed == 1
    assert store.list("Canada Goose") == []
    assert len(store.list("YKK")) == 1
