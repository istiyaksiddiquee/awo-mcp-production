"""data_quality_report: attribution, scoring and the report text (offline, fake rows)."""

import pytest

from src.quality_report import (
    UNKNOWN_ORIGIN,
    build_quality_report,
    city_spelling_problem,
    origin_of,
    score_organisation,
)
from src.sparql_client import SparqlClient


def row(provider=None, source=None, telephone="030123", email=1, homepage=1, hours=1,
        category=1, street="Weg 1", postal_code="10115", city="Berlin"):
    return {
        "facility": "cdf:org-x", "provider": provider, "source": source, "telephone": telephone,
        "has_email": str(email), "has_homepage": str(homepage), "has_opening_hours": str(hours),
        "has_category": str(category), "street": street, "postal_code": postal_code, "city": city,
    }


CARITAS = "/search.ashx?g=1&u=http%3a%2f%2fwww.caritas.de%2fadressen%2fx%2f10551-berlin%2f1"


def test_origin_prefers_provider_then_source_host_then_unknown():
    assert origin_of(row(provider="AWO Berlin", source=CARITAS)) == "AWO Berlin"
    assert origin_of(row(source=CARITAS)) == "www.caritas.de"
    assert origin_of(row(source="http://www.dhs.de/x")) == "www.dhs.de"
    assert origin_of(row()) == UNKNOWN_ORIGIN


def test_score_counts_missing_fields_and_blank_values():
    report = score_organisation([row(), row(telephone=None, email=0), row(telephone="  ", street=None)])
    assert report["facilities"] == 3
    assert report["completeness"]["telephone"]["missing"] == 2
    assert report["completeness"]["email"]["missing"] == 1
    assert report["completeness"]["street"]["missing"] == 1
    assert report["completeness"]["category"]["filled_percentage"] == 100.0
    assert 0 <= report["score"] <= 100


def test_perfect_organisation_scores_100_and_has_no_advice():
    result = build_quality_report([row(provider="A"), row(provider="A")], "A")
    assert result["score"] == 100.0
    assert result["what_to_improve"] == []
    assert "No gaps found" in result["draft_message"]


def test_problems_phone_and_city_spelling_are_reported():
    result = build_quality_report(
        [row(provider="A", telephone="3025293540", city="Berlin-Mitte"), row(provider="A", city="Berl Berlin")], "A"
    )
    assert result["problems"] == {"phone_lost_leading_zero": 1, "city_spelling_not_normalised": 2}
    text = " ".join(result["what_to_improve"])
    assert "leading zero" in text and "plain city name" in text
    assert not city_spelling_problem(row(city="Berlin")) and not city_spelling_problem(row(city="Hamburg"))


def test_biggest_gap_comes_first_and_draft_names_the_organisation():
    rows = [row(provider="A", hours=0, email=0) for _ in range(3)] + [row(provider="A", email=0)]
    result = build_quality_report(rows, "a")
    assert result["what_to_improve"][0].startswith("Add an email address for 4 of 4")
    assert result["draft_message"].startswith("Hello A,")
    assert "4 records" in result["draft_message"]


def test_overview_is_sorted_by_size_and_limited():
    rows = [row(provider="Big")] * 3 + [row(provider="Small")] + [row(source="http://www.dhs.de/x")] * 2
    result = build_quality_report(rows, "", top=2)
    assert [o["organisation"] for o in result["overview"]] == ["Big", "www.dhs.de"]
    assert result["organisations"] == 3 and result["showing"] == 2 and result["total_facilities"] == 6


def test_unknown_organisation_returns_an_error_with_known_names():
    result = build_quality_report([row(provider="AWO")], "Diakonie")
    assert "error" in result and result["known_organisations"] == ["AWO"]


def test_client_method_uses_the_flags_query():
    class Stub(SparqlClient):
        def select(self, query_name, /, **params):
            assert query_name == "list_facility_quality_flags" and params == {}
            return [row(provider="AWO")]

    assert Stub(endpoint="stub://").data_quality_report("awo")["organisation"] == "AWO"
