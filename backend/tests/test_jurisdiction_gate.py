"""A fuzzy registry match must not decide which country's press is searched.

Measured: a brief for "YKK" — the Japanese zipper group — resolved its identity
to "YKK FRANCE" at 0.52 confidence. The jurisdiction from that match (FR) was
then used unconditionally, so the national press list was generated for France
and every French-language query searched French titles. The group is Japanese and
its business press is Japanese, so the whole national lane pointed at the wrong
country.

The registry is a lookup, not an oracle. An unconfident match is no match.
"""
from __future__ import annotations

from app.services.company_identity import CompanyIdentity
from app.services.ingestion import jurisdiction_from_registry


def _identity(legal_name: str, jurisdiction: str, confident: bool) -> CompanyIdentity:
    return CompanyIdentity(
        name=legal_name,
        jurisdiction=jurisdiction,
        legal_form="X",
        status="ACTIVE",
        confident=confident,
    )


def test_a_confident_match_supplies_the_jurisdiction():
    identity = _identity("CANADA GOOSE HOLDINGS INC.", "CA-ON", confident=True)

    assert jurisdiction_from_registry(identity, "Canada Goose") == "CA-ON"


def test_an_unconfident_subsidiary_match_supplies_nothing():
    # The regression: "YKK" -> "YKK FRANCE" at 0.52 confidence became the
    # jurisdiction for a Japanese group.
    identity = _identity("YKK FRANCE", "FR", confident=False)

    assert jurisdiction_from_registry(identity, "YKK") is None


def test_no_registry_match_supplies_nothing():
    assert jurisdiction_from_registry(None, "Some Private Company") is None
