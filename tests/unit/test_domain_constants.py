# tests/unit/test_domain_constants.py

from steelo.domain.constants import GRAVITY_ACCELERATION, Commodities


def test_true_constants_exist_and_are_correct():
    """
    Tests that the constants have been moved to their new home
    and retain their correct values.
    """
    assert GRAVITY_ACCELERATION == 9.81
    assert isinstance(Commodities.STEEL, Commodities)
    assert Commodities.STEEL.value == "steel"
