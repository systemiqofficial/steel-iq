"""
Centralized logging configuration for the steel model simulation.

This module provides a structured way to configure logging levels for different
modules and economic models throughout the simulation process.

Key Features:
- Model-specific logging configurations via MODEL_CONFIGS
- Base logging configurations via BASE_LOGGERS
- Robust filtering to ensure logging levels are enforced even when logging.basicConfig is used
- Automatic restoration to base configuration after each model runs

Usage:
    The simulation_logging context manager applies model-specific logging configurations:

    with LoggingConfig.simulation_logging('AllocationModel'):
        # AllocationModel runs with its specific logging config
        # e.g., calculate_unit_production_cost set to WARNING

    # After context exits, BASE_LOGGERS configuration is restored

How it works:
    1. Sets logger levels according to MODEL_CONFIGS or BASE_LOGGERS
    2. Adds TargetedLevelFilter to root logger and handlers to enforce levels
    3. Filters only affect specified loggers, not interfering with others
    4. Cleans up filters and restores base configuration on exit

This approach ensures logging levels work correctly regardless of how logging
was initially configured (e.g., with basicConfig at DEBUG level).
"""

import logging
import yaml
import threading
from typing import Dict, Optional, Union
from contextlib import contextmanager


# Thread-local storage for current module context
_current_module = threading.local()


class ContextAwareFilter(logging.Filter):
    """
    Filter that allows/suppresses DEBUG logs based on current module context.

    Thread-local context tracks which module (geo/pam/tm) is currently executing.
    DEBUG logs only pass if current module's level is DEBUG in configuration.
    Non-DEBUG logs always pass through.
    """

    def __init__(self, module_levels: Dict[str, int], function_overrides: Dict[str, int]):
        """
        Initialise the context-aware filter.

        Args:
            module_levels: Mapping of module names (geo/pam/tm) to logging levels
            function_overrides: Mapping of function names to logging levels
        """
        super().__init__()
        self.module_levels = module_levels
        self.function_overrides = function_overrides

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
            return False  # No context set, suppress DEBUG

        module_level = self.module_levels.get(current_module, logging.INFO)
        return module_level <= logging.DEBUG


