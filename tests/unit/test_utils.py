import pytest
import pandas as pd
import numpy as np

import enum
import re

from steelo.utilities.utils import count_appearances, normalize_name


def test_count_appearances(capsys):
    # Test case 1: Normal data
    data = pd.DataFrame({"col1": ["A", "B", "A", "C", "B", "B", np.nan, "A"]})
    count_appearances(data, "col1")
    captured = capsys.readouterr()
    assert "Value: A; count: 3" in captured.out
    assert "Value: B; count: 3" in captured.out
    assert "Value: C; count: 1" in captured.out
    assert "Value: nan; count: 1" in captured.out

    # Test case 2: Column with only NaN values
    data = pd.DataFrame({"col1": [np.nan, np.nan, np.nan]})
    count_appearances(data, "col1")
    captured = capsys.readouterr()
    assert "Value: nan; count: 3" in captured.out

    # Test case 3: Empty DataFrame
    data = pd.DataFrame(columns=["col1"])
    count_appearances(data, "col1")
    captured = capsys.readouterr()
    assert captured.out.strip() == ""

    # Test case 4: Column with mixed data types
    data = pd.DataFrame({"col1": ["A", 1, "B", 1, "A", np.nan, 1, "B"]})
    count_appearances(data, "col1")
    captured = capsys.readouterr()
    assert "Value: A; count: 2" in captured.out
    assert "Value: 1; count: 3" in captured.out
    assert "Value: B; count: 2" in captured.out
    assert "Value: nan; count: 1" in captured.out

    # Test case 5: Specified column does not exist
    data = pd.DataFrame({"col1": ["A", "B", "A"]})
    with pytest.raises(KeyError):
        count_appearances(data, "col2")


def _reference_normalize_name(name):
    """The uncached definition of normalize_name."""
    if name is None:
        return ""
    key = str(name).lower().replace(" ", "_").replace("-", "_")
    return re.sub(r"_+", "_", key)


class _StrMixinEnum(str, enum.Enum):
    HOT_METAL = "hot_metal"


NORMALIZE_NAME_INPUTS = [
    "Natural Gas",
    "bio-pci",
    "co2 - stored",
    "co2_-_inlet",
    "Hot metal",
    "  leading and trailing  ",
    "already_normal",
    "MiXeD-Case value",
    "",
    "___",
    None,
    float("nan"),
    7,
    ["a b", "c-d"],
    _StrMixinEnum.HOT_METAL,
]


def test_normalize_name_matches_uncached_definition():
    for _ in range(2):  # the second pass is served from the cache
        for name in NORMALIZE_NAME_INPUTS:
            assert normalize_name(name) == _reference_normalize_name(name), repr(name)


def test_normalize_name_does_not_share_cache_entry_between_str_and_str_subclass():
    # _StrMixinEnum.HOT_METAL == "hot_metal" and hashes alike, but str() gives "_StrMixinEnum.HOT_METAL"
    assert normalize_name("hot_metal") == "hot_metal"
    assert normalize_name(_StrMixinEnum.HOT_METAL) == _reference_normalize_name(_StrMixinEnum.HOT_METAL)
    assert normalize_name(_StrMixinEnum.HOT_METAL) != "hot_metal"
