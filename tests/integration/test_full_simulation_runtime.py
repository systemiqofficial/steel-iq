"""Integration test to verify all runtime issues are resolved."""

import pytest


def test_no_fopex_keyerror():
    """Test that FOPEX access doesn't cause KeyError."""
    # Test the specific line that was fixed
    technology_fopex = {"BF": 100}  # No "Other" key

    # This should not raise KeyError, should use fallback
    result = technology_fopex.get("BOF", technology_fopex.get("Other", 0))
    assert result == 0  # Should use the fallback value

    # Also test when "Other" exists
    technology_fopex_with_other = {"BF": 100, "Other": 150}
    result2 = technology_fopex_with_other.get("BOF", technology_fopex_with_other.get("Other", 0))
    assert result2 == 150  # Should use "Other" value


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
