"""find_nearby and get_awo_details: offline checks on helpers, edge cases and directory.ttl.

The queries (list_facilities_by_bbox, get_postal_code_location_by_postal_code,
get_facility_details_by_iri) run on an in-memory rdflib graph via LocalSparqlClient.
Expected values come from distances computed in Python on the raw graph, not from the
code under test.
"""

import math

import pytest

from src.sparql_client import (
    FACILITY_IRI_PREFIX, accessibility_flag, bounding_box, haversine_km, matches_terms,
    source_page, split_values,
)
from tests.helpers import LocalSparqlClient, load_graph

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

CDF = FACILITY_IRI_PREFIX

# Origin at postal code 99991 (50.0, 10.0). 0.01° of latitude is about 1.11 km.
SNIPPET = """
cdf:addr-origin a schema:PostalAddress ;
    schema:streetAddress "Hauptstraße 1" ; schema:postalCode "99991" ;
    schema:addressLocality "Teststadt" ;
    schema:latitude "50.0" ; schema:longitude "10.0" .
cdf:addr-near a schema:PostalAddress ;
    schema:streetAddress "Nahweg 2" ; schema:postalCode "99992" ;
    schema:addressLocality "Teststadt" ;
    schema:latitude "50.005" ; schema:longitude "10.0" .
cdf:addr-mid a schema:PostalAddress ;
    schema:postalCode "99993" ; schema:addressLocality "Teststadt" ;
    schema:latitude "50.0135" ; schema:longitude "10.0" .
cdf:addr-far a schema:PostalAddress ;
    schema:postalCode "99994" ; schema:addressLocality "Teststadt" ;
    schema:latitude "50.027" ; schema:longitude "10.0" .
cdf:addr-nocoords a schema:PostalAddress ;
    schema:streetAddress "Ohneweg 5" ; schema:postalCode "99991" .

cdf:org-origin a org:OrganizationalUnit ;
    schema:name "Beratungsstelle Mitte" ; schema:address cdf:addr-origin ;
    schema:telephone "+491234" ;
    schema:accessibilitySummary "Die Einrichtung ist nicht barrierefrei erreichbar." .
cdf:org-near a org:OrganizationalUnit ;
    schema:name "Straßenhilfe Nord" ; schema:address cdf:addr-near ;
    schema:email "info@example.org" ;
    schema:accessibilitySummary "Die Einrichtung ist barrierefrei erreichbar." ;
    schema:mainEntityOfPage "/search.ashx?g=1&u=http%3a%2f%2fwww.caritas.de%2fadressen%2fx%2f1%3fsearchterm%3d" ;
    schema:parentOrganization cdf:traeger-1 .
cdf:org-mid a org:OrganizationalUnit ;
    schema:name "Jugendtreff" ; schema:address cdf:addr-mid .
cdf:org-far a org:OrganizationalUnit ;
    schema:name "Seniorenclub" ; schema:address cdf:addr-far .
cdf:org-nocoords a org:OrganizationalUnit ;
    schema:name "Ohne Koordinaten" ; schema:address cdf:addr-nocoords .

cdf:service-1 a schema:Service ;
    schema:name "Allgemeine Suchtberatung" ; schema:category "Suchtberatung" ;
    schema:provider cdf:org-near .
cdf:service-2 a schema:Service ;
    schema:name "Wohnungsnothilfe" ; schema:category "Wohnungsnotfallhilfe" ;
    schema:provider cdf:org-near .

cdf:traeger-1 a schema:Organization ; schema:name "Testträger e.V." .
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


def names(result):
    return [item["name"] for item in result["results"]]


# ---- helpers -------------------------------------------------------------------

def test_haversine_one_degree_of_latitude():
    assert haversine_km(50.0, 10.0, 51.0, 10.0) == pytest.approx(111.19, abs=0.01)


@pytest.mark.parametrize("latitude", [0.0, 52.5, 70.0])
def test_bounding_box_contains_the_whole_circle(latitude):
    radius = 5.0
    south, west, north, east = map(float, bounding_box(latitude, 10.0, radius).split(","))
    for bearing in range(0, 360, 15):
        # a point exactly radius km away in this direction
        d = radius / 6371.0
        lat1, lon1, b = math.radians(latitude), math.radians(10.0), math.radians(bearing)
        lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(b))
        lon2 = lon1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(lat1),
                                 math.cos(d) - math.sin(lat1) * math.sin(lat2))
        point = (math.degrees(lat2), math.degrees(lon2))
        assert south <= point[0] <= north and west <= point[1] <= east, (
            f"point {point} at {radius} km, bearing {bearing}°, lies outside the box "
            f"{(south, west, north, east)} around ({latitude}, 10.0)"
        )


@pytest.mark.parametrize("query, expected", [
    (None, True), ("", True), (" , ", True),
    ("sucht", True), ("SUCHT", True), ("Pflege, Sucht", True), ("pflege", False),
    ("Strasse", True),  # ß in the data matches ss in the query, like the .rq files
])
def test_matches_terms(query, expected):
    texts = ["Straßenhilfe", None, "Allgemeine Suchtberatung"]
    assert matches_terms(texts, query) is expected, f"matches_terms({texts}, {query!r})"


@pytest.mark.parametrize("summary, expected", [
    ("Die Einrichtung ist barrierefrei erreichbar.", True),
    ("Die Einrichtung ist nicht barrierefrei erreichbar.", False),
    (None, None), ("", None),
])
def test_accessibility_flag(summary, expected):
    assert accessibility_flag(summary) is expected


def test_source_page_decodes_caritas_redirects():
    relative = "/search.ashx?g=1&u=http%3a%2f%2fwww.caritas.de%2fadressen%2fx%2f1%3fsearchterm%3d"
    assert source_page(relative) == "http://www.caritas.de/adressen/x/1"
    assert source_page("https://www.dhs.de/x") == "https://www.dhs.de/x"
    assert source_page("not a link") is None
    assert source_page(None) is None


def test_split_values():
    assert split_values("a; b;; c ") == ["a", "b", "c"]
    assert split_values(None) == []


# ---- resolve_location ------------------------------------------------------------

def test_resolve_location_from_coordinates_sends_no_query():
    origin = NoQueryClient(load_graph(SNIPPET)).resolve_location(" 50.0 , 10.0 ")
    assert origin == {"latitude": 50.0, "longitude": 10.0, "matched_by": "coordinates"}


@pytest.mark.parametrize("location", ["Müllerstraße", "1234", "123456", "91.0,10.0", "50,200", ""])
def test_resolve_location_rejects_bad_input_without_query(location):
    assert NoQueryClient(load_graph(SNIPPET)).resolve_location(location) is None


def test_resolve_location_from_postal_code_averages_its_addresses(snippet):
    origin = snippet.resolve_location("99991")
    # addr-nocoords shares the postal code but has no coordinates, so it is left out
    assert origin["matched_by"] == "postal_code" and origin["matched_addresses"] == 1, origin
    assert (origin["latitude"], origin["longitude"]) == pytest.approx((50.0, 10.0))


def test_resolve_location_unknown_postal_code(snippet):
    assert snippet.resolve_location("12345") is None


# ---- find_nearby on the snippet -------------------------------------------------

def test_find_nearby_returns_facilities_within_radius_nearest_first(snippet):
    result = snippet.find_nearby("99991", radius_km=2)
    assert names(result) == ["Beratungsstelle Mitte", "Straßenhilfe Nord", "Jugendtreff"], result
    distances = [item["distance_km"] for item in result["results"]]
    assert distances == sorted(distances) and distances[-1] <= 2, distances
    assert result["total_found"] == 3


def test_find_nearby_excludes_facilities_without_coordinates(snippet):
    result = snippet.find_nearby("99991", radius_km=50)
    assert "Ohne Koordinaten" not in names(result), names(result)
    assert "Seniorenclub" in names(result)


def test_find_nearby_matches_offers_and_any_term(snippet):
    assert names(snippet.find_nearby("99991", 5, query="Suchtberatung")) == ["Straßenhilfe Nord"]
    assert names(snippet.find_nearby("99991", 5, query="Wohnungsnot, Jugend")) == [
        "Straßenhilfe Nord", "Jugendtreff",
    ]
    assert names(snippet.find_nearby("99991", 5, query="nichts-passt")) == []


def test_find_nearby_accessible_only_keeps_only_confirmed_accessible(snippet):
    assert names(snippet.find_nearby("99991", 5, accessible_only=True)) == ["Straßenhilfe Nord"]


def test_find_nearby_limit_and_total(snippet):
    result = snippet.find_nearby("99991", radius_km=5, limit=1)
    assert names(result) == ["Beratungsstelle Mitte"] and result["total_found"] == 4, result


def test_find_nearby_clamps_radius(snippet):
    assert snippet.find_nearby("99991", radius_km=10_000)["radius_km"] == 50.0
    assert snippet.find_nearby("99991", radius_km=-1)["radius_km"] == 0.1


def test_find_nearby_unresolvable_location(snippet):
    result = snippet.find_nearby("Hauptstraße")
    assert result["results"] == [] and "error" in result, result


# ---- get_awo_details on the snippet ---------------------------------------------

def test_get_awo_details_known_facility(snippet):
    details = snippet.get_awo_details(CDF + "org-near")
    assert details["name"] == "Straßenhilfe Nord"
    assert details["address"] == "Nahweg 2, 99992 Teststadt"
    assert details["email"] == "info@example.org" and details["telephone"] is None
    assert details["provider"] == "Testträger e.V."
    assert details["source_url"] == "http://www.caritas.de/adressen/x/1"
    assert details["map_url"].endswith("query=50.005%2C10.0"), details["map_url"]
    assert sorted(details["offers"], key=lambda o: o["name"]) == [
        {"name": "Allgemeine Suchtberatung", "category": "Suchtberatung"},
        {"name": "Wohnungsnothilfe", "category": "Wohnungsnotfallhilfe"},
    ]


def test_get_awo_details_facility_without_offers(snippet):
    details = snippet.get_awo_details(CDF + "org-mid")
    assert details["offers"] == [] and details["address"] == "99993 Teststadt", details


def test_get_awo_details_unknown_iri(snippet):
    assert snippet.get_awo_details(CDF + "org-does-not-exist") is None
    # a service is not a facility
    assert snippet.get_awo_details(CDF + "service-1") is None


@pytest.mark.parametrize("iri", [
    "https://example.org/org-near", CDF + 'org-near" } #', CDF + "org near", "",
])
def test_get_awo_details_rejects_malformed_iri_without_query(iri):
    assert NoQueryClient(load_graph(SNIPPET)).get_awo_details(iri) is None


# ---- find_nearby on directory.ttl vs. an independent distance check -------------

def reference_within(graph, latitude, longitude, radius_km):
    """Facilities within radius_km, computed directly from the graph's triples."""
    from rdflib import Namespace
    from rdflib.namespace import RDF

    schema = Namespace("http://schema.org/")
    org = Namespace("http://www.w3.org/ns/org#")
    found = set()
    for facility in graph.subjects(RDF.type, org.OrganizationalUnit):
        for address in graph.objects(facility, schema.address):
            lat = graph.value(address, schema.latitude)
            lon = graph.value(address, schema.longitude)
            if (address, RDF.type, schema.PostalAddress) in graph and lat and lon:
                if haversine_km(latitude, longitude, float(lat), float(lon)) <= radius_km:
                    found.add(str(facility))
    return found


@pytest.mark.parametrize("location, radius_km", [("13353", 1.0), ("52.5200,13.4050", 2.5)])
def test_find_nearby_matches_independent_distance_check(directory, location, radius_km):
    result = directory.find_nearby(location, radius_km=radius_km, limit=50)
    origin = result["origin"]
    expected = reference_within(directory.graph, origin["latitude"], origin["longitude"], radius_km)
    found = {item["iri"] for item in result["results"]}
    assert result["total_found"] == len(expected), (
        f"find_nearby({location!r}, {radius_km}) found {result['total_found']}, "
        f"reference found {len(expected)}"
    )
    assert found <= expected, f"results outside the radius: {sorted(found - expected)[:5]}"
    assert len(found) == min(len(expected), 50)
