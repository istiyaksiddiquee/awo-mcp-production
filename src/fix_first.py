"""
records_to_fix_first: prioritise which AWO facility records a data
maintainer should work on first, and explain why.

The module only reads data. It flags and ranks, it never edits or
guesses values.
"""

import re
from urllib.parse import parse_qs, urlparse

from dataclasses import dataclass

import pandas as pd


@dataclass
class FixFirstRepository:
    """The data the ranking works on: facility table, addresses and providers by IRI."""

    einrichtung: pd.DataFrame
    address_by_iri: dict
    provider_by_iri: dict


# --------------------------------------------------
# Configuration (can be tuned)
# --------------------------------------------------

# How much a missing field matters. Higher means more important to fill.
FIELD_WEIGHTS = {
    "schema:telephone": 3.0,
    "schema:email": 3.0,
    "schema:openingHours": 3.0,
    "schema:address": 3.0,  # missing IRI or missing street
    "schema:category": 1.5,
    "schema:availableService": 1.5,
    "schema:parentOrganization": 1.5,
    "schema:audience": 1.0,
    "schema:accessibilitySummary": 1.0,
    "foaf:homepage": 1.0,
    "schema:alternateName": 0.5,
}

# Fields that make a record "informative" (used to tell rich from bare).
INFO_FIELDS = [
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
]

# A record with this many informative fields or fewer counts as bare.
BARE_MAX_INFO = 3

# The three fields users need most.
KEY_FIELDS = [
    "schema:telephone",
    "schema:email",
    "schema:openingHours",
]

# Bucket multipliers. They decide how high a bucket ranks by default.
BUCKET_MULTIPLIER = {
    "complete_key_field": 1.0,
    "repair": 1.0,
    "ask_provider": 0.9,
    "enrich_from_source": 0.8,
    "scope_check": 0.6,
    "junk_or_duplicate": 0.6,
    "needs_research": 0.4,
    "minor_gaps": 0.3,
}

# Score bands for the priority tier shown to users. The raw score is only
# shown on request, because small score gaps are not meaningful.
PRIORITY_HIGH_MIN = 18.0
PRIORITY_MEDIUM_MIN = 9.0

# Church bodies and associations that are not services people can use.
OUT_OF_SCOPE_PATTERN = re.compile(
    r"ordinariat|di[oö]zes|kapitel|bisch[oö]f|kirchengemeind|pfarrei|"
    r"klima-kollekte|krankenhausverband|arbeitsrechtliche kommission|"
    r"bundesarbeitsgemeinschaft|mitarbeiterseite|verband katholischer|"
    r"verband kath\.|deutscher caritasverband|caritas-konferenzen",
    re.IGNORECASE,
)

# Services people may urgently depend on (addiction, homelessness, crisis).
# Records matching this get an impact bonus so they rank above routine ones.
URGENT_PATTERN = re.compile(
    r"sucht|drogen|wohnungslos|obdach|[nN]ot[uü]bernacht|k[aä]ltehilfe|"
    r"krisen|bahnhofsmission|substitu|schuldner",
    re.IGNORECASE,
)

BUCKET_NEXT_STEP = {
    "ask_provider": (
        "Ask the provider for phone, email and opening hours. One request "
        "can cover several of their facilities."
    ),
    "scope_check": (
        "Check whether this organisation offers services to the public. "
        "If not, consider removing it from the pool."
    ),
    "junk_or_duplicate": "Review this record and merge or remove it if confirmed.",
    "repair": "Correct the flagged value so it is usable.",
    "complete_key_field": (
        "Ask the provider or check their website for the missing fields."
    ),
    "enrich_from_source": (
        "Open the source page and copy phone, email and opening hours."
    ),
    "needs_research": (
        "No lead on record. Look the facility up by name and address."
    ),
    "minor_gaps": "Low priority. Only minor fields are missing.",
}

FIELD_LABELS = {
    "schema:telephone": "telephone",
    "schema:email": "email",
    "schema:openingHours": "opening hours",
    "schema:address": "address",
    "schema:category": "category",
    "schema:availableService": "services",
    "schema:parentOrganization": "provider",
    "schema:audience": "audience",
    "schema:accessibilitySummary": "accessibility",
    "foaf:homepage": "homepage",
    "schema:alternateName": "alternate name",
}


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def is_missing(value) -> bool:
    """NaN, None or blank string, same rule as check_completeness."""

    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):
        pass
    return str(value).strip() == ""


