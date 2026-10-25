"""get_entity_card, search_service_candidates and search_directory: offline checks.

The queries (list_entity_properties_by_iri, list_linked_services_by_iri,
list_service_candidates_by_query, list_directory_facilities_by_term) run on an in-memory
rdflib graph via LocalSparqlClient: edge cases on a small Turtle snippet, and the
datathon examples on data/directory.ttl. Expected values are written out by hand from the
snippet, or counted from the raw graph, not taken from the code under test.
"""

import pytest

from src.sparql_client import (
    FACILITY_IRI_PREFIX, MAX_PAGE_SIZE, casefold_de, clamp_page, page_info, telephone_quality,
    url_quality, valid_entity_iri,
)
from tests.helpers import LocalSparqlClient, load_graph

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

CDF = FACILITY_IRI_PREFIX

SNIPPET = """
cdf:addr-1 a schema:PostalAddress ;
    schema:streetAddress "Ritterstraße 3" ; schema:postalCode "10969" ;
    schema:addressLocality "Berlin-Kreuzberg" .
cdf:addr-2 a schema:PostalAddress ;
    schema:postalCode "13353" ; schema:addressLocality "Berlin" .
cdf:addr-3 a schema:PostalAddress ;
    schema:postalCode "50667" ; schema:addressLocality "Köln" .

cdf:org-debt a org:OrganizationalUnit ;
    schema:name "Schuldner- und Insolvenzberatung" ;
    schema:telephone "03025293540" ;
    schema:email "info@example.org" ;
    schema:address cdf:addr-1 ;
    schema:parentOrganization cdf:traeger-1 .
cdf:org-plain a org:OrganizationalUnit ;
    schema:name "Beratungsstelle Wedding" ;
    schema:telephone "   " ;
    schema:address cdf:addr-2 .
cdf:org-two-phones a org:OrganizationalUnit ;
    schema:name "Suchtberatung Köln" ;
    schema:telephone "0221 111" , "0221 222" ;
    schema:openingHours "Mo 9-12" ;
    schema:address cdf:addr-3 .
cdf:org-dangling a org:OrganizationalUnit ;
    schema:name "Kaputter Link" ;
    schema:address cdf:addr-missing ;
    schema:parentOrganization cdf:addr-2 .

cdf:service-debt a schema:Service ;
    schema:name "Schuldnerberatung" ; schema:category "Schuldnerberatung" ;
    schema:provider cdf:org-debt .
cdf:service-plain a schema:Service ;
    schema:category "Schuldner- und Verbraucherinsolvenzberatung" ;
    schema:audience "Familien" ;
    schema:provider cdf:org-plain .
cdf:service-hq a schema:Service ;
    schema:name "Geschäftsstelle" ; schema:category "Verwaltung" ;
    schema:provider cdf:traeger-1 .
cdf:service-orphan a schema:Service ;
    schema:name "Schuldner ohne Ort" ; schema:category "Schuldnerberatung" .
cdf:service-sucht a schema:Service ;
    schema:name "Alkoholberatung" ; schema:category "Suchtberatung" ;
    schema:provider cdf:org-two-phones .

cdf:traeger-1 a schema:Organization ;
    schema:name "Testträger e.V." ;
    foaf:homepage "www.example.org" ;
    schema:address cdf:addr-1 .
"""


class NoQueryClient(LocalSparqlClient):
    """Fails if a query is sent: for inputs that must be rejected in Python."""

    def query(self, sparql: str) -> dict:
        raise AssertionError(f"no query should be sent, got:\n{sparql[:200]}")


@pytest.fixture(scope="module")
def snippet():
    return LocalSparqlClient(load_graph(SNIPPET))


@pytest.fixture(scope="module")
def directory():
    return LocalSparqlClient(load_graph())


# ---- helpers -------------------------------------------------------------------

@pytest.mark.parametrize("value, status", [
    (None, "missing"),
    ("", "missing"),
    ("3.025293540E9", "scientific_notation"),
    ("3025293540", "no_leading_zero"),
    ("03025293540", "as_recorded"),
    ("030 252 935-40", "as_recorded"),
    ("+49 30 1234", "as_recorded"),
    ("3025293540; 030 1", "no_leading_zero"),
])
def test_telephone_quality(value, status):
    result = telephone_quality(value)
    assert result["status"] == status and result["verified"] is False, f"{value!r} -> {result}"


@pytest.mark.parametrize("value, status", [
    (None, "missing"),
    ("www.example.org", "missing_scheme"),
    ("/search.ashx?u=x", "relative_unresolved"),
    ("ftp://example.org", "unsupported_scheme"),
    ("https://example.org/a b", "malformed"),
    ("https://example.org/a", "absolute_http_unverified"),
])
def test_url_quality(value, status):
    assert url_quality(value)["status"] == status


@pytest.mark.parametrize("iri, valid", [
    (CDF + "org-1", True),
    ("https://example.org/#org-1", False),
    (CDF + 'org-1") } DROP', False),
    (CDF + "org 1", False),
    ("", False),
])
def test_valid_entity_iri(iri, valid):
    assert valid_entity_iri(iri) is valid


