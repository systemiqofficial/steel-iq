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
from typing import Dict, Optional, Union
from contextlib import contextmanager


class LoggingConfig:
    """Manages logging configuration for different modules and economic models."""

    # Feature flags for debug outputs
    ENABLE_FURNACE_GROUP_DEBUG = True  # Set to False to disable furnace group debug output
    ENABLE_GEO_DEBUG = False  # Set to True to enable detailed geospatial debug output

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
    def simulation_logging(cls, economic_model_name: str, suppress_debug: bool = False):
        """
        Context manager to control logging based on the economic model being run.

        This method uses a robust filtering approach that works even when logging.basicConfig
        sets the root logger to DEBUG. It adds targeted filters that only affect specific
        loggers defined in MODEL_CONFIGS, without interfering with other loggers.

        Args:
            economic_model_name: Name of the economic model being run
            suppress_debug: If True, suppress all DEBUG level logging

        Example:
            with LoggingConfig.simulation_logging('AllocationModel'):
                # calculate_unit_production_cost will only log WARNING and above
                # Other loggers work normally
        """
        # Get configuration for this economic model
        model_config = cls.get_model_config(economic_model_name)

        # Store filters we add so we can remove them later
        added_filters: list[tuple[Union[logging.Logger, logging.Handler], logging.Filter]] = []

        # Apply model-specific configuration
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


# Additional utility functions for common logging patterns


def configure_simulation_logging(config: Optional[Dict[str, int]] = None):
    """
    Configure logging for simulation with optional custom configuration.

    Args:
        config: Optional dictionary of logger names to levels
    """
    if config:
        for logger_name, level in config.items():
            LoggingConfig.set_logger_level(logger_name, level)
    else:
        LoggingConfig.configure_base_loggers()


def get_logger_for_module(module_name: str) -> logging.Logger:
    """
    Get a properly configured logger for a module.

    Args:
        module_name: Name of the module

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(module_name)
    # Ensure it has the right level from our config if available
    if module_name in LoggingConfig.BASE_LOGGERS:
        logger.setLevel(LoggingConfig.BASE_LOGGERS[module_name])
    return logger


# Centralized logger instances to avoid circular imports
new_plant_logger = logging.getLogger("steelo.domain.new_plant_opening")
plant_agents_logger = logging.getLogger("steelo.economic_models.plant_agent.PlantAgentsModel.run")
tm_logger = logging.getLogger("steelo.economic_models.plant_agent.AllocationModel.run")
geo_logger = logging.getLogger("steelo.economic_models.plant_agent.GeospatialModel.run")
geo_layers_logger = logging.getLogger("steelo.adapters.geospatial.geospatial_layers")
