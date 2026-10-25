"""
Tests for records_to_fix_first.

They run on a small fake dataset, not on the real CSV or API, so they keep
working when the data source changes. The only things the code needs from
the data are: a facility table, an address lookup and a provider lookup.
"""

import json

import numpy as np
import pandas as pd

from src.fix_first import (
    analyse_all,
    coverage_gain,
    decode_source_link,
    is_missing,
    normalise_name,
    phone_needs_repair,
    priority_tier,
    records_to_fix_first,
)


# --------------------------------------------------
# Fake data
# --------------------------------------------------

COLUMNS = [
    "iri",
    "schema:name",
    "schema:alternateName",
    "schema:telephone",
    "schema:email",
    "foaf:homepage",
    "schema:mainEntityOfPage",
    "schema:category",
    "schema:availableService",
    "schema:audience",
    "schema:accessibilitySummary",
    "schema:openingHours",
    "schema:parentOrganization",
    "cdf:parentOrgClassification",
    "schema:address",
]

ADDR_1 = "addr-1"
ADDR_2 = "addr-2"
PROV_AWO = "traeger-awo"
PROV_SPI = "traeger-spi"

CARITAS_LINK = (
    "/search.ashx?g=1&p=0&u=http%3a%2f%2fwww.caritas.de%2fadressen%2fx"
    "%3fsearchterm%3d"
)


def rec(i, fields):
    data = {column: np.nan for column in COLUMNS}
    data["iri"] = f"org-{i}"
    data["schema:address"] = ADDR_1
    data.update(fields)
    return data


RICH = {
    "schema:telephone": "030 1111111",
    "schema:email": "a@example.org",
    "foaf:homepage": "http://example.org",
    "schema:category": "Beratung",
    "schema:availableService": "Beratung",
}


class FakeRepository:
    """Same attributes the real repository exposes to fix_first."""

    def __init__(self):
        rows = [
            # 0: church body, almost empty -> scope_check
            rec(0, {"schema:name": "Erzbischöfliches Ordinariat",
                    "schema:mainEntityOfPage": CARITAS_LINK}),
            # 1: church-like name but a real, informative record -> not scope
            rec(1, {**RICH, "schema:name": "Diözesanrat Beispiel",
                    "schema:openingHours": "Mo-Fr 9-17"}),
            # 2: no name -> junk
            rec(2, {"schema:address": np.nan}),
            # 3 and 4: same name and address -> 4 is the duplicate
            rec(3, {"schema:name": "Beratung Nord",
                    "schema:telephone": "30224451400",
                    "schema:category": "Beratung"}),
            rec(4, {"schema:name": "Beratung Nord (BN)"}),
            # 5 and 6: same generic name, different addresses -> not duplicates
            rec(5, {"schema:name": "Betreutes Einzelwohnen"}),
            rec(6, {"schema:name": "Betreutes Einzelwohnen",
                    "schema:address": ADDR_2}),
            # 7: street and postal code inside the name -> repair
            rec(7, {"schema:name": "Beratung Süd, Hauptstr. 5, 10115 Berlin"}),
            # 8: informative, opening hours missing -> complete_key_field
            rec(8, {**RICH, "schema:name": "Hilfe Mitte"}),
            # 9: informative, key fields present -> minor_gaps
            rec(9, {**RICH, "schema:name": "Zentrum Ost",
                    "schema:openingHours": "Mo-Fr 9-17"}),
            # 10: bare, with a Caritas source link -> enrich_from_source
            rec(10, {"schema:name": "Kita Sonnenschein",
                     "schema:mainEntityOfPage": CARITAS_LINK}),
            # 11: bare, provider linked -> ask_provider
            rec(11, {"schema:name": "Familienpflege",
                     "schema:parentOrganization": PROV_AWO}),
            # 12: bare, no link, provider only in the name -> ask_provider
            rec(12, {"schema:name":
                     "AWO Berlin Kreisverband Mitte e.V. - Schuldnerberatung"}),
            # 13 and 14: identical gaps, 14 is urgent help
            rec(13, {"schema:name": "Kleiderkammer"}),
            rec(14, {"schema:name": "Wohnungslosenhilfe Nord"}),
            # 15: bare, provider without a homepage -> ask_provider, no lead
            rec(15, {"schema:name": "Sozialdienst",
                     "schema:parentOrganization": PROV_SPI}),
        ]

        self.einrichtung = pd.DataFrame(rows, columns=COLUMNS)

        self.address_by_iri = {
            ADDR_1: {
                "schema:streetAddress": "Hauptstr. 1",
                "schema:postalCode": "10115",
                "schema:addressLocality": "Berlin",
            },
            ADDR_2: {
                "schema:streetAddress": "Nebenweg 2",
                "schema:postalCode": "10117",
                "schema:addressLocality": "Berlin",
            },
        }

        self.provider_by_iri = {
            PROV_AWO: {
                "iri": PROV_AWO,
                "schema:name": "AWO Kreisverband Berlin-Mitte e.V.",
                "foaf:homepage": "http://awo.example",
            },
            PROV_SPI: {
                "iri": PROV_SPI,
                "schema:name": "SPI Stiftung Beispiel",
                "foaf:homepage": np.nan,
            },
        }