def test_clamp_page_and_page_info():
    assert clamp_page(0, -3, default_limit=5) == (1, 0)
    assert clamp_page(999, 2, default_limit=5) == (MAX_PAGE_SIZE, 2)
    assert clamp_page("x", "y", default_limit=7) == (7, 0)
    assert page_info(total=12, limit=5, offset=5, returned=5) == {
        "total": 12, "returned": 5, "limit": 5, "offset": 5, "next_offset": 10, "truncated": True,
    }
    assert page_info(total=3, limit=5, offset=0, returned=3)["next_offset"] is None


# ---- get_entity_card -------------------------------------------------------------

def test_card_facility_fields_missing_and_links(snippet):
    card = snippet.get_entity_card(CDF + "org-debt")
    assert card["status"] == "ok" and card["entity_type"] == "facility"
    assert card["fields"]["telephone"] == "03025293540", "telephone must be returned exactly as stored"
    assert "opening_hours" in card["missing_fields"] and "telephone" not in card["missing_fields"]
    assert card["quality"]["telephone"]["status"] == "as_recorded"
    address = card["relationships"]["address"]
    assert address["status"] == "resolved" and address["entity"]["fields"]["street"] == "Ritterstraße 3"
    parent = card["relationships"]["parent_organization"]
    assert parent["status"] == "resolved" and parent["entity"]["fields"]["name"] == "Testträger e.V."
    assert [s["id"] for s in card["linked_services"]] == [CDF + "service-debt"]
    assert card["contact_scope"] == "facility_record"


def test_card_whitespace_value_counts_as_missing(snippet):
    card = snippet.get_entity_card(CDF + "org-plain")
    assert card["fields"]["telephone"] is None and "telephone" in card["missing_fields"]
    assert card["relationships"]["parent_organization"] == {
        "status": "missing", "property": "schema:parentOrganization", "target_id": None,
    }


def test_card_multi_valued_telephone_is_joined(snippet):
    card = snippet.get_entity_card(CDF + "org-two-phones")
    assert card["fields"]["telephone"] in ("0221 111; 0221 222", "0221 222; 0221 111")


def test_card_dangling_and_type_mismatch_links(snippet):
    card = snippet.get_entity_card(CDF + "org-dangling")
    assert card["relationships"]["address"]["status"] == "dangling"
    parent = card["relationships"]["parent_organization"]
    assert parent["status"] == "type_mismatch" and parent["target_type"] == "address"


def test_card_service_follows_provider_to_facility_and_address(snippet):
    card = snippet.get_entity_card(CDF + "service-debt")
    assert card["entity_type"] == "service" and "linked_services" not in card
    provider = card["relationships"]["provider"]
    assert provider["status"] == "resolved" and provider["target_type"] == "facility"
    nested_address = provider["entity"]["relationships"]["address"]
    assert nested_address["entity"]["fields"]["locality"] == "Berlin-Kreuzberg"


def test_card_service_can_link_to_a_provider_organisation(snippet):
    provider = snippet.get_entity_card(CDF + "service-hq")["relationships"]["provider"]
    assert provider["status"] == "resolved" and provider["target_type"] == "provider"


def test_card_provider_lists_its_services(snippet):
    card = snippet.get_entity_card(CDF + "traeger-1")
    assert card["entity_type"] == "provider" and card["linked_services_total"] == 1
    assert card["quality"]["homepage"]["status"] == "missing_scheme"


def test_card_unknown_iri(snippet):
    assert snippet.get_entity_card(CDF + "does-not-exist") == {"status": "not_found", "id": CDF + "does-not-exist"}


@pytest.mark.parametrize("iri", ["https://example.org/#x", CDF + 'x") } #', CDF + "a b", ""])
def test_card_rejects_malformed_iri_without_query(iri):
    assert NoQueryClient(load_graph(SNIPPET)).get_entity_card(iri)["status"] == "invalid_iri"


# ---- search_service_candidates ----------------------------------------------------

def test_service_candidates_link_office_and_locality(snippet):
    result = snippet.search_service_candidates("schuldner", limit=10)
    by_id = {c["service_id"]: c for c in result["candidates"]}
    assert set(by_id) == {CDF + "service-debt", CDF + "service-plain", CDF + "service-orphan"}
    debt = by_id[CDF + "service-debt"]
    assert debt["provider"]["type"] == "facility" and debt["location"]["locality"] == "Berlin-Kreuzberg"
    assert debt["location"]["scope"] == "facility_address"
    plain = by_id[CDF + "service-plain"]
    assert plain["name_is_fallback"] and plain["name"] == plain["category"]
    orphan = by_id[CDF + "service-orphan"]
    assert orphan["provider"]["status"] == "missing" and orphan["location"]["status"] == "unknown"


def test_service_candidates_organisation_scope(snippet):
    hq = snippet.search_service_candidates("Geschäftsstelle")["candidates"][0]
    assert hq["provider"]["type"] == "provider"
    assert hq["location"]["scope"] == "provider_organization_address"


