"""An ambiguous short name must query the legal-name variants the registry misses.

Measured on "Toro": the exact filter returned three French shells and a shipping
company, so the model had no way to pick The Toro Company. Querying "Toro
Company" surfaces it, and the model then chooses US-DE.
"""
from __future__ import annotations

from app.services import company_identity


def _record(name: str, jurisdiction: str, status: str = "ACTIVE") -> dict:
    return {
        "attributes": {
            "entity": {
                "legalName": {"name": name},
                "jurisdiction": jurisdiction,
                "legalForm": {"id": "X"},
                "status": status,
            }
        }
    }


def test_an_ambiguous_name_also_queries_legal_name_variants(monkeypatch):
    calls: list[str] = []

    def fake_fetch(name: str) -> list[dict]:
        calls.append(name)
        if name == "Toro":
            return [
                _record("TORO", "FR"),
                _record("TORO", "NL"),
                _record("TORO CORP.", "MH"),
            ]
        if name == "Toro Company":
            return [_record("THE TORO COMPANY", "US-DE")]
        return []

    monkeypatch.setattr(company_identity, "_fetch_candidates", fake_fetch)

    candidates = company_identity.rank_candidates("Toro")

    assert any(c.name == "THE TORO COMPANY" for c in candidates)
    assert "Toro Company" in calls


def test_a_settled_name_does_not_pay_for_variant_queries(monkeypatch):
    calls: list[str] = []

    def fake_fetch(name: str) -> list[dict]:
        calls.append(name)
        return [_record("INTERNATIONAL BUSINESS MACHINES CORPORATION", "US-NY")]

    monkeypatch.setattr(company_identity, "_fetch_candidates", fake_fetch)

    company_identity.rank_candidates("International Business Machines")

    assert calls == ["International Business Machines"]
