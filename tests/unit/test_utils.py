import pytest
import pandas as pd
import numpy as np

from steelo.utilities.utils import count_appearances


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
