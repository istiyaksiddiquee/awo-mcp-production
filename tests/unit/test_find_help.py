"""find_social_help: need validation, ranking and result shape (offline, no endpoint, no rdflib)."""

import pytest

from src.sparql_client import HELP_NEEDS, SparqlClient, load_query


def row(name, telephone=None, postal_code="10115"):
    return {
        "service": f"cdf:{name}", "name": name, "category": "Schuldnerberatung",
        "facility_name": "Beratungsstelle", "street": "Weg 1", "postal_code": postal_code,
        "city": "Berlin", "telephone": telephone, "email": None,
    }


class StubClient(SparqlClient):
    """Returns canned rows instead of calling the endpoint; remembers the query it got."""

    def __init__(self, rows):
        super().__init__(endpoint="stub://")
        self.canned, self.seen = rows, []

    def select(self, query_name, /, **params):
        self.seen.append((query_name, params))
        return list(self.canned)


def test_unknown_need_lists_valid_needs():
    result = StubClient([]).find_help("pizza")
    assert "error" in result and result["valid_needs"] == list(HELP_NEEDS)


def test_need_is_case_and_space_insensitive_and_uses_its_terms():
    client = StubClient([row("A")])
    client.find_help("  DEBT ")
    assert client.seen == [("list_help_services_by_terms", {"terms": "schuldner|insolvenz"})]


def test_entries_with_phone_come_first_and_missing_stays_null():
    result = StubClient([row("no-phone"), row("with-phone", "030 123")]).find_help("debt")
    assert [r["service"] for r in result["results"]] == ["with-phone", "no-phone"]
    assert result["results"][1]["telephone"] is None


def test_limit_is_clamped_and_total_is_reported():
    result = StubClient([row(str(i)) for i in range(30)]).find_help("debt", limit=99)
    assert result["total_matches"] == 30 and result["showing"] == 10 == len(result["results"])
    assert StubClient([row("x")]).find_help("debt", limit=0)["showing"] == 1


def test_postal_code_prefix_filters():
    result = StubClient([row("a", postal_code="10997"), row("b", postal_code="12043"),
                         row("c", postal_code=None)]).find_help("debt", postal_code_prefix="10")
    assert [r["service"] for r in result["results"]] == ["a"]


def test_injection_in_need_is_not_a_valid_need():
    client = StubClient([])
    assert "error" in client.find_help('debt" ) } DROP')
    assert client.seen == []  # never reached the query


def test_terms_are_escaped_in_the_query_file():
    query = load_query("list_help_services_by_terms", terms="schuldner|insolvenz")
    assert '"schuldner|insolvenz"' in query