def by_iri(repo):
    return {r["iri"]: r for r in analyse_all(repo)}


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def test_is_missing():
    assert is_missing(None)
    assert is_missing(np.nan)
    assert is_missing("")
    assert is_missing("   ")
    assert not is_missing("0")
    assert not is_missing("text")


def test_decode_source_link():
    assert decode_source_link(CARITAS_LINK) == (
        "http://www.caritas.de/adressen/x?searchterm="
    )
    assert decode_source_link("https://example.org/page") == (
        "https://example.org/page"
    )
    assert decode_source_link(np.nan) is None
    assert decode_source_link("/search.ashx?g=1&p=0") is None


def test_normalise_name():
    assert normalise_name("Psychosoziale Betreuung für Substituierte (PSB)") == (
        "psychosoziale betreuung für substituierte"
    )
    assert normalise_name("Café-Streetwork 'Nur Nach Hause...'") == (
        "café streetwork nur nach hause"
    )
    assert normalise_name(np.nan) == ""


def test_phone_needs_repair():
    assert phone_needs_repair(30224451400.0)
    assert phone_needs_repair("30224451400")
    assert not phone_needs_repair("030 22445 1400")
    assert not phone_needs_repair("+49 30 224451400")
    assert not phone_needs_repair(np.nan)


# --------------------------------------------------
# Buckets
# --------------------------------------------------

def test_scope_check_for_bare_church_body():
    assert by_iri(FakeRepository())["org-0"]["bucket"] == "scope_check"


def test_informative_record_with_church_like_name_is_not_scope_check():
    assert by_iri(FakeRepository())["org-1"]["bucket"] != "scope_check"


def test_record_without_name_is_junk():
    result = by_iri(FakeRepository())["org-2"]

    assert result["bucket"] == "junk_or_duplicate"
    assert result["name"] is None


def test_duplicate_with_same_name_and_address():
    results = by_iri(FakeRepository())

    assert results["org-4"]["bucket"] == "junk_or_duplicate"
    assert "Beratung Nord" in results["org-4"]["reasons"][0]
    # The more informative record is kept as the original.
    assert results["org-3"]["bucket"] != "junk_or_duplicate"


def test_same_name_at_different_addresses_is_not_a_duplicate():
    results = by_iri(FakeRepository())

    assert results["org-5"]["bucket"] != "junk_or_duplicate"
    assert results["org-6"]["bucket"] != "junk_or_duplicate"


def test_name_with_address_needs_repair():
    assert by_iri(FakeRepository())["org-7"]["bucket"] == "repair"


def test_informative_record_missing_hours():
    result = by_iri(FakeRepository())["org-8"]

    assert result["bucket"] == "complete_key_field"
    assert "opening hours" in result["missing"]
    assert "telephone" not in result["missing"]


def test_informative_record_with_key_fields_has_minor_gaps():
    assert by_iri(FakeRepository())["org-9"]["bucket"] == "minor_gaps"


def test_bare_record_with_source_link_is_enrich_from_source():
    result = by_iri(FakeRepository())["org-10"]

    assert result["bucket"] == "enrich_from_source"
    assert result["lead"] == "http://www.caritas.de/adressen/x?searchterm="


def test_bare_record_with_provider_link_is_ask_provider():
    result = by_iri(FakeRepository())["org-11"]

    assert result["bucket"] == "ask_provider"
    assert result["provider"] == "AWO Kreisverband Berlin-Mitte e.V."
    assert result["lead"] == "http://awo.example"


