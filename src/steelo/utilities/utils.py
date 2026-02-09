import re

import pandas as pd
from IPython.display import display, HTML
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from steelo.domain.models import FurnaceGroup


def normalize_energy_key(name: str | None) -> str:
    """Normalise an energy carrier or feedstock name to a canonical key.

    Args:
        name: Raw carrier/feedstock name (e.g. "Natural Gas", "bio-pci", "co2_-_inlet").
            None is treated as empty string.

    Returns:
        Lowercase key with spaces and hyphens replaced by underscores,
        and consecutive underscores collapsed to one.
    """
    if name is None:
        return ""
    key = str(name).lower().replace(" ", "_").replace("-", "_")
    return re.sub(r"_+", "_", key)


def display_scrollable_dataframe(df) -> None:
    """
    Display all DataFrame rows in a scrollable element
    """

    with pd.option_context("display.max_rows", None):
        display(HTML(f'<div style="max-height: 400px; overflow-y: scroll;">{df.to_html()}</div>'))


def display_scrollable_series(s) -> None:
    """
    Display all Series rows in a scrollable element, showing the full content of each cell
    """

    with pd.option_context("display.max_rows", None, "display.max_colwidth", None):
        display(
            HTML(
                f'<div style="max-height: 400px; overflow-y: scroll; text-align: left;">{s.to_frame().to_html()}</div>'
            )
        )


def count_appearances(data: pd.DataFrame, col: str) -> None:
    """
    Prints all unique values in a specified column of a DataFrame and counts the number of appearences.
    """

    for i in data[col].unique():
        if pd.isna(i):
            count = data[col].isna().sum()
        else:
            count = data[col].value_counts(dropna=False)[i]
        print(f"Value: {i}; count: {count}")


def merge_dicts_bom(list_of_dicts):
    """A function to merge a list of dicts"""
    merged = {}

    for d in list_of_dicts:
        for top_key, sub_dict in d.items():
            # If 'top_key' isn't in 'merged', create it
            merged.setdefault(top_key, {})

            for inner_key, inner_val in sub_dict.items():
                # If 'inner_key' isn't in 'merged[top_key]', copy this entire sub-dict
                if inner_key not in merged[top_key]:
                    merged[top_key][inner_key] = inner_val.copy()
                else:
                    # Otherwise, just sum the 'demand'
                    merged[top_key][inner_key]["demand"] += inner_val["demand"]

    return merged


def merge_two_dictionaries(target: dict, source: dict) -> dict:
    """
    Merges two dictionaries by summing values for matching keys.
    If a key in source doesn't exist in target, it's added to target.
    """
    for key, value in source.items():
        target[key] = target.get(key, 0) + value
    return target


def get_most_common_reductant_by_technology(furnace_groups: list["FurnaceGroup"]) -> dict[str, str]:
    """
    Get the most common reductant for each technology from a collection of furnace groups.

    Aggregates reductant data from all furnace groups to determine the most frequently
    used reductant for each technology type.

    Args:
        furnace_groups: List of FurnaceGroup objects to aggregate from.

    Returns:
        dict[str, str]: Dictionary mapping technology name to most common reductant.
                       If no reductant is set for a technology, it will be omitted from the result.

    Example:
        {
            "BOF": "coke",
            "EAF": "electricity",
            "DRI": "natural_gas"
        }
    """
    # Group reductants by technology
    tech_reductants: dict[str, list[str]] = defaultdict(list)

    for fg in furnace_groups:
        tech_name = fg.technology.name
        reductant = fg.chosen_reductant

        # Only include non-empty reductants
        if reductant and reductant.strip():
            tech_reductants[tech_name].append(reductant)

    # Find most common reductant for each technology
    result: dict[str, str] = {}
    for tech_name, reductants in tech_reductants.items():
        if reductants:
            # Counter.most_common(1) returns [(value, count)]
            most_common = Counter(reductants).most_common(1)[0][0]
            result[tech_name] = most_common

    return result
