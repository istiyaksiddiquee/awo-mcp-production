"""AWO MCP server: exposes the federated directory of social support services as MCP tools.

Each tool calls one SparqlClient method (src/sparql_client.py), which runs the queries in
sparql_queries/ against the live SPARQL endpoint. Run with `python server.py` (stdio).
"""

import json
import os
import sys

from mcp.server.mcpserver import MCPServer as FastMCP

from src.sparql_client import SparqlClient

sparql = SparqlClient()

mcp = FastMCP("AWO MCP")


def to_json(data) -> str:
    """Serialize a tool result; umlauts stay readable instead of being \\u-escaped."""

    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
def count_facilities(city: str) -> int:
    """
    Count facilities in a city in the federated directory of
    social support services in Germany (live SPARQL API).

    Args:
        city: City name, for example "Berlin".
    """

    return sparql.count_facilities(city)


@mcp.tool()
def detect_duplicates() -> str:
    """
    Detect duplicate entries in all datasets of the directory
    (facilities, addresses, services, providers).

    A duplicate group is two or more entries whose values are
    identical in every column except their IRI.

    Returns:
        JSON per dataset: number of occurrences, the IRIs of the
        duplicated entries and the shared column values.
    """

    return to_json(sparql.detect_duplicates())


@mcp.tool()
def search_facilities(city: str) -> str:
    """
    Search for facilities in a city.

    Args:
        city: City name, for example "Berlin".

    Returns:
        JSON containing matching facilities and their basic contact/address information.
    """

    return to_json(sparql.search_facilities(city))


@mcp.tool()
def get_facility_details(name: str) -> str:
    """
    Find facilities by name and return detailed information.

    Args:
        name: Full or partial facility name.

    Returns:
        JSON containing facility details, address, provider,
        and the facility's service description.
    """

    return to_json(sparql.get_facility_details(name))


@mcp.tool()
def search_services(query: str) -> str:
    """
    Search the structured service catalogue.

    Args:
        query: Search term, such as "Senioren", "Pflege", or "Beratung".

    Returns:
        JSON containing matching services.
    """

    return to_json(sparql.search_services(query))


@mcp.tool()
def check_completeness() -> str:
    """
    Check completeness of all datasets (facilities, addresses,
    services, providers).

    A value is missing when it is absent or only whitespace.

    Returns:
        JSON containing dataset-level and column-level
        completeness statistics.
    """

    return to_json(sparql.check_completeness())


@mcp.tool()
def generate_statistics() -> str:
    """
    Generate statistics for the datasets of the directory.

    Returns:
        JSON containing dataset sizes, facility statistics,
        service statistics, and relationship statistics.
    """

    return to_json(sparql.generate_statistics())


@mcp.tool()
def compare_facilities(facility_1: str, facility_2: str) -> str:
    """
    Compare two facilities by name.

    Args:
        facility_1: Full or partial name of the first facility.
        facility_2: Full or partial name of the second facility.

    Returns:
        JSON containing the details of both facilities.
    """

    return to_json(sparql.compare_facilities(facility_1, facility_2))


@mcp.tool()
def list_providers() -> str:
    """
    List all provider organisations in the federated directory.

    Returns:
        JSON containing provider names, websites, and addresses.
    """

    return to_json(sparql.list_providers())


@mcp.tool()
def search_by_postal_code(postal_code: str) -> str:
    """
    Find welfare facilities by German postal code.

    Args:
        postal_code: Five-digit German postal code, for example "10115".

    Returns:
        JSON containing facilities located in that postal code area.
    """

    return to_json(sparql.search_by_postal_code(postal_code))


if __name__ == "__main__":
    # stdout carries the MCP protocol (stdio), so log to stderr.
    print("AWO MCP server starting...", file=sys.stderr)
    if os.environ.get("MCP_TRANSPORT", "stdio") == "streamable-http":
        # Remote mode: endpoint is http://<host>:<port>/mcp
        mcp.run(
            transport="streamable-http",
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8000")),
            stateless_http=True,  # no per-session state; safe behind proxies and restarts
        )
    else:
        mcp.run()  # stdio, as before (Claude Desktop / Claude Code)
