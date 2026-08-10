"""Row types for the three ``Capacity pool - …`` master-excel sheets.

The rows mirror the sheets minus the free-text provenance columns
(``notes``/``source``, which stay in the workbook only); the loader
maps opening-credit rows onto :class:`~steelo.capacity_policy.pool.SeedEntry`
at seed time. Swap-ratio derivation lives here too, so the data-prep ratio-grid
diagnostic and the tree evaluator share one precedence implementation.
"""

from dataclasses import dataclass

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
        from_year: First year the classification applies, or None for always.
    """

    geo_key: str
    region_name: str | None
    type: str | None
    from_year: int | None


@dataclass(frozen=True)
class TechnologyRow:
    """One row of ``Capacity pool - technologies``.

    Two row types share the sheet, discriminated by ``switching_to``:
    classification rows (``switching_to`` blank) carry the emission-intense and
    deep-abatement flags for one ``(technology, reductant)``; override rows
    (``switching_to`` set) pin one transition's swap ratio and carry no flags.
    A blank flag is unauthored, not False.

    Attributes:
        technology: Model technology name; ``"*"`` only on override rows.
        product: ``"iron"`` or ``"steel"`` on classification rows.
        reductant: Reductant the classification is specific to, or None for
            any reductant.
        is_emission_intense: Whether replacing this route triggers the
            penalised ratio; None when unauthored.
        is_deep_abatement: Whether building this route qualifies for 1:1;
            None when unauthored.
        switching_to: Target technology of an override row (``"*"`` allowed),
            None on classification rows.
        swap_ratio: The overridden ratio; only on override rows.
    """

    technology: str
    product: str | None
    reductant: str | None
    is_emission_intense: bool | None
    is_deep_abatement: bool | None
    switching_to: str | None
    swap_ratio: float | None

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
        plant_group_id: Owning company (maps to ``SeedEntry.owner_id``), or
            None for an unowned, freely drawable credit.
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
        and row.is_deep_abatement is None
        and row.technology in reductant_split_technologies
    )


def resolve_swap_ratio(
    old: TechnologyRow,
    new: TechnologyRow,
    overrides: list[TechnologyRow],
    default_ratio: float,
) -> float | None:
    """Resolve the effective ratio for one transition between classification rows.

    Override precedence, most specific first: (1) technology + reductant +
    ``switching_to``; (2) technology + ``switching_to``; (3) one side ``"*"``;
    (4) both sides ``"*"``; (5) no match — derive from the flags. Validation
    has already refused equal-specificity collisions, so each level holds at
    most one match.

    Args:
        old: Classification row of the route being replaced.
        new: Classification row of the route being built.
        overrides: The sheet's override rows.
        default_ratio: The penalised replacement ratio (config default 1.5).

    Returns:
        The ratio, or None when a needed flag is unauthored and no override
        decides the transition.
    """
    levels = (
        lambda r: r.reductant is not None
        and r.reductant == old.reductant
        and r.technology == old.technology
        and r.switching_to == new.technology,
        lambda r: r.reductant is None and r.technology == old.technology and r.switching_to == new.technology,
        lambda r: (r.technology == WILDCARD and r.switching_to == new.technology)
        or (r.technology == old.technology and r.switching_to == WILDCARD),
        lambda r: r.technology == WILDCARD and r.switching_to == WILDCARD,
    )
    for matches in levels:
        hits = [r for r in overrides if matches(r)]
        if hits:
            return hits[0].swap_ratio
    if old.is_emission_intense is None or new.is_deep_abatement is None:
        return None
    return default_ratio if old.is_emission_intense and not new.is_deep_abatement else 1.0


def effective_ratio_grid(rows: list[TechnologyRow], default_ratio: float) -> tuple[list[str], list[list[str]]]:
    """Derive the effective transition-ratio grid from the flags and overrides.

    The axes are the classification keys as authored — the bare technology, or
    ``technology|reductant`` where the classification is reductant-specific.
    Cells hold the resolved ratio, or ``"unauthored"`` where a missing flag
    leaves the transition undecided. Always generated, never authored, so it
    cannot disagree with the rules that produce it.

    Args:
        rows: All rows of the technologies sheet.
        default_ratio: The penalised replacement ratio (config default 1.5).

    Returns:
        The axis labels and the row-major grid of cell strings, old routes on
        the rows and new routes on the columns.
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