def test_provider_matched_from_name():
    result = by_iri(FakeRepository())["org-12"]

    assert result["bucket"] == "ask_provider"
    assert result["provider"] == "AWO Kreisverband Berlin-Mitte e.V."
    assert result["lead"] == "http://awo.example"
    assert "provider" in result["missing"]  # the link itself is still missing


def test_provider_without_homepage_gives_no_lead():
    result = by_iri(FakeRepository())["org-15"]

    assert result["bucket"] == "ask_provider"
    assert result["lead"] is None


def test_bare_record_without_any_lead_needs_research():
    assert by_iri(FakeRepository())["org-13"]["bucket"] == "needs_research"


def test_phone_without_leading_zero_is_noted_but_does_not_change_bucket():
    result = by_iri(FakeRepository())["org-3"]

    assert result["bucket"] != "repair"
    assert any("leading zero" in reason for reason in result["reasons"])


# --------------------------------------------------
# Scoring
# --------------------------------------------------

def test_urgent_help_scores_higher_with_same_gaps():
    results = by_iri(FakeRepository())

    assert results["org-14"]["missing"] == results["org-13"]["missing"]
    assert results["org-14"]["score"] > results["org-13"]["score"]


def test_record_with_known_provider_ranks_above_hopeless_one():
    results = by_iri(FakeRepository())

    assert results["org-11"]["score"] > results["org-13"]["score"]


# --------------------------------------------------
# Projection
# --------------------------------------------------

def test_coverage_gain_excludes_scope_check_records():
    repo = FakeRepository()
    results = by_iri(repo)

    selected = [results["org-0"], results["org-11"]]
    gain = coverage_gain(repo, selected)

    assert gain["records_counted"] == 1

    # 16 records, 4 have a phone. One fixable record gets one.
    phone = gain["fields"]["telephone"]
    assert phone["before"].startswith("4 of 16")
    assert phone["after"].startswith("5 of 16")


# --------------------------------------------------
# Main function
# --------------------------------------------------

def test_summary_counts():
    output = records_to_fix_first(FakeRepository(), n=100)

    assert output["summary"]["total_facilities"] == 16
    assert output["summary"]["by_bucket"] == {
        "scope_check": 1,
        "junk_or_duplicate": 2,
        "repair": 1,
        "complete_key_field": 1,
        "minor_gaps": 2,
        "enrich_from_source": 1,
        "ask_provider": 3,
        "needs_research": 5,
    }


def test_dataset_issue_reports_phones_without_leading_zero():
    output = records_to_fix_first(FakeRepository(), n=100)

    assert output["summary"]["dataset_issues"][
        "phones_without_leading_zero"
    ] == 1


def test_n_limits_results_and_ranks_them():
    output = records_to_fix_first(FakeRepository(), n=3, explain=True)

    assert len(output["results"]) == 3
    assert [r["rank"] for r in output["results"]] == [1, 2, 3]

    scores = [r["score"] for r in output["results"]]
    assert scores == sorted(scores, reverse=True)


def test_priority_tiers():
    assert priority_tier(24.5) == "high"
    assert priority_tier(18.0) == "high"
    assert priority_tier(17.9) == "medium"
    assert priority_tier(9.0) == "medium"
    assert priority_tier(8.9) == "low"


def test_default_output_shows_priority_but_not_the_score():
    output = records_to_fix_first(FakeRepository(), n=100)

    for record in output["results"]:
        assert record["priority"] in ("high", "medium", "low")
        assert "score" not in record
        assert "score_details" not in record

    assert sum(output["summary"]["by_priority"].values()) == 16


def test_explain_adds_score_and_breakdown():
    output = records_to_fix_first(FakeRepository(), n=100, explain=True)

    for record in output["results"]:
        details = record["score_details"]
        expected = round(
            details["missing_fields_weight"]
            * details["bonus_factor"]
            * details["bucket_multiplier"],
            1,
        )

        assert record["score"] == expected
        assert details["bonus_factor"] == round(
            1.0 + sum(details["bonuses"].values()), 2
        )


def test_breakdown_shows_why_a_record_ranks_higher():
    results = by_iri(FakeRepository())

    urgent = results["org-14"]["score_details"]
    routine = results["org-13"]["score_details"]

    assert any("urgent" in name for name in urgent["bonuses"])
    assert routine["bonuses"] == {}
    assert urgent["missing_fields_weight"] == routine["missing_fields_weight"]


