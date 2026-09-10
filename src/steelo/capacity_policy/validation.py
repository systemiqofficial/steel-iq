"""Cross-row validation for the capacity pool input sheets.

Errors are authoring mistakes and fail ``steelo-data-prepare``; warnings are
content gaps (unauthored classification flags) that prepare reports and
tolerates — running the policy with them is refused at bootstrap instead,
which re-runs these checks with warnings promoted to errors. Reference sets
are injected so both callers and the tests control them.
"""

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from steelo.utilities.utils import normalize_name

from .inputs import (
    WILDCARD,
    OpeningCreditRow,
    RegionRow,
    TechnologyRow,
    is_delegation_row,
    technologies_with_reductant_rows,
)

PRODUCTS = ("iron", "steel")
REGION_TYPES = ("key", "exempt")


@dataclass(frozen=True)
class ValidationIssue:
    """One finding against a capacity pool sheet.

    Attributes:
        severity: ``"error"`` fails prepare; ``"warning"`` is reported only.
        sheet: The sheet the finding is against.
        message: Human-readable description naming the offending values.
    """

    severity: Literal["error", "warning"]
    sheet: str
    message: str


def validate_provinces(
    rows: list[RegionRow],
    *,
    chinese_geo_keys: set[str],
    sheet: str = "Capacity pool - CHN provinces",
) -> list[ValidationIssue]:
    """Validate the provinces sheet against the model's Chinese units.

    Args:
        rows: The sheet's rows.
        chinese_geo_keys: Every Chinese first-order unit the model resolves;
            the sheet must cover each exactly once, so non-key status is a
            recorded decision rather than an omission.
        sheet: Sheet name used in the issue messages.

    Returns:
        Issues found; empty when the sheet is valid.
    """
    issues: list[ValidationIssue] = []
    for row in rows:
        if row.type is not None and row.type not in REGION_TYPES:
            issues.append(
                ValidationIssue(
                    "error", sheet, f"type must be one of {REGION_TYPES} or blank, got {row.type!r} for {row.geo_key}"
                )
            )
        if row.type == "key" and not row.region_name:
            issues.append(
                ValidationIssue(
                    "error",
                    sheet,
                    f"key province {row.geo_key} needs a region_name — the cluster name is the credit tag",
                )
            )
    counts = Counter(row.geo_key for row in rows)
    for geo_key, count in sorted(counts.items()):
        if count > 1:
            issues.append(
                ValidationIssue(
                    "error", sheet, f"{geo_key} appears {count} times — a province cannot sit in two clusters"
                )
            )
    missing = chinese_geo_keys - set(counts)
    if missing:
        issues.append(
            ValidationIssue(
                "error", sheet, f"sheet must enumerate every Chinese unit; missing: {', '.join(sorted(missing))}"
            )
        )
    unknown = set(counts) - chinese_geo_keys
    if unknown:
        issues.append(ValidationIssue("error", sheet, f"unknown geo_key(s): {', '.join(sorted(unknown))}"))
    return issues