def decode_source_link(link):
    """
    Turn a Caritas search link such as
    /search.ashx?...&u=http%3a%2f%2fwww.caritas.de%2f...
    into the real page URL. Returns None if there is nothing usable.
    """

    if is_missing(link):
        return None

    link = str(link).strip()

    if link.startswith("http"):
        return link

    query = parse_qs(urlparse(link).query)
    decoded = query.get("u", [None])[0]

    return decoded


def normalise_name(name) -> str:
    """Lowercase, drop bracketed parts and punctuation, collapse spaces."""

    if is_missing(name):
        return ""

    text = str(name).casefold()
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def phone_needs_repair(value) -> bool:
    """A phone stored as a number has lost its leading zero."""

    if is_missing(value):
        return False

    if isinstance(value, (int, float)):
        return True

    text = str(value).strip()
    return not text.startswith(("0", "+"))


# --------------------------------------------------
# Row analysis
# --------------------------------------------------

def address_missing(repository, row) -> bool:
    iri = row["schema:address"]

    if is_missing(iri):
        return True

    address = repository.address_by_iri.get(iri)

    if not address:
        return True

    return is_missing(address.get("schema:streetAddress"))


def missing_fields(repository, row) -> list[str]:
    """Return the weighted fields that are missing for this record."""

    missing = []

    for field in FIELD_WEIGHTS:
        if field == "schema:address":
            if address_missing(repository, row):
                missing.append(field)
        elif is_missing(row[field]):
            missing.append(field)

    return missing


def info_count(row) -> int:
    return sum(0 if is_missing(row[f]) else 1 for f in INFO_FIELDS)


def provider_hint(row):
    """Guess a provider from the name when no provider link exists."""

    if not is_missing(row["schema:parentOrganization"]):
        return None

    name = "" if is_missing(row["schema:name"]) else str(row["schema:name"])

    if re.search(r"\bawo\b", name, re.IGNORECASE):
        return "AWO"

    if re.search(r"\bspi\b", name, re.IGNORECASE):
        return "Stiftung SPI"

    return None


def address_key(repository, row):
    """Street plus postal code, or None when the address is unknown."""

    address = repository.address_by_iri.get(row["schema:address"])

    if not address or is_missing(address.get("schema:streetAddress")):
        return None

    return (
        str(address["schema:streetAddress"]).casefold().strip(),
        str(address.get("schema:postalCode")).strip(),
    )


def resolve_provider_by_name(repository, row):
    """
    Match a known provider (from the provider table) when the facility
    name contains all of the provider's name words. Only used when the
    record has no provider link.
    """

    if not is_missing(row["schema:parentOrganization"]):
        return None

    if is_missing(row["schema:name"]):
        return None

    facility_words = set(normalise_name(row["schema:name"]).split())
    ignore = {"e", "v", "gmbh", "ggmbh", "mbh"}

    for provider in repository.provider_by_iri.values():
        words = set(normalise_name(provider.get("schema:name")).split()) - ignore

        if len(words) >= 3 and words <= facility_words:
            return provider

    return None


def find_near_duplicates(repository) -> dict:
    """
    Map record index to the name of the record it likely duplicates.
    Two records only count as duplicates when the normalised names match
    and the addresses match or at least one address is unknown. Generic
    names at different addresses are treated as different facilities.
    The record with the most informative fields is kept as the original.
    """

    einrichtung = repository.einrichtung

    groups = {}

    for index, row in einrichtung.iterrows():
        key = normalise_name(row["schema:name"])

        if not key:
            continue

        groups.setdefault(key, []).append(index)

    duplicates = {}

    for indexes in groups.values():
        if len(indexes) < 2:
            continue

        keeper = max(
            indexes,
            key=lambda i: (info_count(einrichtung.loc[i]), -i),
        )

        keeper_address = address_key(repository, einrichtung.loc[keeper])

        for index in indexes:
            if index == keeper:
                continue

            other_address = address_key(repository, einrichtung.loc[index])

            same_place = (
                keeper_address is None
                or other_address is None
                or keeper_address == other_address
            )

            if same_place:
                duplicates[index] = einrichtung.loc[keeper, "schema:name"]

    return duplicates


def detect_signals(row, duplicates, index) -> dict:
    name = "" if is_missing(row["schema:name"]) else str(row["schema:name"])

    return {
        "out_of_scope": bool(OUT_OF_SCOPE_PATTERN.search(name)),
        "junk": is_missing(row["schema:name"]),
        "phone_format": phone_needs_repair(row["schema:telephone"]),
        "name_has_address": bool(re.search(r"\b\d{5}\b", name)),
        "provider_hint": provider_hint(row),
        "near_duplicate_of": duplicates.get(index),
    }