class LoggingConfig:
    """Manages logging configuration for different modules and economic models."""

    # Feature flags for debug outputs
    ENABLE_FURNACE_GROUP_DEBUG = True  # Set to False to disable furnace group debug output
    ENABLE_GEO_DEBUG = True  # Set to True to enable detailed geospatial debug output

    logging_level = logging.WARNING if ENABLE_GEO_DEBUG else logging.INFO  # Default logging level

    # Base logger configurations for all modules
    BASE_LOGGERS = {
        # General
        "__main__": logging_level,
        # simulation
        "steelo.simulation": logging_level,
        # Economic models
        "steelo.economic_models.plant_agent": logging_level,
        "steelo.economic_models.plant_agent.PlantAgentsModel.run": logging.ERROR if ENABLE_GEO_DEBUG else logging_level,
        "steelo.economic_models.plant_agent.AllocationModel.run": logging_level,
        "steelo.economic_models.plant_agent.GeospatialModel.run": logging.DEBUG if ENABLE_GEO_DEBUG else logging_level,
        # Domain models
        "steelo.domain.models": logging_level,
        "steelo.simulation.SimulationRunner.run": logging_level,
        "steelo.domain.models.get_bom_from_avg_boms": logging_level,
        "steelo.domain.models.optimal_technology": logging_level,
        "steelo.domain.models.update_furnace_and_plant_balance": logging_level,
        "steelo.domain.models.extract_price_from_costcurve": logging.ERROR if ENABLE_GEO_DEBUG else logging_level,
        "steelo.domain.models.evaluate_furnace_group_strategy": logging_level,
        "steelo.domain.models.evaluate_expansion": logging_level,
        "steelo.domain.models.debt_repayment_for_current_year": logging_level,
        # Domain costs
        "steelo.domain.calculate_costs.calculate_unit_production_cost": logging_level,
        "steelo.domain.calculate_costs.calculate_npv_full": logging_level,
        "steelo.domain.calculate_costs.calculate_npv_costs": logging_level,
        "steelo.domain.calculate_costs.stranding_asset_cost": logging_level,
        # Domain trade modelling
        "steelo.domain.trade_modelling": logging_level,
        "steelo.domain.trade_modelling.TM_PAM_connector.update_bill_of_materials": logging_level,
        "steelo.domain.trade_modelling.process_network_validator": logging_level,
        # GEO and new plant opening
        "steelo.domain.new_plant_opening": logging.DEBUG if ENABLE_GEO_DEBUG else logging.WARNING,
        "steelo.adapters.geospatial.geospatial_layers": logging.DEBUG if ENABLE_GEO_DEBUG else logging.WARNING,
        "steelo.adapters.geospatial.geospatial_calculations": logging.DEBUG if ENABLE_GEO_DEBUG else logging.WARNING,
        # Utilities
        "steelo.utilities.country_network_plotting": logging.WARNING,
        "steelo.utilities.plotting": logging.WARNING,
        # Other modules
        "pyomo": logging.ERROR,
    }

    # Model-specific logger overrides
    MODEL_CONFIGS = {
        "GeospatialModel": {
            "steelo.domain.models.extract_price_from_costcurve": logging.ERROR,
            "steelo.domain.calculate_costs": logging.WARNING,
            "steelo.domain.calculate_costs.calculate_npv_full": logging.WARNING,
            "steelo.domain.calculate_costs.calculate_npv_costs": logging.WARNING,
        },
        "PlantAgentsModel": {
            "steelo.domain.calculate_costs.calculate_unit_production_cost": logging.WARNING
            if ENABLE_GEO_DEBUG
            else logging.INFO,
        },
        "AllocationModel": {
            "steelo.domain.models.extract_price_from_costcurve": logging.ERROR,
            "steelo.domain.calculate_costs.calculate_unit_production_cost": logging.WARNING,
            "steelo.domain.trade_modelling": logging.WARNING if ENABLE_GEO_DEBUG else logging.INFO,
            "steelo.domain.trade_modelling.TM_PAM_connector": logging.WARNING if ENABLE_GEO_DEBUG else logging.INFO,
            "steelo.domain.trade_modelling.TM_PAM_connector.update_bill_of_materials": logging.WARNING
            if ENABLE_GEO_DEBUG
            else logging.INFO,
            "steelo.domain.trade_modelling.process_network_validator": logging_level,
        },
        "DebugLogging": {
            # Suppress verbose debug output when accessing properties during debug logging
            "steelo.domain.calculate_costs.calculate_unit_production_cost": logging.WARNING,
            "steelo.domain.models.extract_price_from_costcurve": logging.ERROR if ENABLE_GEO_DEBUG else logging.WARNING,
            "steelo.domain.models.debt_repayment_for_current_year": logging.WARNING,
        },
    }

    @classmethod
    def configure_base_loggers(cls):
        """Configure all base loggers with their default levels."""
        for logger_name, level in cls.BASE_LOGGERS.items():
            logger = logging.getLogger(logger_name)
            logger.setLevel(level)

    @classmethod
    def get_model_config(cls, economic_model_name: str) -> Dict[str, int]:
        """
        Get the logging configuration for a specific economic model.

        Args:
            economic_model_name: Name of the economic model (e.g., "PlantAgentsModel")

        Returns:
            Dictionary of logger names to their logging levels
        """
        # Start with base configuration
        config = cls.BASE_LOGGERS.copy()

        # Apply model-specific overrides if available
        if economic_model_name in cls.MODEL_CONFIGS:
            config.update(cls.MODEL_CONFIGS[economic_model_name])

        return config

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
              furnace_group_debug: true
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
        cls.ENABLE_FURNACE_GROUP_DEBUG = features.get("furnace_group_debug", True)

        # Create and attach filter to root logger
        context_filter = ContextAwareFilter(module_levels, function_overrides)
        root = logging.getLogger()
        root.addFilter(context_filter)

        # Set external logger levels
        for logger_name, level_str in config.get("external", {}).items():
            logging.getLogger(logger_name).setLevel(getattr(logging, level_str))

    @classmethod
    @contextmanager
    def simulation_logging(cls, economic_model_name: str, suppress_debug: bool = False):
        """
        Context manager to control logging based on the economic model being run.

        This sets the module context (geo/pam/tm) for thread-local logging control.
        For backward compatibility, also applies legacy MODEL_CONFIGS filtering.

        Args:
            economic_model_name: Name of the economic model being run
            suppress_debug: If True, suppress all DEBUG level logging

        Example:
            with LoggingConfig.simulation_logging('PlantAgentsModel'):
                # Sets "pam" context for thread-local logging
                # DEBUG logs allowed based on YAML configuration
        """
        # Map economic model to module context
        model_mapping = {
            "GeospatialModel": "geo",
            "PlantAgentsModel": "pam",
            "AllocationModel": "tm",
        }
        module = model_mapping.get(economic_model_name)

        # Get configuration for this economic model (legacy support)
        model_config = cls.get_model_config(economic_model_name)

        # Store filters we add so we can remove them later
        added_filters: list[tuple[Union[logging.Logger, logging.Handler], logging.Filter]] = []

        # Apply model-specific configuration (legacy support)
        for logger_name, desired_level in model_config.items():
            logger = logging.getLogger(logger_name)

            # Apply suppression if requested
            if suppress_debug and desired_level == logging.DEBUG:
                effective_level = logging.INFO
            else:
                effective_level = desired_level

            # Set the logger level
            logger.setLevel(effective_level)

            # Add a filter that blocks messages below the desired level for THIS specific logger
            # This ensures messages below the desired level are blocked even if handlers accept them
            if effective_level > logging.DEBUG:

                class TargetedLevelFilter(logging.Filter):
                    def __init__(self, logger_name, min_level):
                        self.logger_name = logger_name
                        self.min_level = min_level

                    def filter(self, record):
                        # If this record is from our target logger, apply level filtering
                        if record.name == self.logger_name or record.name.startswith(self.logger_name + "."):
                            return record.levelno >= self.min_level
                        # Let other loggers' messages through
                        return True

                level_filter = TargetedLevelFilter(logger_name, effective_level)

                # Add filter to the root logger and all its handlers
                # This catches messages as they propagate up
                root_logger = logging.getLogger()
                root_logger.addFilter(level_filter)
                added_filters.append((root_logger, level_filter))

                # Also add to all existing handlers on the root logger
                for handler in root_logger.handlers:
                    handler.addFilter(level_filter)
                    added_filters.append((handler, level_filter))

        logging.info(f"Running {economic_model_name} with configured logging levels")

        # Set module context for new YAML-based logging
        if module:
            with cls.module_context(module):
                try:
                    yield
                finally:
                    # Remove all filters we added
                    for filterable, filter_obj in added_filters:
                        try:
                            filterable.removeFilter(filter_obj)
                        except ValueError:
                            # Filter might have already been removed
                            pass

                    # ALWAYS restore to BASE_LOGGERS configuration
                    # This ensures each model starts fresh with the base configuration
                    for logger_name, base_level in cls.BASE_LOGGERS.items():
                        logger = logging.getLogger(logger_name)
                        logger.setLevel(base_level)
        else:
            # For other contexts (like DebugLogging), just yield without module context
            try:
                yield
            finally:
                # Remove all filters we added
                for filterable, filter_obj in added_filters:
                    try:
                        filterable.removeFilter(filter_obj)
                    except ValueError:
                        # Filter might have already been removed
                        pass

                # ALWAYS restore to BASE_LOGGERS configuration
                # This ensures each model starts fresh with the base configuration
                for logger_name, base_level in cls.BASE_LOGGERS.items():
                    logger = logging.getLogger(logger_name)
                    logger.setLevel(base_level)

    @classmethod
    def set_logger_level(cls, logger_name: str, level: int):
        """
        Set the logging level for a specific logger.

        Args:
            logger_name: Name of the logger to configure
            level: Logging level (e.g., logging.DEBUG, logging.INFO)
        """
        logger = logging.getLogger(logger_name)
        logger.setLevel(level)

    @classmethod
    def set_module_logging(cls, module_prefix: str, level: int):
        """
        Set logging level for all loggers under a module prefix.

        Args:
            module_prefix: Module prefix (e.g., "steelo.domain")
            level: Logging level to set
        """
        # Configure the parent logger
        logger = logging.getLogger(module_prefix)
        logger.setLevel(level)

        # Also configure any existing child loggers
        for name in logging.Logger.manager.loggerDict.keys():
            if name.startswith(module_prefix):
                logging.getLogger(name).setLevel(level)

    @classmethod
    def get_current_levels(cls) -> Dict[str, int]:
        """
        Get current logging levels for all configured loggers.

        Returns:
            Dictionary of logger names to their current levels
        """
        levels = {}
        for logger_name in cls.BASE_LOGGERS:
            logger = logging.getLogger(logger_name)
            levels[logger_name] = logger.level
        return levels

    @classmethod
    def configure_for_production(cls):
        """Configure logging for production environment (less verbose)."""
        production_levels = {
            "__main__": logging.INFO,
            "steelo": logging.INFO,
            "pyomo": logging.WARNING,
            "steelo.utilities": logging.WARNING,
            "steelo.domain.trade_modelling": logging.WARNING,
        }
        for logger_name, level in production_levels.items():
            cls.set_logger_level(logger_name, level)

    @classmethod
    def configure_for_development(cls):
        """Configure logging for development environment (more verbose)."""
        cls.configure_base_loggers()

    @classmethod
    def suppress_module(cls, module_name: str):
        """
        Suppress all logging from a specific module.

        Args:
            module_name: Name of the module to suppress
        """
        cls.set_logger_level(module_name, logging.CRITICAL)
