# tests/architecture/test_no_global_imports.py
import re
from pathlib import Path

# Per your guidelines, these modules are allowed to import settings.project_root
ALLOWED_EXCEPTIONS = {
    "cost_of_renewables.py",
    "baseload_power_optimization.py",
    "baseload_power_simulation.py",
    "renewable_energy_input_prep.py",  # Part of standalone baseload power simulation
}


def test_forbid_global_config_imports_in_core_logic():
    """
    Scans the codebase to ensure no modules in the core simulation
    are importing from the global config or global_variables modules.
    """
    project_root = Path(__file__).parent.parent.parent
    steelo_src = project_root / "src" / "steelo"

    forbidden_imports = (
        re.compile(r"from steelo\.config import settings"),
        re.compile(r"from steelo\.utilities\.global_variables import"),
    )

    violations = []
    for py_file in steelo_src.rglob("*.py"):
        # Skip allowed exceptions and the config modules themselves
        if py_file.name in ALLOWED_EXCEPTIONS or "config.py" in str(py_file):
            continue

        content = py_file.read_text()
        for pattern in forbidden_imports:
            if pattern.search(content):
                violations.append(str(py_file.relative_to(project_root)))

    assert not violations, f"Forbidden global imports found in: {', '.join(violations)}"
