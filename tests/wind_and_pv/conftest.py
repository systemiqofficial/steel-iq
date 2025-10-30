import pytest


def pytest_collection_modifyitems(config, items):
    for item in items:
        # Mark all tests in the wind_and_pv directory:
        if "tests/wind_and_pv/" in str(item.fspath):
            item.add_marker(pytest.mark.wind_and_pv)
