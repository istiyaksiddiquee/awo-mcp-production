"""records_to_fix_first on the real query: list_facility_quality_fields_by_city.rq run on data/directory.ttl."""

import pytest

from tests.helpers import LocalSparqlClient, load_graph


@pytest.fixture(scope="module")
def client():
    return LocalSparqlClient(load_graph())


def test_repository_has_one_row_per_facility_in_city(client):
    repository = client.facility_repository("Berlin")
    assert len(repository.einrichtung) == client.count_facilities("Berlin")
    assert repository.einrichtung["iri"].is_unique


def test_repository_addresses_belong_to_the_city(client):
    repository = client.facility_repository("Berlin")
    cities = {a["schema:addressLocality"].casefold() for a in repository.address_by_iri.values()}
    assert cities <= {"berlin"}


def test_ranking_runs_and_counts_match(client):
    result = client.records_to_fix_first("Berlin", n=5)
    assert result["summary"]["total_facilities"] == client.count_facilities("Berlin")
    assert len(result["results"]) <= 5
    assert all(r["priority"] in {"high", "medium", "low"} for r in result["results"])


def test_unknown_city_gives_empty_result(client):
    result = client.records_to_fix_first("Atlantis-Gibtsnicht")
    assert result["summary"]["total_facilities"] == 0
    assert result["results"] == []
