"""Row types for the three ``Capacity pool - …`` master-excel sheets, and the swap-ratio precedence.

Swap-ratio derivation lives here so the data-prep ratio grid and the tree
evaluator share one implementation. Columns and rules:
docs/user_guide/master_input_reference.md#china-capacity-replacement-policy-sheets.
"""

from dataclasses import dataclass

from steelo.utilities.utils import normalize_name

WILDCARD = "*"


@dataclass(frozen=True)
class RegionRow:
    """One Chinese first-order unit from ``Capacity pool - CHN provinces``.

    Attributes:
        geo_key: Combined ``iso3:code`` unit key, e.g. ``"CHN:CN-HE"``.
        region_name: Display name; for key provinces, the cluster name that
            becomes the credit tag (member provinces share it).
        type: ``"key"``, ``"exempt"``, or None for a non-key province
            (untagged deposits, unrestricted withdrawals).
    """

    geo_key: str
    region_name: str | None
    type: str | None


@dataclass(frozen=True)
class TechnologyRow:
    """One row of ``Capacity pool - technologies``.

    Classification rows (``switching_to`` blank) carry the emission-intense
    flag for one ``(technology, reductant)``; override rows (``switching_to``
    set) pin one transition's ratio. A blank flag is unauthored, not False.

    Attributes:
        technology: Model technology name; ``"*"`` only on override rows.
        product: ``"iron"`` or ``"steel"`` on classification rows.
        reductant: Reductant the row is specific to, or None for any.
        is_emission_intense: Whether the route falls short of the
            deep-abatement threshold; None when unauthored.
        switching_to: Target technology of an override row (``"*"`` allowed),
            None on classification rows.
        swap_ratio: The overridden ratio; only on override rows.
        switching_to_reductant: New-side reductant an override row is specific
            to, or None for any; always None on classification rows.
    """

    technology: str
    product: str | None
    reductant: str | None
    is_emission_intense: bool | None
    switching_to: str | None
    swap_ratio: float | None
    switching_to_reductant: str | None = None

    @property
    def is_override(self) -> bool:
        """Whether this row overrides a transition rather than classifying a route."""
        return self.switching_to is not None


@dataclass(frozen=True)
class OpeningCreditRow:
    """One row of ``Capacity pool - opening credits``: unspent credit at t=0.

    Attributes:
        vintage_year: Retirement year; sets FIFO order and the banked cutoff.
        capacity_mt: Freed capacity in Mt.
        geo_key: Combined ``iso3:code`` key of the retiring province.
        product: ``"iron"`` or ``"steel"``.
        technology: Retired technology — provenance only, never a filter.
        plant_group_id: Owning company, or None for an unowned credit.
    """

    vintage_year: int
    capacity_mt: float
    geo_key: str
    product: str
    technology: str | None
    plant_group_id: str | None


def technologies_with_reductant_rows(rows: list[TechnologyRow]) -> set[str]:
    """Return the technologies classified per reductant rather than as a whole."""
    return {row.technology for row in rows if not row.is_override and row.reductant is not None}


def is_delegation_row(row: TechnologyRow, reductant_split_technologies: set[str]) -> bool:
    """Whether a flagless blank-reductant row merely points at reductant-specific rows.

    Such a row classifies nothing itself, so it is neither a grid entry nor an
    unauthored-flags warning.
    """
    return (
        not row.is_override
        and row.reductant is None
        and row.is_emission_intense is None
        and row.technology in reductant_split_technologies
    )


def override_specificity(row: TechnologyRow) -> tuple[int, int]:
    """Rank an override row: concrete technologies named first, then reductants named.

    Validation rejects two rows of equal rank that can match one transition,
    so the most specific match is unique.
    """
    technologies = (row.technology != WILDCARD) + (row.switching_to != WILDCARD)
    reductants = (row.reductant is not None) + (row.switching_to_reductant is not None)
    return technologies, reductants


def resolve_swap_ratio(
    old: TechnologyRow,
    new: TechnologyRow,
    overrides: list[TechnologyRow],
    default_ratio: float,
) -> float | None:
    """Resolve the effective ratio for one transition between classification rows.

    Override precedence, most specific first: technology + target; one side
    ``"*"``; both sides ``"*"``; within a level the row naming more reductants
    (both, one, none); else derive from the flags. A row naming a reductant on
    either side matches only a route carrying that reductant, so never one
    classified without a reductant.

    Args:
        old: Classification row of the route being replaced.
        new: Classification row of the route being built.
        overrides: The sheet's override rows.
        default_ratio: The penalised replacement ratio.

    Returns:
        The ratio, or None when a needed flag is unauthored and no override
        decides the transition.
    """

    def reductant_matches(named: str | None, route: TechnologyRow) -> bool:
        if named is None:
            return True
        return route.reductant is not None and normalize_name(named) == normalize_name(route.reductant)

    hits = [
        row
        for row in overrides
        if row.technology in (old.technology, WILDCARD)
        and row.switching_to in (new.technology, WILDCARD)
        and reductant_matches(row.reductant, old)
        and reductant_matches(row.switching_to_reductant, new)
    ]
    if hits:
        return max(hits, key=override_specificity).swap_ratio
    if old.is_emission_intense is None or new.is_emission_intense is None:
        return None
    return default_ratio if new.is_emission_intense else 1.0


def effective_ratio_grid(rows: list[TechnologyRow], default_ratio: float) -> tuple[list[str], list[list[str]]]:
    """Derive the old-route × new-route ratio grid the sheet implies.

    Args:
        rows: All rows of the technologies sheet.
        default_ratio: The penalised replacement ratio.

    Returns:
        The axis labels (``technology`` or ``technology|reductant``) and the
        row-major grid of cells, each a ratio or ``"unauthored"``.
    """
    overrides = [row for row in rows if row.is_override]
    split = technologies_with_reductant_rows(rows)
    entries = [row for row in rows if not row.is_override and not is_delegation_row(row, split)]
    labels = [row.technology if row.reductant is None else f"{row.technology}|{row.reductant}" for row in entries]
    grid = [
        [
            "unauthored" if (ratio := resolve_swap_ratio(old, new, overrides, default_ratio)) is None else f"{ratio:g}"
            for new in entries
        ]
        for old in entries
    ]
    return labels, grid