def assign_bucket(repository, row, signals, missing, lead):
    """Return the bucket and the reasons that put the record there."""

    info = info_count(row)
    is_bare = info <= BARE_MAX_INFO
    reasons = []

    if signals["out_of_scope"] and is_bare:
        reasons.append(
            "Name looks like a church body or association, not a service "
            "people can contact for help."
        )
        return "scope_check", reasons

    if signals["junk"]:
        reasons.append("Record has no name.")
        return "junk_or_duplicate", reasons

    if signals["near_duplicate_of"]:
        reasons.append(
            f"Name matches another record: {signals['near_duplicate_of']}."
        )
        return "junk_or_duplicate", reasons

    # A phone without its leading zero affects every record that has a
    # phone, so it is reported once in the summary and added as a note
    # on each record. It does not decide the bucket.

    if signals["name_has_address"]:
        reasons.append("The name contains a street address or postal code.")
        return "repair", reasons

    if not is_bare:
        key_missing = [
            FIELD_LABELS[f]
            for f in KEY_FIELDS + ["schema:address"]
            if f in missing
        ]

        if key_missing:
            reasons.append(
                "Informative record, but missing: " + ", ".join(key_missing) + "."
            )
            return "complete_key_field", reasons

        reasons.append("Key fields are present. Only minor fields are missing.")
        return "minor_gaps", reasons

    if lead:
        reasons.append(
            "Almost empty, but a source page is on record to copy data from."
        )
        return "enrich_from_source", reasons

    if not is_missing(row["schema:parentOrganization"]) or signals["provider_hint"]:
        reasons.append(
            "Almost empty, but the provider is known or suggested, so they "
            "can be asked directly."
        )
        return "ask_provider", reasons

    reasons.append("Almost empty and there is no source or provider to ask.")
    return "needs_research", reasons


def is_urgent(row) -> bool:
    """True for services people may urgently depend on."""

    text = " ".join(
        str(row[f]) for f in
        ("schema:name", "schema:category", "schema:availableService")
        if not is_missing(row[f])
    )

    return bool(URGENT_PATTERN.search(text))


def priority_tier(score: float) -> str:
    """Turn a score into high, medium or low."""

    if score >= PRIORITY_HIGH_MIN:
        return "high"

    if score >= PRIORITY_MEDIUM_MIN:
        return "medium"

    return "low"


def score_details(row, missing, bucket, signals, lead) -> dict:
    """
    Score a record and keep the parts, so the score can be explained:
    score = missing weight x (1 + sum of bonuses) x bucket multiplier.
    """

    weight_sum = sum(FIELD_WEIGHTS[f] for f in missing)

    bonuses = {}

    # Impact: people may rely on records that list services.
    if not is_missing(row["schema:availableService"]) or not is_missing(
        row["schema:audience"]
    ):
        bonuses["lists services or audience"] = 0.3

    # Impact: urgent help (addiction, homelessness, crisis services).
    if is_urgent(row):
        bonuses["urgent help (addiction, homelessness, crisis, debt)"] = 0.4

    # Fixability: there is somewhere to look.
    if not is_missing(row["foaf:homepage"]):
        bonuses["has a homepage"] = 0.3
    if lead:
        bonuses["has a source page"] = 0.2
    if not is_missing(row["schema:parentOrganization"]) or signals["provider_hint"]:
        bonuses["provider known or suggested"] = 0.2

    bonus_factor = round(1.0 + sum(bonuses.values()), 2)
    multiplier = BUCKET_MULTIPLIER[bucket]

    return {
        "score": round(weight_sum * bonus_factor * multiplier, 1),
        "missing_fields_weight": round(weight_sum, 1),
        "bonuses": bonuses,
        "bonus_factor": bonus_factor,
        "bucket_multiplier": multiplier,
        "formula": (
            "score = missing_fields_weight x bonus_factor x bucket_multiplier"
        ),
    }


def next_step_for(bucket, lead, missing) -> str:
    """Suggested action, using the source page when there is one."""

    if bucket == "complete_key_field" and lead:
        wanted = [
            FIELD_LABELS[f]
            for f in KEY_FIELDS + ["schema:address"]
            if f in missing
        ]
        return "Open the source page and look up: " + ", ".join(wanted) + "."

    return BUCKET_NEXT_STEP[bucket]


# --------------------------------------------------
# Main analysis
# --------------------------------------------------

