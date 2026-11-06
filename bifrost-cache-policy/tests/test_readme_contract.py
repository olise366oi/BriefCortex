"""Guards on the documentation and on the README's own code sample.

Two things rot silently in a package like this: the API reference drifts from
the exports, and the worked example stops compiling. Both are checkable, so both
are checked on every run rather than discovered by a reader.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import bifrost_cache_policy as pkg

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"

# Names that are real exports but deliberately kept out of the API tables.
NOT_IN_TABLES = {"__version__"}


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _api_names() -> set[str]:
    """Names listed in the first column of the API reference tables."""
    api = _readme().split("## API reference", 1)[1].split("## Worked example", 1)[0]
    names: set[str] = set()
    for line in api.splitlines():
        if line.startswith("| `"):
            names.update(
                re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", line.split("|")[1])
            )
    return names


def test_the_api_reference_matches_the_package():
    documented = _api_names()

    undocumented = sorted(set(pkg.__all__) - documented - NOT_IN_TABLES)
    invented = sorted(n for n in documented if not hasattr(pkg, n))

    assert not undocumented, f"exported but undocumented: {undocumented}"
    assert not invented, f"documented but not exported: {invented}"


def test_every_table_of_contents_link_resolves():
    def slug(heading: str) -> str:
        s = heading.strip().lower()
        s = s.replace("—", " ").replace("–", " ").replace(":", " ")
        s = re.sub(r"[^\w\s\-]", "", s)
        s = re.sub(r"\s+", "-", s.strip())
        return re.sub(r"-+", "-", s).strip("-")

    text = _readme()
    anchors = {slug(h) for h in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE)}
    toc = text.split("## Table of contents", 1)[1].split("---", 1)[0]

    broken = [link for link in re.findall(r"\]\(#([^)]+)\)", toc) if link not in anchors]
    assert not broken, f"broken anchors: {broken}"


def test_the_documented_rules_match_the_constants():
    # A table in prose that disagrees with the code is worse than no table.
    text = _readme()

    for fact_class, ttl in pkg.TTL_BY_CLASS.items():
        assert f"| `{fact_class}`" in text, fact_class
        assert f"`{ttl}`" in text, f"{fact_class} -> {ttl}"

    for rule_name, _group_a, _group_b in pkg.CONFLICT_RULES:
        assert f"`{rule_name}`" in text, rule_name


def test_the_worked_example_still_runs():
    # The README block is executed verbatim by demo/worked_example.py, which
    # asserts the miss -> hit -> refusal -> clear behaviour.
    path = ROOT / "demo" / "worked_example.py"
    spec = importlib.util.spec_from_file_location("worked_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main() == 0
