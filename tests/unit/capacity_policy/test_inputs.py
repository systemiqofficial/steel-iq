"""Tests for the swap-ratio precedence and the effective ratio grid."""

from steelo.capacity_policy import inputs
from steelo.capacity_policy.inputs import TechnologyRow

DEFAULT_RATIO = 1.5


def classification(
    technology: str,
    reductant: str | None = None,
    is_emission_intense: bool | None = True,
) -> TechnologyRow:
    """Build a classification row (switching_to blank)."""
    return TechnologyRow(
        technology=technology,
        product="iron",
        reductant=reductant,
        is_emission_intense=is_emission_intense,
        switching_to=None,
        swap_ratio=None,
    )


def override(
    technology: str,
    switching_to: str,
    swap_ratio: float,
    reductant: str | None = None,
    switching_to_reductant: str | None = None,
) -> TechnologyRow:
    """Build an override row (flag blank, ratio set)."""
    return TechnologyRow(
        technology=technology,
        product=None,
        reductant=reductant,
        is_emission_intense=None,
        switching_to=switching_to,
        swap_ratio=swap_ratio,
        switching_to_reductant=switching_to_reductant,
    )


BF = classification("BF")
SR = classification("SR")
DRI_COAL = classification("DRI", reductant="Coal")
DRI_HYDROGEN = classification("DRI", reductant="Hydrogen", is_emission_intense=False)
DRI_NATURAL_GAS = classification("DRI", reductant="Natural gas", is_emission_intense=False)
DRI_NO_HYPOTHESIS = classification("DRI")  # what the conservative fallback synthesises


def test_new_side_reductant_override_hits_only_that_reductant():
    """``* -> DRI|Coal`` pins coal DRI and leaves hydrogen DRI to the derivation."""
    overrides = [override("*", "DRI", 2.0, switching_to_reductant="Coal")]

    assert inputs.resolve_swap_ratio(BF, DRI_COAL, overrides, DEFAULT_RATIO) == 2.0
    assert inputs.resolve_swap_ratio(BF, DRI_HYDROGEN, overrides, DEFAULT_RATIO) == 1.0


def test_new_side_reductant_matches_up_to_normalisation():
    """The run-time spelling of a reductant hits the sheet spelling, as on the old side."""
    overrides = [override("*", "DRI", 2.0, switching_to_reductant="natural_gas")]

    assert inputs.resolve_swap_ratio(BF, DRI_NATURAL_GAS, overrides, DEFAULT_RATIO) == 2.0


def test_reductant_pinned_override_misses_a_route_with_no_reductant_hypothesis():
    """No reductant hypothesis means no reductant-specific exception: the derivation decides."""
    overrides = [override("*", "DRI", 2.0, switching_to_reductant="Coal")]

    assert inputs.resolve_swap_ratio(BF, DRI_NO_HYPOTHESIS, overrides, DEFAULT_RATIO) == DEFAULT_RATIO


def test_more_named_reductants_win_within_a_technology_level():
    """Both reductants beat one, which beats none, whatever the sheet order."""
    none_named = override("DRI", "DRI", 1.1)
    one_named = override("DRI", "DRI", 1.2, switching_to_reductant="Coal")
    both_named = override("DRI", "DRI", 1.3, reductant="Coal", switching_to_reductant="Coal")

    assert inputs.resolve_swap_ratio(DRI_COAL, DRI_COAL, [none_named, one_named, both_named], DEFAULT_RATIO) == 1.3
    assert inputs.resolve_swap_ratio(DRI_COAL, DRI_COAL, [none_named, one_named], DEFAULT_RATIO) == 1.2
    assert inputs.resolve_swap_ratio(DRI_HYDROGEN, DRI_COAL, [none_named, one_named, both_named], DEFAULT_RATIO) == 1.2
    assert inputs.resolve_swap_ratio(DRI_COAL, DRI_HYDROGEN, [none_named, one_named, both_named], DEFAULT_RATIO) == 1.1


def test_named_reductant_wins_at_a_wildcard_level_whatever_the_sheet_order():
    """``* -> DRI|Coal`` beats ``* -> DRI`` even when the plain row comes first."""
    overrides = [override("*", "DRI", 1.1), override("*", "DRI", 2.0, switching_to_reductant="Coal")]

    assert inputs.resolve_swap_ratio(BF, DRI_COAL, overrides, DEFAULT_RATIO) == 2.0
    assert inputs.resolve_swap_ratio(BF, DRI_HYDROGEN, overrides, DEFAULT_RATIO) == 1.1


def test_technology_level_outranks_the_reductant_count():
    """A plain exact pair beats a wildcard row however many reductants the wildcard row names."""
    overrides = [
        override("*", "*", 3.0, reductant="Coal", switching_to_reductant="Coal"),
        override("DRI", "*", 2.0, switching_to_reductant="Coal"),
        override("DRI", "DRI", 1.1),
    ]

    assert inputs.resolve_swap_ratio(DRI_COAL, DRI_COAL, overrides, DEFAULT_RATIO) == 1.1
    assert inputs.resolve_swap_ratio(DRI_COAL, DRI_COAL, overrides[:2], DEFAULT_RATIO) == 2.0
    assert inputs.resolve_swap_ratio(DRI_COAL, DRI_COAL, overrides[:1], DEFAULT_RATIO) == 3.0


def test_sheet_without_new_side_reductants_resolves_by_the_four_technology_levels():
    """With no ``switching_to_reductant`` values the established precedence holds at every level."""
    overrides = [
        override("*", "*", 1.4),
        override("DRI", "*", 1.3),
        override("DRI", "SR", 1.2),
        override("DRI", "SR", 1.1, reductant="Coal"),
    ]

    assert inputs.resolve_swap_ratio(DRI_COAL, SR, overrides, DEFAULT_RATIO) == 1.1
    assert inputs.resolve_swap_ratio(DRI_HYDROGEN, SR, overrides, DEFAULT_RATIO) == 1.2
    assert inputs.resolve_swap_ratio(DRI_COAL, BF, overrides, DEFAULT_RATIO) == 1.3
    assert inputs.resolve_swap_ratio(BF, SR, overrides, DEFAULT_RATIO) == 1.4
    assert inputs.resolve_swap_ratio(BF, SR, [], DEFAULT_RATIO) == DEFAULT_RATIO


def test_ratio_grid_pins_only_the_named_new_side_column():
    """``* -> DRI|Coal = 2`` moves the ``DRI|Coal`` column and no other DRI column."""
    rows = [
        BF,
        classification("DRI", is_emission_intense=None),  # delegation row
        DRI_COAL,
        DRI_HYDROGEN,
        DRI_NATURAL_GAS,
    ]
    baseline_labels, baseline = inputs.effective_ratio_grid(rows, DEFAULT_RATIO)

    labels, grid = inputs.effective_ratio_grid(
        rows + [override("*", "DRI", 2.0, switching_to_reductant="Coal")],
        DEFAULT_RATIO,
    )

    assert labels == baseline_labels == ["BF", "DRI|Coal", "DRI|Hydrogen", "DRI|Natural gas"]
    coal = labels.index("DRI|Coal")
    for line, baseline_line in zip(grid, baseline):
        assert line[coal] == "2"
        assert [cell for i, cell in enumerate(line) if i != coal] == [
            cell for i, cell in enumerate(baseline_line) if i != coal
        ]
