"""SPARQL client for the federated directory of social support services (SoSuSe).

Every query lives in its own file in sparql_queries/; load_query() fills its {{placeholders}}
and SparqlClient runs it and shapes the rows into the result of one MCP tool. See QUERIES.md.
"""

import json
import math
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_ENDPOINT = "https://sosuse.correlaid.org/directory/sparql"

QUERY_DIR = Path(__file__).resolve().parent.parent / "sparql_queries"

PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

FACILITY_IRI_PREFIX = "https://civic-data.de/federated-directory#"

COORDINATES = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

POSTAL_CODE = re.compile(r"^\d{5}$")

EARTH_RADIUS_KM = 6371.0

# find_nearby searches at most this far, so a bounding-box query stays small.
MAX_RADIUS_KM = 50.0

MAX_NEARBY_RESULTS = 50

NAMESPACES = {
    "schema:": "http://schema.org/",
    "org:": "http://www.w3.org/ns/org#",
    "foaf:": "http://xmlns.com/foaf/0.1/",
    "cdf:": "https://civic-data.de/federated-directory#",
}

# Dataset name -> RDF class, duplicates query and the columns that completeness and
# duplicate checks look at. The column lists are the headers of the original CSV export
# in data/ (kept in sync by tests/unit/test_sparql_parity.py).
DATASETS = {
    "einrichtung": {
        "class": "org:OrganizationalUnit",
        "duplicates_query": "list_duplicate_facilities",
        "columns": [
            "iri", "schema:name", "schema:alternateName", "schema:telephone",
            "schema:email", "foaf:homepage", "schema:mainEntityOfPage",
            "schema:category", "schema:availableService", "schema:audience",
            "schema:accessibilitySummary", "schema:openingHours",
            "schema:parentOrganization", "cdf:parentOrgClassification",
            "schema:address",
        ],
    },
    "adresse": {
        "class": "schema:PostalAddress",
        "duplicates_query": "list_duplicate_addresses",
        "columns": [
            "iri", "schema:streetAddress", "schema:postalCode",
            "schema:addressLocality", "schema:addressCountry",
            "schema:latitude", "schema:longitude",
        ],
    },
    "angebot": {
        "class": "schema:Service",
        "duplicates_query": "list_duplicate_services",
        "columns": [
            "iri", "schema:name", "schema:category", "schema:codeValue",
            "schema:audience", "schema:areaServed", "schema:provider",
        ],
    },
    "traeger": {
        "class": "schema:Organization",
        "duplicates_query": "list_duplicate_providers",
        "columns": ["iri", "schema:name", "foaf:homepage", "schema:address"],
    },
}


def escape_literal(value: str) -> str:
    """Escape a Python string for use inside a double-quoted SPARQL literal."""

    replacements = {
        "\\": "\\\\",
        '"': '\\"',
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }

    return "".join(replacements.get(char, char) for char in value)


def expand(prefixed: str) -> str:
    """Expand a prefixed name such as "schema:name" to a full IRI."""

    for prefix, namespace in NAMESPACES.items():
        if prefixed.startswith(prefix):
            return namespace + prefixed[len(prefix):]

    return prefixed


def column_variable(column: str) -> str:
    """Variable name a duplicates query uses for a column ("schema:postalCode" -> "postal_code")."""

    local = column.split(":", 1)[1]

    return re.sub(r"(?<!^)(?=[A-Z])", "_", local).lower()


def rows(result: dict) -> list[dict]:
    """
    Flatten SPARQL JSON bindings to {variable: value} dicts.

    Unbound variables and empty GROUP_CONCAT results become None.
    """

    return [
        {
            variable: (binding[variable]["value"] or None)
            if variable in binding
            else None
            for variable in result["head"]["vars"]
        }
        for binding in result["results"]["bindings"]
    ]


def percentage(part: int, whole: int) -> float:
    """part / whole in percent, rounded to 2 decimals (0.0 when whole is 0)."""

    return round(part / whole * 100, 2) if whole else 0.0


def to_float(value: str | None) -> float | None:
    """Convert a coordinate literal to float (None stays None)."""

    return float(value) if value is not None else None


def split_values(value: str | None) -> list[str]:
    """Split a "; "-joined GROUP_CONCAT value into a list (None -> [])."""

    return [part.strip() for part in (value or "").split(";") if part.strip()]


