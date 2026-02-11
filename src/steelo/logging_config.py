"""
Centralized logging configuration for the steel model simulation.

This module provides YAML-based logging configuration with context-aware filtering.
Logging levels are controlled per-module (geo/pam/tm) via logging_config.yaml.

Key Features:
- YAML-based configuration with module-specific levels
- Context-aware DEBUG filtering via thread-local tracking
- Function-level overrides for noisy functions
- CLI ceiling support (--log-level INFO suppresses all DEBUG)

Usage:
    # In bootstrap.py - load YAML config early
    LoggingConfig.configure_from_yaml("logging_config.yaml", cli_level)

    # In simulation.py - set module context during model execution
    with LoggingConfig.simulation_logging("PlantAgentsModel"):
        # DEBUG logs filtered based on "pam" module config
        model.run()
"""

import logging
import yaml
import threading
from typing import Dict, Optional
from contextlib import contextmanager


# Thread-local storage for current module context
_current_module = threading.local()


class ShortNameFormatter(logging.Formatter):
    """
    Formatter that shortens logger names using runtime module context.

    Transforms verbose log output like:
        WARNING:steelo.domain.calculate_costs.calculate_subsidies:message
    Into context-aware format:
        WARNING | PAM | calculate_subsidies: message

    The context prefix comes from the thread-local _current_module, which is
    set by LoggingConfig.simulation_logging() during model execution.
    """

    def format(self, record: logging.LogRecord) -> str:
        """
        Format the log record with shortened logger name.

        Args:
            record: Log record to format

        Returns:
            Formatted log message string
        """
        # Extract function name (last component of logger name)
        parts = record.name.split(".")
        func_name = parts[-1] if len(parts) > 1 else record.name

        # Get current module context from thread-local
        context = getattr(_current_module, "name", None)
        context_str = context.upper() if context else "CORE"

        return f"{record.levelname:<7} | {context_str:<4} | {func_name}: {record.getMessage()}"


class ContextAwareFilter(logging.Filter):
    """
    Filter that allows/suppresses DEBUG logs based on current module context.

    Thread-local context tracks which module (geo/pam/tm) is currently executing.
    Inside module contexts: DEBUG logs pass if module's YAML level allows it.
    Outside module contexts: CLI level determines filtering.
    The CLI level also acts as a ceiling for module contexts.
    """

    def __init__(
        self,
        module_levels: Dict[str, int],
        function_overrides: Dict[str, int],
        cli_level: Optional[int] = None,
    ):
        """
        Initialise the context-aware filter.

        Args:
            module_levels: Mapping of module names (geo/pam/tm) to logging levels
            function_overrides: Mapping of function names to logging levels
            cli_level: CLI-specified logging level (used outside contexts, ceiling inside)
        """
        super().__init__()
        self.module_levels = module_levels
        self.function_overrides = function_overrides
        self.cli_level = cli_level if cli_level is not None else logging.WARNING

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Determine if a log record should be processed.

        Args:
            record: Log record to filter

        Returns:
            True if record should be logged, False otherwise
        """
        # Check function override first (highest priority)
        func_name = record.name.split(".")[-1]
        if func_name in self.function_overrides:
            return record.levelno >= self.function_overrides[func_name]

        # Non-DEBUG always allowed
        if record.levelno > logging.DEBUG:
            return True

        # For DEBUG: check current module context
        current_module = getattr(_current_module, "name", None)
        if not current_module:
            # Outside module context - use CLI level directly
            return record.levelno >= self.cli_level

        # Inside module context - use YAML level (CLI ceiling already applied to module_levels)
        module_level = self.module_levels.get(current_module, logging.INFO)
        return module_level <= logging.DEBUG


class LoggingConfig:
    """Manages logging configuration for the simulation."""

    # Feature flag for furnace group debug output (set from YAML)
    FURNACE_GROUP_BREAKDOWN = True

    @classmethod
    @contextmanager
    def module_context(cls, module_name: str):
        """
        Set current module context for logging.

        This context manager sets thread-local state that ContextAwareFilter
        uses to determine whether DEBUG logs should be allowed.

        Args:
            module_name: Name of the module (geo/pam/tm)

        Example:
            with LoggingConfig.module_context("geo"):
                # DEBUG logs allowed based on geo configuration
                logger.debug("This is a geo debug message")
        """
        _current_module.name = module_name
        try:
            yield
        finally:
            _current_module.name = None

    @classmethod
    def configure_from_yaml(cls, yaml_path: str, cli_max_level: Optional[int] = None):
        """
        Load YAML configuration and set up context-aware filtering.

        Args:
            yaml_path: Path to logging_config.yaml file
            cli_max_level: Optional CLI-specified maximum level (acts as ceiling)

        Example YAML structure:
            version: 1
            global_level: WARNING
            features:
              furnace_group_breakdown: true
            modules:
              geo: DEBUG
              pam: INFO
              tm: INFO
            function_overrides:
              calculate_unit_production_cost: WARNING
            external:
              pyomo: ERROR
        """
        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        # Parse module levels
        module_levels = {}
        for module, level_str in config.get("modules", {}).items():
            level = getattr(logging, level_str)
            # Apply CLI ceiling if provided
            if cli_max_level:
                level = max(level, cli_max_level)
            module_levels[module] = level

        # Parse function overrides
        function_overrides = {}
        for func, level_str in config.get("function_overrides", {}).items():
            function_overrides[func] = getattr(logging, level_str)

        # Set feature flags
        features = config.get("features", {})
        cls.FURNACE_GROUP_BREAKDOWN = features.get("furnace_group_breakdown", True)

        # Create filter and formatter
        context_filter = ContextAwareFilter(module_levels, function_overrides, cli_max_level)
        formatter = ShortNameFormatter()

        root = logging.getLogger()
        root.addFilter(context_filter)

        # Ensure root logger has at least one handler
        if not root.handlers:
            stream_handler = logging.StreamHandler()
            stream_handler.setLevel(cli_max_level or logging.DEBUG)
            root.addHandler(stream_handler)
            root.setLevel(cli_max_level or logging.DEBUG)

        # Add filter and formatter to all handlers
        for h in root.handlers:
            h.addFilter(context_filter)
            h.setFormatter(formatter)

        # Set external logger levels
        for logger_name, level_str in config.get("external", {}).items():
            logging.getLogger(logger_name).setLevel(getattr(logging, level_str))

    @classmethod
    def configure_base_loggers(cls):
        """
        Configure basic logging as fallback when YAML not available.

        Sets reasonable defaults for common noisy loggers.
        """
        logging.getLogger("pyomo").setLevel(logging.ERROR)
        logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

    @classmethod
    @contextmanager
    def simulation_logging(cls, economic_model_name: str, suppress_debug: bool = False):
        """
        Context manager to set module context during economic model execution.

        Maps economic model names to module contexts for logging:
        - GeospatialModel → geo
        - PlantAgentsModel → pam
        - AllocationModel → tm

        Args:
            economic_model_name: Name of the economic model being run
            suppress_debug: Unused, kept for backward compatibility

        Example:
            with LoggingConfig.simulation_logging('PlantAgentsModel'):
                # Sets "pam" context for thread-local logging
                model.run()
        """
        # Map economic model to module context
        model_to_module = {
            "GeospatialModel": "geo",
            "PlantAgentsModel": "pam",
            "AllocationModel": "tm",
        }
        module = model_to_module.get(economic_model_name)

        logging.info(f"Running {economic_model_name} with configured logging levels")

        if module:
            with cls.module_context(module):
                yield
        else:
            # For other contexts (like DebugLogging), yield without module context
            yield
