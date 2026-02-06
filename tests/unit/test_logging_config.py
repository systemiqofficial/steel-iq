"""
Unit tests for logging_config.py.

Tests cover YAML loading, context-aware filtering, module context management,
CLI ceiling behaviour, and simulation_logging context manager.
"""

import logging
import pytest
from pathlib import Path

from steelo.logging_config import (
    LoggingConfig,
    ContextAwareFilter,
    ShortNameFormatter,
    _current_module,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_logging_state():
    """
    Ensure clean logging state before and after each test.

    Removes ContextAwareFilter instances from root logger before and after test.
    """
    root = logging.getLogger()

    # Remove ALL ContextAwareFilter instances BEFORE test (from other tests)
    for f in list(root.filters):
        if isinstance(f, ContextAwareFilter):
            root.removeFilter(f)

    # Clear thread-local context before test
    if hasattr(_current_module, "name"):
        _current_module.name = None

    yield

    # Remove ALL ContextAwareFilter instances AFTER test
    for f in list(root.filters):
        if isinstance(f, ContextAwareFilter):
            root.removeFilter(f)

    # Clear thread-local context
    if hasattr(_current_module, "name"):
        _current_module.name = None


@pytest.fixture
def yaml_config_file(tmp_path: Path):
    """
    Factory fixture to create temporary YAML config files.

    Returns a function that creates a YAML file with specified content.
    """

    def _create_yaml(content: str) -> Path:
        yaml_path = tmp_path / "logging_config.yaml"
        yaml_path.write_text(content)
        return yaml_path

    return _create_yaml


@pytest.fixture
def basic_yaml_config(yaml_config_file):
    """
    Create a basic YAML config with standard module levels.
    """
    content = """
version: 1
global_level: WARNING

features:
  furnace_group_debug: true

modules:
  geo: DEBUG
  pam: INFO
  tm: WARNING

function_overrides:
  noisy_function: ERROR
  verbose_func: WARNING

external:
  pyomo: ERROR
  urllib3: WARNING
"""
    return yaml_config_file(content)


# ---------------------------------------------------------------------------
# YAML Configuration Loading Tests
# ---------------------------------------------------------------------------


def test_configure_from_yaml_loads_module_levels(
    basic_yaml_config: Path,
    clean_logging_state,
):
    """Verify geo/pam/tm levels are parsed from YAML."""
    LoggingConfig.configure_from_yaml(str(basic_yaml_config))

    root = logging.getLogger()
    # Find the ContextAwareFilter we added
    context_filters = [f for f in root.filters if isinstance(f, ContextAwareFilter)]
    assert len(context_filters) == 1

    filter_obj = context_filters[0]
    assert filter_obj.module_levels["geo"] == logging.DEBUG
    assert filter_obj.module_levels["pam"] == logging.INFO
    assert filter_obj.module_levels["tm"] == logging.WARNING


def test_configure_from_yaml_loads_function_overrides(
    basic_yaml_config: Path,
    clean_logging_state,
):
    """Verify function overrides dict is populated correctly."""
    LoggingConfig.configure_from_yaml(str(basic_yaml_config))

    root = logging.getLogger()
    context_filters = [f for f in root.filters if isinstance(f, ContextAwareFilter)]
    filter_obj = context_filters[0]

    assert filter_obj.function_overrides["noisy_function"] == logging.ERROR
    assert filter_obj.function_overrides["verbose_func"] == logging.WARNING


def test_configure_from_yaml_sets_feature_flags(
    yaml_config_file,
    clean_logging_state,
):
    """Verify ENABLE_FURNACE_GROUP_DEBUG is updated from YAML."""
    # First set to opposite value
    LoggingConfig.ENABLE_FURNACE_GROUP_DEBUG = True

    content = """
version: 1
features:
  furnace_group_debug: false
modules:
  geo: INFO
"""
    yaml_path = yaml_config_file(content)
    LoggingConfig.configure_from_yaml(str(yaml_path))

    assert LoggingConfig.ENABLE_FURNACE_GROUP_DEBUG is False

    # Reset for other tests
    LoggingConfig.ENABLE_FURNACE_GROUP_DEBUG = True


def test_configure_from_yaml_sets_external_loggers(
    basic_yaml_config: Path,
    clean_logging_state,
):
    """Verify external logger levels (e.g. pyomo) are set."""
    LoggingConfig.configure_from_yaml(str(basic_yaml_config))

    pyomo_logger = logging.getLogger("pyomo")
    assert pyomo_logger.level == logging.ERROR

    urllib3_logger = logging.getLogger("urllib3")
    assert urllib3_logger.level == logging.WARNING


# ---------------------------------------------------------------------------
# Module Context (Thread-Local) Tests
# ---------------------------------------------------------------------------


def test_module_context_sets_thread_local(clean_logging_state):
    """Verify _current_module.name is set within context."""
    with LoggingConfig.module_context("geo"):
        assert getattr(_current_module, "name", None) == "geo"


def test_module_context_clears_on_exit(clean_logging_state):
    """Verify _current_module.name is None after context exit."""
    with LoggingConfig.module_context("pam"):
        pass

    assert getattr(_current_module, "name", None) is None


def test_module_context_handles_exception(clean_logging_state):
    """Verify context is cleared even if exception raised inside."""
    with pytest.raises(ValueError):
        with LoggingConfig.module_context("tm"):
            raise ValueError("Test exception")

    assert getattr(_current_module, "name", None) is None


# ---------------------------------------------------------------------------
# ContextAwareFilter Behaviour Tests
# ---------------------------------------------------------------------------


def test_filter_allows_info_without_context(clean_logging_state):
    """INFO+ logs always pass regardless of module context."""
    filter_obj = ContextAwareFilter(
        module_levels={"geo": logging.INFO},
        function_overrides={},
    )

    record = logging.LogRecord(
        name="steelo.test",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Test",
        args=(),
        exc_info=None,
    )

    # No context set
    _current_module.name = None
    assert filter_obj.filter(record) is True


def test_filter_suppresses_debug_without_context(clean_logging_state):
    """DEBUG blocked when no module context is set."""
    filter_obj = ContextAwareFilter(
        module_levels={"geo": logging.DEBUG},
        function_overrides={},
    )

    record = logging.LogRecord(
        name="steelo.test",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Test",
        args=(),
        exc_info=None,
    )

    # No context set
    _current_module.name = None
    assert filter_obj.filter(record) is False


def test_filter_allows_debug_when_module_enabled(clean_logging_state):
    """DEBUG passes when module context is set and module level is DEBUG."""
    filter_obj = ContextAwareFilter(
        module_levels={"geo": logging.DEBUG},
        function_overrides={},
    )

    record = logging.LogRecord(
        name="steelo.test",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Test",
        args=(),
        exc_info=None,
    )

    _current_module.name = "geo"
    assert filter_obj.filter(record) is True


def test_filter_suppresses_debug_when_module_disabled(clean_logging_state):
    """DEBUG blocked when module context is set but module level is INFO."""
    filter_obj = ContextAwareFilter(
        module_levels={"pam": logging.INFO},
        function_overrides={},
    )

    record = logging.LogRecord(
        name="steelo.test",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Test",
        args=(),
        exc_info=None,
    )

    _current_module.name = "pam"
    assert filter_obj.filter(record) is False


def test_filter_function_override_takes_precedence(clean_logging_state):
    """Function override wins over module setting."""
    filter_obj = ContextAwareFilter(
        module_levels={"geo": logging.DEBUG},
        function_overrides={"my_function": logging.WARNING},
    )

    # DEBUG log from overridden function - should be blocked
    record = logging.LogRecord(
        name="steelo.module.my_function",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Test",
        args=(),
        exc_info=None,
    )

    _current_module.name = "geo"
    assert filter_obj.filter(record) is False


def test_filter_function_override_suppress_in_debug_module(clean_logging_state):
    """Function: WARNING blocks DEBUG even when module: DEBUG."""
    filter_obj = ContextAwareFilter(
        module_levels={"geo": logging.DEBUG},
        function_overrides={"noisy_func": logging.WARNING},
    )

    # DEBUG log should be blocked by function override
    debug_record = logging.LogRecord(
        name="steelo.domain.noisy_func",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Debug message",
        args=(),
        exc_info=None,
    )

    # INFO log should also be blocked (below WARNING)
    info_record = logging.LogRecord(
        name="steelo.domain.noisy_func",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Info message",
        args=(),
        exc_info=None,
    )

    # WARNING log should pass
    warning_record = logging.LogRecord(
        name="steelo.domain.noisy_func",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="Warning message",
        args=(),
        exc_info=None,
    )

    _current_module.name = "geo"
    assert filter_obj.filter(debug_record) is False
    assert filter_obj.filter(info_record) is False
    assert filter_obj.filter(warning_record) is True


def test_filter_function_override_enable_in_info_module(clean_logging_state):
    """Function: DEBUG allows DEBUG even when module: INFO."""
    filter_obj = ContextAwareFilter(
        module_levels={"pam": logging.INFO},
        function_overrides={"debug_func": logging.DEBUG},
    )

    record = logging.LogRecord(
        name="steelo.domain.debug_func",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Debug message",
        args=(),
        exc_info=None,
    )

    _current_module.name = "pam"
    assert filter_obj.filter(record) is True


# ---------------------------------------------------------------------------
# CLI Ceiling Tests
# ---------------------------------------------------------------------------


def test_cli_ceiling_restricts_yaml_debug(yaml_config_file, clean_logging_state):
    """YAML geo: DEBUG + CLI INFO = INFO enforced."""
    content = """
version: 1
modules:
  geo: DEBUG
  pam: DEBUG
"""
    yaml_path = yaml_config_file(content)
    LoggingConfig.configure_from_yaml(str(yaml_path), cli_max_level=logging.INFO)

    root = logging.getLogger()
    context_filters = [f for f in root.filters if isinstance(f, ContextAwareFilter)]
    filter_obj = context_filters[0]

    # CLI ceiling should restrict DEBUG to INFO
    assert filter_obj.module_levels["geo"] == logging.INFO
    assert filter_obj.module_levels["pam"] == logging.INFO


def test_cli_ceiling_allows_lower_levels(yaml_config_file, clean_logging_state):
    """YAML geo: WARNING + CLI DEBUG = WARNING used (more restrictive wins)."""
    content = """
version: 1
modules:
  geo: WARNING
"""
    yaml_path = yaml_config_file(content)
    LoggingConfig.configure_from_yaml(str(yaml_path), cli_max_level=logging.DEBUG)

    root = logging.getLogger()
    context_filters = [f for f in root.filters if isinstance(f, ContextAwareFilter)]
    filter_obj = context_filters[0]

    # WARNING is more restrictive than DEBUG, so WARNING should be used
    assert filter_obj.module_levels["geo"] == logging.WARNING


# ---------------------------------------------------------------------------
# simulation_logging Context Manager Tests
# ---------------------------------------------------------------------------


def test_simulation_logging_sets_geo_context(clean_logging_state):
    """GeospatialModel sets 'geo' context."""
    with LoggingConfig.simulation_logging("GeospatialModel"):
        assert getattr(_current_module, "name", None) == "geo"


def test_simulation_logging_sets_pam_context(clean_logging_state):
    """PlantAgentsModel sets 'pam' context."""
    with LoggingConfig.simulation_logging("PlantAgentsModel"):
        assert getattr(_current_module, "name", None) == "pam"


def test_simulation_logging_sets_tm_context(clean_logging_state):
    """AllocationModel sets 'tm' context."""
    with LoggingConfig.simulation_logging("AllocationModel"):
        assert getattr(_current_module, "name", None) == "tm"


def test_simulation_logging_clears_context_on_exit(clean_logging_state):
    """Context is cleared after simulation_logging exits."""
    with LoggingConfig.simulation_logging("PlantAgentsModel"):
        assert getattr(_current_module, "name", None) == "pam"

    assert getattr(_current_module, "name", None) is None


def test_simulation_logging_unknown_model_no_context(clean_logging_state):
    """Unknown model name yields without setting context."""
    with LoggingConfig.simulation_logging("DebugLogging"):
        assert getattr(_current_module, "name", None) is None


# ---------------------------------------------------------------------------
# configure_base_loggers Tests
# ---------------------------------------------------------------------------


def test_configure_base_loggers_sets_pyomo(clean_logging_state):
    """configure_base_loggers sets pyomo to ERROR."""
    LoggingConfig.configure_base_loggers()

    pyomo_logger = logging.getLogger("pyomo")
    assert pyomo_logger.level == logging.ERROR


def test_configure_base_loggers_sets_matplotlib(clean_logging_state):
    """configure_base_loggers sets matplotlib.font_manager to ERROR."""
    LoggingConfig.configure_base_loggers()

    mpl_logger = logging.getLogger("matplotlib.font_manager")
    assert mpl_logger.level == logging.ERROR


# ---------------------------------------------------------------------------
# Edge Case Tests
# ---------------------------------------------------------------------------


def test_missing_yaml_raises_file_not_found(clean_logging_state):
    """FileNotFoundError raised for missing YAML file."""
    with pytest.raises(FileNotFoundError):
        LoggingConfig.configure_from_yaml("/nonexistent/path/config.yaml")


def test_invalid_yaml_level_string_raises_attribute_error(
    yaml_config_file,
    clean_logging_state,
):
    """Invalid level string like 'FOOBAR' raises AttributeError."""
    content = """
version: 1
modules:
  geo: FOOBAR
"""
    yaml_path = yaml_config_file(content)

    with pytest.raises(AttributeError):
        LoggingConfig.configure_from_yaml(str(yaml_path))


# ---------------------------------------------------------------------------
# ShortNameFormatter Tests
# ---------------------------------------------------------------------------


def test_short_name_formatter_with_pam_context(clean_logging_state):
    """Formatter uses PAM prefix when pam context is set."""
    formatter = ShortNameFormatter()

    record = logging.LogRecord(
        name="steelo.domain.calculate_costs.calculate_subsidies",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="Test message",
        args=(),
        exc_info=None,
    )

    _current_module.name = "pam"
    result = formatter.format(record)

    assert result == "WARNING | PAM  | calculate_subsidies: Test message"


def test_short_name_formatter_with_geo_context(clean_logging_state):
    """Formatter uses GEO prefix when geo context is set."""
    formatter = ShortNameFormatter()

    record = logging.LogRecord(
        name="steelo.domain.calculate_costs.calculate_subsidies",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Geo message",
        args=(),
        exc_info=None,
    )

    _current_module.name = "geo"
    result = formatter.format(record)

    assert result == "INFO    | GEO  | calculate_subsidies: Geo message"


def test_short_name_formatter_with_tm_context(clean_logging_state):
    """Formatter uses TM prefix when tm context is set."""
    formatter = ShortNameFormatter()

    record = logging.LogRecord(
        name="steelo.domain.trade_modelling.set_up_steel_trade_lp.enforce_tariffs",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="Trade message",
        args=(),
        exc_info=None,
    )

    _current_module.name = "tm"
    result = formatter.format(record)

    assert result == "WARNING | TM   | enforce_tariffs: Trade message"


def test_short_name_formatter_without_context(clean_logging_state):
    """Formatter uses CORE prefix when no context is set."""
    formatter = ShortNameFormatter()

    record = logging.LogRecord(
        name="steelo.bootstrap.some_function",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg="Bootstrap message",
        args=(),
        exc_info=None,
    )

    _current_module.name = None
    result = formatter.format(record)

    assert result == "INFO    | CORE | some_function: Bootstrap message"


def test_short_name_formatter_external_logger(clean_logging_state):
    """Formatter keeps external logger names unchanged."""
    formatter = ShortNameFormatter()

    record = logging.LogRecord(
        name="pyomo.core.base",
        level=logging.WARNING,
        pathname="",
        lineno=0,
        msg="Pyomo message",
        args=(),
        exc_info=None,
    )

    _current_module.name = "pam"
    result = formatter.format(record)

    # External loggers use their full name as function name
    assert result == "WARNING | PAM  | base: Pyomo message"


def test_short_name_formatter_same_function_different_context(clean_logging_state):
    """Same function shows different context prefix based on current module."""
    formatter = ShortNameFormatter()

    record = logging.LogRecord(
        name="steelo.domain.calculate_costs.calculate_subsidies",
        level=logging.DEBUG,
        pathname="",
        lineno=0,
        msg="Subsidy calc",
        args=(),
        exc_info=None,
    )

    # Same function, different contexts
    _current_module.name = "pam"
    pam_result = formatter.format(record)

    _current_module.name = "geo"
    geo_result = formatter.format(record)

    assert pam_result == "DEBUG   | PAM  | calculate_subsidies: Subsidy calc"
    assert geo_result == "DEBUG   | GEO  | calculate_subsidies: Subsidy calc"