def casefold_de(text: str) -> str:
    """Casefold like the queries do: lower case and ß -> ss."""

    return text.casefold().replace("ß", "ss")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""

    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])

    a = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )

    return EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def bounding_box(latitude: float, longitude: float, radius_km: float) -> str:
    """
    The "south,west,north,east" box around a point that contains every point within
    radius_km, as list_facilities_by_bbox.rq expects it.
    """

    lat_delta = math.degrees(radius_km / EARTH_RADIUS_KM)
    lon_delta = lat_delta / max(math.cos(math.radians(latitude)), 1e-6)

    # Round outward to 6 decimals so the box never cuts into the circle.
    def down(value: float) -> str:
        return f"{math.floor(value * 1e6) / 1e6:.6f}"

    def up(value: float) -> str:
        return f"{math.ceil(value * 1e6) / 1e6:.6f}"

    return ",".join(
        [
            down(latitude - lat_delta),
            down(longitude - lon_delta),
            up(latitude + lat_delta),
            up(longitude + lon_delta),
        ]
    )


def accessibility_flag(summary: str | None) -> bool | None:
    """Read the accessibility sentence as True, False, or None when unknown."""

    text = (summary or "").casefold()

    if "nicht barrierefrei" in text:
        return False

    if "barrierefrei" in text:
        return True

    return None


def source_page(value: str | None) -> str | None:
    """
    The facility's page in its source directory.

    Older Caritas records store a relative redirect ("/search.ashx?...&u=<url>"),
    so the real URL is taken from its "u" parameter.
    """

    if not value:
        return None

    if value.startswith(("http://", "https://")):
        return value

    if value.startswith("/search.ashx"):
        target = urllib.parse.parse_qs(urllib.parse.urlparse(value).query).get("u", [""])[0]
        return target.split("?")[0] or None

    return None


def map_link(latitude: float | None, longitude: float | None, fallback: str) -> str | None:
    """Google Maps link for coordinates, or a text search when they are missing."""

    query = f"{latitude},{longitude}" if latitude is not None and longitude is not None else fallback

    if not query:
        return None

    return "https://www.google.com/maps/search/?api=1&query=" + urllib.parse.quote(query)


def format_address(street: str | None, postal_code: str | None, city: str | None) -> str | None:
    """ "Street, Postal code City", leaving out missing parts."""

    locality = " ".join(part for part in [postal_code, city] if part)

    return ", ".join(part for part in [street, locality] if part) or None


def matches_terms(texts: list[str | None], query: str | None) -> bool:
    """
    True when any comma-separated term in query appears in the texts.

    Matching is a casefolded substring, like the queries. An empty query matches.
    """

    terms = [casefold_de(term.strip()) for term in (query or "").split(",") if term.strip()]

    if not terms:
        return True

    text = casefold_de(" ".join(value for value in texts if value))

    return any(term in text for term in terms)


def load_query(query_name: str, /, **params: str) -> str:
    """
    Load sparql_queries/<query_name>.rq and fill its {{placeholders}}.

    Values are escaped as SPARQL string literals.
    """

    template = (QUERY_DIR / f"{query_name}.rq").read_text(encoding="utf-8")

    missing = sorted(set(PLACEHOLDER.findall(template)) - set(params))
    if missing:
        raise ValueError(f"query '{query_name}' needs parameter(s) {missing}")

    return PLACEHOLDER.sub(
        lambda match: escape_literal(str(params[match.group(1)])),
        template,
    )


