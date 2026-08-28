"""
Module-level skip gates for tests that specify code still being implemented.

`pytest.importorskip` guards only that a module imports, not that it exposes the names
a test file needs. Since the implementation lands over many commits, a file importing a
function that does not exist yet would error at collection rather than skip -- turning
the suite red for work that is simply not done.

These helpers skip instead, so the tree stays green while each commit un-gates its own
file. They are a scaffold: once everything is implemented, every gate here is a no-op
and the calls can be replaced by plain imports.
"""

import pytest


def require(module_name: str, *names: str):
    """Import `module_name`, or skip the file. Skip too if any name is still missing."""
    module = pytest.importorskip(module_name)
    missing = [name for name in names if not hasattr(module, name)]
    if missing:
        pytest.skip(
            f"{module_name} does not define {', '.join(missing)} yet",
            allow_module_level=True,
        )
    return module


def require_schema(module_name: str, attr: str, minimum: int):
    """Skip unless `module_name.attr` has reached `minimum` -- for schema-version gates."""
    module = pytest.importorskip(module_name)
    actual = getattr(module, attr, None)
    if actual is None or actual < minimum:
        pytest.skip(
            f"{module_name}.{attr} is {actual!r}, needs >= {minimum}",
            allow_module_level=True,
        )
    return module