def analyse_all(repository) -> list[dict]:
    """Analyse every facility and return one result dict per record."""

    einr = repository.einrichtung
    duplicates = find_near_duplicates(repository)

    results = []

    for index, row in einr.iterrows():
        signals = detect_signals(row, duplicates, index)
        missing = missing_fields(repository, row)
        lead = decode_source_link(row["schema:mainEntityOfPage"])

        bucket, reasons = assign_bucket(
            repository, row, signals, missing, lead
        )

        details = score_details(row, missing, bucket, signals, lead)
        score = details["score"]

        provider = repository.provider_by_iri.get(
            row["schema:parentOrganization"]
        )
        provider_name = provider.get("schema:name") if provider else None

        # No provider link: try to match a known provider from the name.
        if not provider:
            matched = resolve_provider_by_name(repository, row)

            if matched:
                provider = matched
                provider_name = matched.get("schema:name")
                reasons.append(
                    f"Provider matched from the name: {provider_name} "
                    "(no provider link on record)."
                )

        # For "ask the provider", the provider's homepage is the lead.
        if bucket == "ask_provider" and not lead and provider:
            homepage = provider.get("foaf:homepage")
            lead = None if is_missing(homepage) else homepage

        address = repository.address_by_iri.get(row["schema:address"])
        city = address.get("schema:addressLocality") if address else None

        if signals["provider_hint"]:
            reasons.append(
                f"Name suggests provider '{signals['provider_hint']}' "
                "but no provider link exists."
            )

        if signals["phone_format"]:
            reasons.append(
                "Telephone is stored without its leading zero (source data "
                "issue, see summary)."
            )

        results.append(
            {
                "name": None if is_missing(row["schema:name"]) else row["schema:name"],
                "iri": row["iri"],
                "city": None if is_missing(city) else city,
                "provider": provider_name,
                "provider_hint": signals["provider_hint"],
                "phone_needs_repair": signals["phone_format"],
                "urgent_help": is_urgent(row),
                "bucket": bucket,
                "priority": priority_tier(score),
                "score": score,
                "score_details": details,
                "missing": [FIELD_LABELS[f] for f in missing],
                "missing_fields": missing,
                "reasons": reasons,
                "lead": lead,
                "next_step": next_step_for(bucket, lead, missing),
            }
        )

    return results


def coverage_gain(repository, selected: list[dict]) -> dict:
    """
    Projected coverage of the three key fields if the selected records
    were completely filled. Scope and junk records are excluded.
    This is a projection, not a measured improvement.
    """

    einr = repository.einrichtung
    total = len(einr)

    fixable = [
        r for r in selected
        if r["bucket"] not in ("scope_check", "junk_or_duplicate")
    ]

    gain = {}

    if total == 0:
        return {"note": "No facilities, nothing to project.", "records_counted": 0, "fields": {}}

    for field in KEY_FIELDS:
        present = int(sum(0 if is_missing(v) else 1 for v in einr[field]))

        newly_filled = sum(
            1 for r in fixable if field in r["missing_fields"]
        )

        gain[FIELD_LABELS[field]] = {
            "before": f"{present} of {total} ({present / total:.1%})",
            "after": (
                f"{present + newly_filled} of {total} "
                f"({(present + newly_filled) / total:.1%})"
            ),
        }

    return {
        "note": (
            "Projection: assumes the selected records get these fields "
            "filled. Scope check and junk records are excluded."
        ),
        "records_counted": len(fixable),
        "fields": gain,
    }