def test_bucket_filter():
    output = records_to_fix_first(
        FakeRepository(), n=100, bucket="ask_provider"
    )

    assert len(output["results"]) == 3
    assert {r["bucket"] for r in output["results"]} == {"ask_provider"}


def test_provider_filter_is_partial_and_case_insensitive():
    output = records_to_fix_first(FakeRepository(), n=100, provider="awo")

    names = {r["name"] for r in output["results"]}

    assert names == {
        "Familienpflege",
        "AWO Berlin Kreisverband Mitte e.V. - Schuldnerberatung",
    }


def test_unassigned_filter():
    output = records_to_fix_first(
        FakeRepository(), n=100, provider="unassigned"
    )

    # 16 records, 3 have a provider (two linked, one matched from the name).
    assert output["summary"]["matching_filter"] == 13


def test_urgent_focus_only_returns_urgent_services():
    output = records_to_fix_first(FakeRepository(), n=100, focus="urgent")

    names = {r["name"] for r in output["results"]}

    assert names == {
        "AWO Berlin Kreisverband Mitte e.V. - Schuldnerberatung",
        "Wohnungslosenhilfe Nord",
    }
    assert all(r["urgent_help"] for r in output["results"])
    assert output["summary"]["filter"]["focus"] == "urgent"


def test_urgent_focus_can_be_combined_with_a_provider():
    output = records_to_fix_first(
        FakeRepository(), n=100, focus="urgent", provider="awo"
    )

    assert [r["name"] for r in output["results"]] == [
        "AWO Berlin Kreisverband Mitte e.V. - Schuldnerberatung"
    ]


def test_summary_counts_shown_and_urgent_records():
    everything = records_to_fix_first(FakeRepository(), n=100)
    summary = everything["summary"]

    assert summary["urgent_in_matching_records"] == 2
    assert summary["shown"]["records"] == 16
    assert summary["shown"]["urgent"] == 2

    top_three = records_to_fix_first(FakeRepository(), n=3)["summary"]

    assert top_three["shown"]["records"] == 3
    assert top_three["urgent_in_matching_records"] == 2
    assert top_three["shown"]["urgent"] <= 2
    assert top_three["shown"]["high_priority"] == sum(
        1 for r in records_to_fix_first(FakeRepository(), n=3)["results"]
        if r["priority"] == "high"
    )


def test_no_matching_records_returns_empty_results():
    output = records_to_fix_first(
        FakeRepository(), n=10, provider="no such provider"
    )

    assert output["results"] == []
    assert output["summary"]["matching_filter"] == 0


def test_internal_fields_are_not_returned():
    output = records_to_fix_first(FakeRepository(), n=100)

    for record in output["results"]:
        assert "missing_fields" not in record
        assert "provider_hint" not in record
        assert "phone_needs_repair" not in record


def test_group_by_provider():
    output = records_to_fix_first(
        FakeRepository(), n=100, group_by="provider"
    )

    assert "results" not in output
    assert output["excluded_scope_check_or_junk"] == 3

    batches = output["batches"]
    groups = [b["group"] for b in batches]

    # Providers that can be asked come first, unknown owners come last.
    assert groups[0] == "AWO Kreisverband Berlin-Mitte e.V."
    assert groups[-1] == "Unassigned (owner unknown)"

    # Raw scores are hidden unless explain is requested.
    assert all("total_score" not in b for b in batches)
    assert all("high_priority_facilities" in b for b in batches)

    explained = records_to_fix_first(
        FakeRepository(), n=100, group_by="provider", explain=True
    )
    assert all("total_score" in b for b in explained["batches"])

    awo = batches[0]
    assert awo["facilities"] == 2
    assert awo["can_be_asked_directly"] is True
    assert awo["projected_coverage_if_filled"]["telephone"].startswith("4 to 6")

    unknown = batches[-1]
    assert unknown["can_be_asked_directly"] is False
    assert isinstance(unknown["projected_coverage_if_filled"], str)


def test_output_is_valid_json_without_nan():
    for kwargs in ({"n": 100}, {"n": 100, "group_by": "provider"}):
        output = records_to_fix_first(FakeRepository(), **kwargs)
        json.dumps(output, allow_nan=False)