def test_service_candidates_paging(snippet):
    first = snippet.search_service_candidates("schuldner", limit=2)
    second = snippet.search_service_candidates("schuldner", limit=2, offset=first["next_offset"])
    assert first["total"] == second["total"] == 3
    assert first["returned"] == 2 and first["next_offset"] == 2 and first["truncated"]
    assert second["returned"] == 1 and second["next_offset"] is None
    ids = [c["service_id"] for c in first["candidates"] + second["candidates"]]
    assert len(set(ids)) == 3, f"pages overlap or miss results: {ids}"


def test_service_candidates_match_audience_and_ignore_facility_names(snippet):
    assert snippet.search_service_candidates("familien")["total"] == 1
    # "Beratungsstelle Wedding" is a facility name, not a service field
    assert snippet.search_service_candidates("wedding")["total"] == 0


# ---- search_directory -------------------------------------------------------------

def test_directory_matches_facility_name_service_and_address(snippet):
    result = snippet.search_directory("Schuldner")
    # org-debt by name and service, org-plain only through its service category
    assert {h["iri"] for h in result["hits"]} == {CDF + "org-debt", CDF + "org-plain"}
    plain = next(h for h in result["hits"] if h["iri"] == CDF + "org-plain")
    assert {(m["source"], m["field"]) for m in plain["matched_fields"]} == {("service", "category")}


def test_directory_every_term_must_match(snippet):
    assert [h["name"] for h in snippet.search_directory("Schuldner Kreuzberg")["hits"]] == [
        "Schuldner- und Insolvenzberatung"
    ]
    assert snippet.search_directory("Schuldner Köln")["total"] == 0
    assert snippet.search_directory("Sucht 50667")["total"] == 1


def test_directory_filters_and_summaries_cover_all_matches(snippet):
    everything = snippet.search_directory("Schuldner", limit=1)
    assert everything["total"] == 2 and everything["returned"] == 1 and everything["next_offset"] == 1
    assert everything["missing_counts"]["telephone"] == 1, "whitespace-only phone counts as missing"
    assert everything["phone_format_counts"]["missing"] == 1
    assert sum(p["count"] for p in everything["by_provider"]) == 2

    with_phone = snippet.search_directory("Schuldner", has_telephone=True)
    without = snippet.search_directory("Schuldner", has_telephone=False)
    assert with_phone["total"] + without["total"] == 2
    assert with_phone["hits"][0]["telephone"] == "03025293540"

    by_provider = snippet.search_directory("Schuldner", provider="testträger")
    assert [h["iri"] for h in by_provider["hits"]] == [CDF + "org-debt"]


def test_directory_eszett_matches_ss(snippet):
    assert snippet.search_directory("ritterstrasse")["total"] == 1


@pytest.mark.parametrize("query", ["", "ab", "a b c"])
def test_directory_needs_a_long_enough_term_and_sends_no_query(query):
    result = NoQueryClient(load_graph(SNIPPET)).search_directory(query)
    assert result["status"] == "error" and "3 or more characters" in result["error"]


# ---- directory.ttl: the datathon examples -------------------------------------------

def test_directory_ttl_schuldner_matches_independent_count(directory):
    """12 facilities: the datathon result on the CSV sample, recounted from the raw graph."""
    import rdflib

    graph = directory.graph
    schema = rdflib.Namespace("http://schema.org/")
    unit = rdflib.URIRef("http://www.w3.org/ns/org#OrganizationalUnit")

    def has(node, props, term="schuldner"):
        return any(term in casefold_de(str(v)) for p in props for v in graph.objects(node, schema[p]))

    expected = set()
    for facility in graph.subjects(rdflib.RDF.type, unit):
        own = has(facility, ["name", "alternateName", "description", "category", "availableService",
                             "audience", "accessibilitySummary", "openingHours"])
        services = any(has(s, ["name", "category", "audience", "areaServed"])
                       for s in graph.subjects(schema.provider, facility))
        addresses = any(has(a, ["streetAddress", "postalCode", "addressLocality"])
                        for a in graph.objects(facility, schema.address))
        if own or services or addresses:
            expected.add(str(facility))

    result = directory.search_directory("Schuldner", limit=25)
    assert result["total"] == len(expected) == 12
    assert {h["iri"] for h in result["hits"]} == expected

    with_phone = directory.search_directory("Schuldner", has_telephone=True)
    assert [h["street"] for h in with_phone["hits"]] == ["Ritterstraße 3"]


def test_directory_ttl_service_candidates_for_schuldner(directory):
    result = directory.search_service_candidates("Schuldner", limit=25)
    assert result["total"] == 6
    assert all(c["provider"]["status"] == "resolved" for c in result["candidates"])


def test_directory_ttl_entity_card_has_hours_and_accessibility(directory):
    card = directory.get_entity_card(CDF + "org-34e986356c61")
    assert card["fields"]["name"] == "Frauenladen - Drogennotdienst Berlin gGmbH"
    assert "Montags 11 - 14 Uhr" in card["fields"]["opening_hours"]
    assert card["fields"]["accessibility"] == "Die Einrichtung ist nicht barrierefrei erreichbar."