def validate_technologies(
    rows: list[TechnologyRow],
    *,
    technology_roster: set[str],
    reductant_vocabulary: set[str],
    sheet: str = "Capacity pool - technologies",
) -> list[ValidationIssue]:
    """Validate the technologies sheet against the model's vocabularies.

    Args:
        rows: The sheet's rows, classification and override alike.
        technology_roster: Technology names from the techno-economic sheet;
            sheet names must match exactly, with ``"*"`` permitted only in
            ``technology``/``switching_to`` on override rows.
        reductant_vocabulary: Reductant names from the bill of materials.
        sheet: Sheet name used in the issue messages.

    Returns:
        Issues found. An unauthored classification flag is a warning — except on
        delegation rows, which classify nothing — so fixtures still build from
        a workbook with ``TO AUTHOR`` cells; everything else is an error.
    """
    issues: list[ValidationIssue] = []

    def error(message: str) -> None:
        issues.append(ValidationIssue("error", sheet, message))

    overrides = [row for row in rows if row.is_override]
    classifications = [row for row in rows if not row.is_override]
    split = technologies_with_reductant_rows(rows)
    # Membership is checked up to normalize_name, the canonical key form, so the
    # check holds whether the vocabulary comes from the workbook's Bill of
    # Materials (data prep, sheet spelling) or the prepared primary-feedstocks
    # fixture (bootstrap, normalised spelling)
    known_reductants = {normalize_name(reductant) for reductant in reductant_vocabulary}

    for row in classifications:
        label = f"technology {row.technology!r}" + (f" reductant {row.reductant!r}" if row.reductant else "")
        if row.technology == WILDCARD:
            error("'*' is only allowed on override rows")
        elif row.technology not in technology_roster:
            error(f"unknown {label} — names must match the model roster exactly")
        if row.reductant is not None and normalize_name(row.reductant) not in known_reductants:
            error(f"unknown reductant {row.reductant!r} for technology {row.technology!r}")
        if row.product not in PRODUCTS:
            error(f"product must be one of {PRODUCTS} on classification rows, got {row.product!r} ({label})")
        if row.swap_ratio is not None:
            error(f"swap_ratio is only valid on override rows ({label})")

    for (technology, reductant), count in sorted(
        Counter((row.technology, row.reductant) for row in classifications).items(), key=str
    ):
        if count > 1:
            error(f"duplicate classification rows for technology {technology!r} reductant {reductant!r}")

    for row in overrides:
        label = f"override row {row.technology!r} -> {row.switching_to!r}"
        for name in (row.technology, row.switching_to):
            if name != WILDCARD and name not in technology_roster:
                error(f"unknown technology {name!r} on {label} — names must match the model roster exactly")
        if row.reductant is not None and normalize_name(row.reductant) not in known_reductants:
            error(f"unknown reductant {row.reductant!r} on {label}")
        if row.reductant is not None and row.technology != WILDCARD and row.technology not in split:
            error(
                f"{label} restricts reductant {row.reductant!r}, but {row.technology!r} is not classified "
                "per reductant, so the row can never match"
            )
        if row.is_emission_intense is not None:
            error(f"{label} must not carry classification flags")
        if row.swap_ratio is None or row.swap_ratio <= 0:
            error(f"{label} needs a positive swap_ratio, got {row.swap_ratio!r}")

    for (technology, reductant, switching_to), count in sorted(
        Counter((row.technology, row.reductant, row.switching_to) for row in overrides).items(), key=str
    ):
        if count > 1:
            error(f"duplicate override rows for {technology!r} -> {switching_to!r} (reductant {reductant!r})")
    for to_wildcard in (row for row in overrides if row.switching_to == WILDCARD and row.technology != WILDCARD):
        for from_wildcard in (row for row in overrides if row.technology == WILDCARD and row.switching_to != WILDCARD):
            error(
                f"override rows collide at equal specificity: {to_wildcard.technology!r} -> '*' and "
                f"'*' -> {from_wildcard.switching_to!r} both match "
                f"{to_wildcard.technology!r} -> {from_wildcard.switching_to!r}"
            )

    for row in classifications:
        if is_delegation_row(row, split):
            continue
        if row.is_emission_intense is None:
            label = f"technology {row.technology!r}" + (f" reductant {row.reductant!r}" if row.reductant else "")
            issues.append(ValidationIssue("warning", sheet, f"unauthored is_emission_intense for {label}"))
    for technology in sorted(technology_roster - {row.technology for row in classifications}):
        issues.append(ValidationIssue("warning", sheet, f"technology {technology!r} has no classification row"))
    return issues


def validate_opening_credits(
    rows: list[OpeningCreditRow],
    *,
    technology_roster: set[str],
    chinese_geo_keys: set[str],
    sheet: str = "Capacity pool - opening credits",
) -> list[ValidationIssue]:
    """Validate the opening-credits sheet.

    Args:
        rows: The sheet's rows, in sheet order (messages cite sheet rows,
            header offset included).
        technology_roster: Technology names from the techno-economic sheet;
            the provenance ``technology`` may be blank but never ``"*"``.
        chinese_geo_keys: Every Chinese first-order unit the model resolves.
        sheet: Sheet name used in the issue messages.

    Returns:
        Issues found; empty when the sheet is valid.
    """
    issues: list[ValidationIssue] = []
    for position, row in enumerate(rows):
        label = f"row {position + 2}"
        if row.capacity_mt <= 0:
            issues.append(
                ValidationIssue("error", sheet, f"{label}: capacity_mt must be positive, got {row.capacity_mt}")
            )
        if row.product not in PRODUCTS:
            issues.append(
                ValidationIssue("error", sheet, f"{label}: product must be one of {PRODUCTS}, got {row.product!r}")
            )
        if row.technology is not None and row.technology not in technology_roster:
            issues.append(
                ValidationIssue(
                    "error",
                    sheet,
                    f"{label}: unknown technology {row.technology!r} — names must match the model roster exactly",
                )
            )
        if row.geo_key not in chinese_geo_keys:
            issues.append(ValidationIssue("error", sheet, f"{label}: unknown geo_key {row.geo_key!r}"))
    return issues