def group_by_provider(
    repository, selected: list[dict], explain: bool = False
) -> dict:
    """
    Batch records by who can fix them, for outreach. Scope check and
    junk records are left out, since nobody should be asked to fill them.
    Each batch has its own projected coverage gain.
    """

    fixable = [
        r for r in selected
        if r["bucket"] not in ("scope_check", "junk_or_duplicate")
    ]

    einr = repository.einrichtung
    total = len(einr)

    present = {
        field: int(sum(0 if is_missing(v) else 1 for v in einr[field]))
        for field in KEY_FIELDS
    }

    groups = {}

    for record in fixable:
        if record["provider"]:
            key = record["provider"]
        elif record["provider_hint"]:
            key = f"Unassigned (name suggests {record['provider_hint']})"
        else:
            key = "Unassigned (owner unknown)"

        groups.setdefault(key, []).append(record)

    batches = []

    for key, records in groups.items():
        fields = {}

        for record in records:
            for label in record["missing"]:
                fields[label] = fields.get(label, 0) + 1

        projected = {}

        for field in KEY_FIELDS:
            newly = sum(1 for r in records if field in r["missing_fields"])
            after = present[field] + newly

            projected[FIELD_LABELS[field]] = (
                f"{present[field]} to {after} of {total} ({after / total:.1%})"
            )

        if key.startswith("Unassigned (owner unknown)"):
            priority = 2
        elif key.startswith("Unassigned"):
            priority = 1
        else:
            priority = 0

        batches.append(
            {
                "group": key,
                "can_be_asked_directly": priority < 2,
                "facilities": len(records),
                "total_score": round(sum(r["score"] for r in records), 1),
                "high_priority_facilities": sum(
                    1 for r in records if r["priority"] == "high"
                ),
                "missing_counts": dict(
                    sorted(fields.items(), key=lambda kv: -kv[1])
                ),
                "projected_coverage_if_filled": (
                    projected
                    if priority < 2
                    else "Not shown: no single owner can fill these."
                ),
                "examples": [r["name"] for r in records[:8]],
                "_priority": priority,
            }
        )

    batches.sort(key=lambda b: (b["_priority"], -b["total_score"]))

    for batch in batches:
        batch.pop("_priority")

        if not explain:
            batch.pop("total_score")

    return {
        "excluded_scope_check_or_junk": len(selected) - len(fixable),
        "batches": batches,
    }


def records_to_fix_first(
    repository,
    provider: str | None = None,
    n: int = 10,
    bucket: str | None = None,
    group_by: str | None = None,
    focus: str | None = None,
    explain: bool = False,
) -> dict:
    """
    Rank facility records by how much fixing them matters.

    Each record gets a priority tier (high, medium, low). The numeric
    score and how it was calculated are only included when explain=True.
    """

    all_results = analyse_all(repository)

    results = all_results

    if provider:
        wanted = provider.casefold().strip()

        if wanted == "unassigned":
            results = [r for r in results if not r["provider"]]
        else:
            results = [
                r for r in results
                if r["provider"] and wanted in r["provider"].casefold()
            ]

    if bucket:
        results = [r for r in results if r["bucket"] == bucket]

    if focus and focus.casefold().strip() == "urgent":
        results = [r for r in results if r["urgent_help"]]

    results = sorted(results, key=lambda r: (-r["score"], str(r["name"])))

    bucket_counts = {}
    priority_counts = {"high": 0, "medium": 0, "low": 0}
    for r in results:
        bucket_counts[r["bucket"]] = bucket_counts.get(r["bucket"], 0) + 1
        priority_counts[r["priority"]] += 1

    urgent_total = sum(1 for r in results if r["urgent_help"])

    top = results[: max(n, 0)]

    for rank, record in enumerate(top, start=1):
        record["rank"] = rank

    phone_issue_count = sum(1 for r in all_results if r["phone_needs_repair"])

    output = {
        "summary": {
            "total_facilities": len(all_results),
            "matching_filter": len(results),
            "by_bucket": bucket_counts,
            "by_priority": priority_counts,
            "urgent_in_matching_records": urgent_total,
            "filter": {
                "provider": provider,
                "bucket": bucket,
                "n": n,
                "group_by": group_by,
                "focus": focus,
                "explain": explain,
            },
            "dataset_issues": {
                "phones_without_leading_zero": phone_issue_count,
                "note": (
                    f"{phone_issue_count} stored phone numbers do not start "
                    "with 0 or +, so the leading zero is probably missing "
                    "(for example 30224451400 instead of 030 22445 1400). "
                    "This comes from the source data."
                    if phone_issue_count
                    else "No phone number formatting problems found."
                ),
            },
        },
    }

    if explain:
        output["summary"]["priority_tiers"] = {
            "high": f"score of {PRIORITY_HIGH_MIN:g} or more",
            "medium": f"score of {PRIORITY_MEDIUM_MIN:g} to under {PRIORITY_HIGH_MIN:g}",
            "low": f"score under {PRIORITY_MEDIUM_MIN:g}",
        }

    if group_by == "provider":
        output.update(group_by_provider(repository, results, explain))
    else:
        output["projected_gain"] = coverage_gain(repository, top)

        output["summary"]["shown"] = {
            "records": len(top),
            "high_priority": sum(1 for r in top if r["priority"] == "high"),
            "urgent": sum(1 for r in top if r["urgent_help"]),
        }

        for record in top:
            record.pop("missing_fields", None)
            record.pop("provider_hint", None)
            record.pop("phone_needs_repair", None)

            if not explain:
                record.pop("score", None)
                record.pop("score_details", None)

        output["results"] = top

    return output
