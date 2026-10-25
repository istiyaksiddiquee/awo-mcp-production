"""
data_quality_report: a data quality scorecard per organisation that provided records,
with what is missing, what to improve and a draft message to send them.

An organisation is the provider linked to a record (schema:parentOrganization) or, when there
is no link, the website the record was taken from (schema:mainEntityOfPage, for example
www.caritas.de). Records with neither are grouped as "unknown origin". The module only reads
data. It counts and describes, it never edits or guesses values.
"""

from urllib.parse import urlparse

from src.fix_first import decode_source_link, is_missing, phone_needs_repair

UNKNOWN_ORIGIN = "unknown origin (no provider link, no source page)"

# field label -> what to ask for when it is missing
FIELDS = {
    "telephone": "a phone number",
    "email": "an email address",
    "website": "a website",
    "opening_hours": "opening hours",
    "category": "a service category",
    "street": "a street address",
    "postal_code": "a postcode",
}

# How much a missing field matters to a person looking for help. The score is the weighted
# average fill rate, and the advice lists the biggest weight x missing gaps first. The category
# is rarely filled on the facility itself (it lives mostly on the offers), so it counts little.
FIELD_WEIGHTS = {
    "telephone": 3,
    "opening_hours": 3,
    "street": 3,
    "email": 2,
    "postal_code": 2,
    "website": 1,
    "category": 0.5,
}


def origin_of(row: dict) -> str:
    """Name of the organisation a record is attributed to."""

    if not is_missing(row["provider"]):
        return str(row["provider"]).strip()

    page = decode_source_link(row["source"])
    host = urlparse(page).netloc if page else ""

    return host or UNKNOWN_ORIGIN


def field_flags(row: dict) -> dict:
    """True for every field the record has filled in."""

    return {
        "telephone": not is_missing(row["telephone"]),
        "email": row["has_email"] == "1",
        "website": row["has_homepage"] == "1",
        "opening_hours": row["has_opening_hours"] == "1",
        "category": row["has_category"] == "1",
        "street": not is_missing(row["street"]),
        "postal_code": not is_missing(row["postal_code"]),
    }


def city_spelling_problem(row: dict) -> bool:
    """A Berlin locality that is not plain 'Berlin', such as 'Berlin-Mitte' or 'Berl Berlin'."""

    city = (row["city"] or "").strip()

    return "berl" in city.casefold() and city != "Berlin"


def score_organisation(rows: list[dict]) -> dict:
    """Completeness per field, problems and a score (average field completeness, 0-100)."""

    total = len(rows)
    flags = [field_flags(row) for row in rows]

    completeness = {}
    for field in FIELDS:
        filled = sum(1 for flag in flags if flag[field])
        completeness[field] = {
            "filled": filled,
            "missing": total - filled,
            "filled_percentage": round(filled / total * 100, 1),
        }

    problems = {
        "phone_lost_leading_zero": sum(1 for row in rows if phone_needs_repair(row["telephone"])),
        "city_spelling_not_normalised": sum(1 for row in rows if city_spelling_problem(row)),
    }

    weighted = sum(FIELD_WEIGHTS[field] * c["filled_percentage"] for field, c in completeness.items())
    score = round(weighted / sum(FIELD_WEIGHTS.values()), 1)

    return {
        "facilities": total,
        "score": score,
        "score_note": "Weighted average fill rate. Phone, opening hours and street weigh most, category least.",
        "completeness": completeness,
        "problems": problems,
    }


def improvements(report: dict, limit: int = 4) -> list[str]:
    """The biggest gaps first, as short sentences a maintainer can act on."""

    gaps = sorted(
        ((FIELD_WEIGHTS[field] * c["missing"], c["missing"], field)
         for field, c in report["completeness"].items() if c["missing"]),
        reverse=True,
    )
    advice = [
        f"Add {FIELDS[field]} for {missing} of {report['facilities']} records."
        for _, missing, field in gaps[:limit]
    ]

    problems = report["problems"]
    if problems["phone_lost_leading_zero"]:
        advice.append(f"Repair {problems['phone_lost_leading_zero']} phone numbers that lost their leading zero.")
    if problems["city_spelling_not_normalised"]:
        advice.append(
            f"Use the plain city name in {problems['city_spelling_not_normalised']} addresses "
            "(for example 'Berlin' instead of 'Berlin-Mitte')."
        )

    return advice


def draft_message(name: str, report: dict, advice: list[str]) -> str:
    """A short message to the organisation. It is a draft: a person reads and sends it."""

    lines = "\n".join(f"- {item}" for item in advice) or "- No gaps found."
    # a website address is not an organisation name: greet the team behind it
    greeting = f"Hello team at {name}," if "." in name and " " not in name else f"Hello {name},"

    return (
        f"{greeting}\n\n"
        f"we checked the {report['facilities']} records attributed to your organisation in the "
        f"federated directory of social support services. Data quality score: {report['score']} of 100.\n\n"
        f"What would help most:\n{lines}\n\n"
        "People looking for help rely on these details, so every filled gap makes a difference. "
        "Thank you!"
    )


def build_quality_report(rows: list[dict], organisation: str = "", top: int = 10) -> dict:
    """Overview of all organisations (organisation empty) or the full report of one."""

    by_origin: dict[str, list[dict]] = {}
    for row in rows:
        by_origin.setdefault(origin_of(row), []).append(row)

    wanted = organisation.strip().casefold()

    if not wanted:
        overview = [
            {"organisation": name, "facilities": len(group), "score": score_organisation(group)["score"]}
            for name, group in by_origin.items()
        ]
        overview.sort(key=lambda item: (-item["facilities"], item["organisation"]))

        return {
            "total_facilities": len(rows),
            "organisations": len(by_origin),
            "showing": min(top, len(overview)),
            "overview": overview[:top],
            "note": "Call again with an organisation name for its full report and a draft message.",
        }

    matches = sorted(name for name in by_origin if wanted in name.casefold())
    if not matches:
        return {"error": f"No organisation matches '{organisation}'.", "known_organisations": sorted(by_origin)[:20]}

    name = matches[0]
    report = score_organisation(by_origin[name])
    advice = improvements(report)

    return {
        "organisation": name,
        "other_matches": matches[1:5],
        **report,
        "what_to_improve": advice,
        "draft_message": draft_message(name, report, advice),
    }
