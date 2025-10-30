import pandas as pd
from IPython.display import display, HTML


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