class SparqlClient:
    """Query the federated directory of social support services over SPARQL."""

    def __init__(self, endpoint: str | None = None, timeout: float = 30):
        self.endpoint = endpoint or os.environ.get(
            "SPARQL_ENDPOINT", DEFAULT_ENDPOINT
        )
        self.timeout = timeout

    def query(self, sparql: str) -> dict:
        """Run a SPARQL query and return the parsed JSON result."""

        url = self.endpoint + "?" + urllib.parse.urlencode({"query": sparql})
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/sparql-results+json"},
        )

        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.load(response)

    def select(self, query_name: str, /, **params: str) -> list[dict]:
        """Run sparql_queries/<query_name>.rq and return its rows."""

        return rows(self.query(load_query(query_name, **params)))

    def value_counts(self, query_name: str) -> dict:
        """Run a count_*_per_* query and return {value: count}."""

        return {
            row["value"]: int(row["count"])
            for row in self.select(query_name)
        }

    def count_facilities(self, city: str) -> int:
        """Count facilities in a city."""

        result = self.select("count_facilities_by_city", city=city.casefold())

        return int(result[0]["count"]) if result else 0

    def search_facilities(self, city: str) -> list[dict]:
        """Find facilities in a city."""

        return [
            {
                "iri": row["facility"],
                "name": row["name"],
                "street": row["street"],
                "postal_code": row["postal_code"],
                "city": row["city"],
                "telephone": row["telephone"],
                "email": row["email"],
                "homepage": row["homepage"],
            }
            for row in self.select("list_facilities_by_city", city=city.casefold())
        ]

    def get_facility_details(self, name: str) -> list[dict]:
        """Find facilities by name and return their details."""

        results = []

        for row in self.select("list_facility_details_by_name", name=name.casefold()):
            result = {
                "iri": row["facility"],
                "name": row["name"],
                "alternate_name": row["alternate_name"],
                "telephone": row["telephone"],
                "email": row["email"],
                "homepage": row["homepage"],
                "category": row["category"],
                "service_description": row["service_description"],
            }

            if row["address"]:
                result["address"] = {
                    "street": row["street"],
                    "postal_code": row["postal_code"],
                    "city": row["city"],
                    "country": row["country"],
                    "latitude": to_float(row["latitude"]),
                    "longitude": to_float(row["longitude"]),
                }

            if row["provider"]:
                result["provider"] = {
                    "iri": row["provider"],
                    "name": row["provider_name"],
                    "homepage": row["provider_homepage"],
                }

            results.append(result)

        return results

    def search_services(self, query: str) -> list[dict]:
        """Search the structured service catalogue."""

        return [
            {
                "iri": row["service"],
                "schema:name": row["name"],
                "schema:category": row["category"],
                "schema:audience": row["audience"],
            }
            for row in self.select("list_services_by_query", query=query.casefold())
        ]

    def detect_duplicates(self) -> dict:
        """
        Detect duplicate entities in all datasets.

        A duplicate group is two or more entities whose values are
        identical in every column except the IRI.
        """

        duplicates = {}

        for dataset_name, dataset in DATASETS.items():
            groups = [
                {
                    "occurrences": int(row["occurrences"]),
                    "iris": row["iris"].split(" "),
                    "row": {
                        column: row[column_variable(column)]
                        for column in dataset["columns"][1:]
                    },
                }
                for row in self.select(dataset["duplicates_query"])
            ]

            if groups:
                duplicates[dataset_name] = groups

        return duplicates

    def dataset_sizes(self) -> dict:
        """Number of entities per dataset."""

        counts = {
            row["class"]: int(row["count"])
            for row in self.select("count_entities_per_class")
        }

        return {
            dataset_name: counts.get(expand(dataset["class"]), 0)
            for dataset_name, dataset in DATASETS.items()
        }

    def check_completeness(self) -> dict:
        """
        Check data completeness for all datasets.

        A value is missing when the property is absent or only whitespace.
        """

        sizes = self.dataset_sizes()

        filled = {
            (row["class"], row["property"]): int(row["filled"])
            for row in self.select("count_filled_properties_per_class")
        }

        completeness = {}

        for dataset_name, dataset in DATASETS.items():
            total_rows = sizes[dataset_name]
            class_iri = expand(dataset["class"])
            columns = {}

            for column in dataset["columns"]:
                if column == "iri":
                    missing = 0
                else:
                    missing = total_rows - filled.get((class_iri, expand(column)), 0)

                columns[column] = {
                    "total": total_rows,
                    "missing": missing,
                    "missing_percentage": percentage(missing, total_rows),
                }

            total_cells = total_rows * len(dataset["columns"])
            total_missing = sum(column["missing"] for column in columns.values())

            completeness[dataset_name] = {
                "rows": total_rows,
                "columns": len(dataset["columns"]),
                "total_cells": total_cells,
                "missing_cells": total_missing,
                "missing_percentage": percentage(total_missing, total_cells),
                "columns_detail": columns,
            }

        return completeness

    def generate_statistics(self) -> dict:
        """Generate statistics for the directory."""

        links = self.select("count_facility_links")[0]
        total = int(links["total"])
        with_address = int(links["with_address"])
        with_provider = int(links["with_provider"])

        return {
            "dataset_sizes": self.dataset_sizes(),
            "facilities_by_city": self.value_counts("count_facilities_per_city"),
            "facilities_by_category": self.value_counts("count_facilities_per_category"),
            "facilities_by_provider": self.value_counts("count_facilities_per_provider"),
            "facility_relationships": {
                "with_address": with_address,
                "without_address": total - with_address,
                "with_provider": with_provider,
                "without_provider": total - with_provider,
            },
            "services_by_category": self.value_counts("count_services_per_category"),
            "services_by_audience": self.value_counts("count_services_per_audience"),
        }

    def list_providers(self) -> list[dict]:
        """List all provider organisations."""

        return [
            {
                "iri": row["provider"],
                "name": row["name"],
                "homepage": row["homepage"],
                "address": {
                    "street": row["street"],
                    "postal_code": row["postal_code"],
                    "city": row["city"],
                },
            }
            for row in self.select("list_providers")
        ]

    def search_by_postal_code(self, postal_code: str) -> list[dict]:
        """Find facilities by German postal code."""

        return [
            {
                "iri": row["facility"],
                "name": row["name"],
                "street": row["street"],
                "postal_code": row["postal_code"],
                "city": row["city"],
                "telephone": row["telephone"],
                "email": row["email"],
                "homepage": row["homepage"],
            }
            for row in self.select("list_facilities_by_postal_code", postal_code=postal_code)
        ]

    def facility_repository(self, city: str):
        """Facilities of a city plus their addresses and providers, shaped for src/fix_first.py."""

        import pandas as pd

        from src.fix_first import FixFirstRepository

        columns = DATASETS["einrichtung"]["columns"]
        # column -> query variable (the query joins multi-valued fields with "; ")
        variables = {
            "iri": "facility",
            "schema:name": "name",
            "schema:alternateName": "alternate_name",
            "schema:telephone": "telephone",
            "schema:email": "email",
            "foaf:homepage": "homepage",
            "schema:mainEntityOfPage": "main_page",
            "schema:category": "category",
            "schema:availableService": "service_description",
            "schema:audience": "audience",
            "schema:accessibilitySummary": "accessibility",
            "schema:openingHours": "opening_hours",
            "schema:parentOrganization": "provider",
            "cdf:parentOrgClassification": "parent_classification",
            "schema:address": "address",
        }

        facilities = self.select("list_facility_quality_fields_by_city", city=city.casefold())

        address_by_iri = {
            row["address"]: {
                "schema:streetAddress": row["street"],
                "schema:postalCode": row["postal_code"],
                "schema:addressLocality": row["city"],
            }
            for row in facilities
            if row["address"]
        }
        provider_by_iri = {
            provider["iri"]: {
                "iri": provider["iri"],
                "schema:name": provider["name"],
                "foaf:homepage": provider["homepage"],
            }
            for provider in self.list_providers()
        }
        einrichtung = pd.DataFrame(
            [{column: row[variables[column]] for column in columns} for row in facilities],
            columns=columns,
            dtype=object,
        )

        return FixFirstRepository(einrichtung, address_by_iri, provider_by_iri)

    def records_to_fix_first(self, city: str, **options) -> dict:
        """Rank the facilities of a city by how much fixing their record matters."""

        from src.fix_first import records_to_fix_first

        result = records_to_fix_first(self.facility_repository(city), **options)
        result["summary"] = {"city": city, **result["summary"]}

        return result

    def compare_facilities(self, facility_1: str, facility_2: str) -> list[dict]:
        """Compare two facilities by name."""

        return [
            {
                "search_name": name,
                "matches": self.get_facility_details(name),
            }
            for name in [facility_1, facility_2]
        ]

    def resolve_location(self, location: str) -> dict | None:
        """
        Turn a location into search coordinates.

        Accepts "latitude,longitude" or a five-digit postal code (the average position
        of the addresses with that code). Returns None when it can't be resolved.
        """

        match = COORDINATES.match(location)

        if match:
            latitude, longitude = float(match.group(1)), float(match.group(2))

            if -90 <= latitude <= 90 and -180 <= longitude <= 180:
                return {
                    "latitude": latitude,
                    "longitude": longitude,
                    "matched_by": "coordinates",
                }

            return None

        postal_code = location.strip()

        if not POSTAL_CODE.match(postal_code):
            return None

        result = self.select("get_postal_code_location_by_postal_code", postal_code=postal_code)

        if not result or int(result[0]["addresses"]) == 0:
            return None

        return {
            "latitude": float(result[0]["latitude"]),
            "longitude": float(result[0]["longitude"]),
            "matched_by": "postal_code",
            "matched_addresses": int(result[0]["addresses"]),
        }

    def find_nearby(
        self,
        location: str,
        radius_km: float = 2.0,
        query: str | None = None,
        accessible_only: bool = False,
        limit: int = 10,
    ) -> dict:
        """Find facilities within radius_km of a location, nearest first."""

        radius_km = min(max(float(radius_km), 0.1), MAX_RADIUS_KM)
        limit = min(max(int(limit), 1), MAX_NEARBY_RESULTS)

        origin = self.resolve_location(location)

        if origin is None:
            return {
                "location": location,
                "error": (
                    "Could not resolve the location. Use 'latitude,longitude' "
                    "(e.g. '52.5446,13.3592') or a five-digit postal code."
                ),
                "results": [],
            }

        bbox = bounding_box(origin["latitude"], origin["longitude"], radius_km)
        results = []

        for row in self.select("list_facilities_by_bbox", bbox=bbox):
            latitude, longitude = to_float(row["latitude"]), to_float(row["longitude"])
            accessible = accessibility_flag(row["accessibility"])

            if accessible_only and accessible is not True:
                continue

            if not matches_terms(
                [
                    row["name"], row["alternate_name"], row["category"], row["audience"],
                    row["service_description"], row["offers"], row["offer_categories"],
                ],
                query,
            ):
                continue

            distance = haversine_km(origin["latitude"], origin["longitude"], latitude, longitude)

            if distance > radius_km:
                continue

            results.append(
                {
                    "iri": row["facility"],
                    "name": row["name"],
                    "distance_km": round(distance, 2),
                    "address": format_address(row["street"], row["postal_code"], row["city"]),
                    "category": row["category"],
                    "audience": split_values(row["audience"]),
                    "offers": split_values(row["offers"]),
                    "accessible": accessible,
                    "telephone": row["telephone"],
                    "email": row["email"],
                    "homepage": row["homepage"],
                    "opening_hours": row["opening_hours"],
                    "latitude": latitude,
                    "longitude": longitude,
                }
            )

        results.sort(key=lambda result: (result["distance_km"], result["iri"]))

        return {
            "location": location,
            "origin": origin,
            "radius_km": radius_km,
            "total_found": len(results),
            "results": results[:limit],
        }

    def get_awo_details(self, iri: str) -> dict | None:
        """
        Address, map link, contacts, opening hours, offers and source link for one
        facility, by IRI. Returns None for an unknown or malformed IRI.
        """

        iri = iri.strip()

        if not iri.startswith(FACILITY_IRI_PREFIX) or re.search(r"[\s<>\"{}|\\^`]", iri):
            return None

        result = self.select("get_facility_details_by_iri", iri=iri)

        if not result:
            return None

        row = result[0]
        latitude, longitude = to_float(row["latitude"]), to_float(row["longitude"])
        address = format_address(row["street"], row["postal_code"], row["city"])

        offers = []
        for offer in split_values(row["offers"]):
            name, _, category = (part.strip() for part in offer.partition("|"))
            if name or category:
                offers.append(
                    {
                        "name": name or category,
                        "category": category if name and category != name else None,
                    }
                )

        return {
            "iri": row["facility"],
            "name": row["name"],
            "alternate_name": row["alternate_name"],
            "category": row["category"],
            "address": address,
            "map_url": map_link(
                latitude, longitude, ", ".join(part for part in [row["name"], address] if part)
            ),
            "telephone": row["telephone"],
            "email": row["email"],
            "homepage": row["homepage"],
            "source_url": source_page(row["source_url"]),
            "opening_hours": row["opening_hours"],
            "accessibility": row["accessibility"],
            "offers": offers,
            "provider": row["provider_name"],
            "provider_type": row["provider_type"],
        }
