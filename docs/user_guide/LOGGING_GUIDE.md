# Logging Configuration Guide for Steel Model

## Overview

The steel model uses a centralized logging configuration system located in `src/steelo/logging_config.py`. This system allows you to:
- Set different logging levels for different modules
- Apply specific logging configurations for each economic model
- Easily add new logging configurations without modifying simulation code
- Define common settings once in BASE_LOGGERS that apply to all models

## How It Works

### 1. Base Configuration

All default logging levels are defined in `LoggingConfig.BASE_LOGGERS`:

```python
BASE_LOGGERS = {
    "__main__": logging.DEBUG,
    "steelo.simulation": logging.DEBUG,
    "steelo.economic_models.AllocationModel": logging.WARNING,
    "pyomo": logging.WARNING,
    # ... more loggers
}
```

These are applied at module import time when `simulation.py` is loaded.

### 2. Model-Specific Overrides

Each economic model can have its own logging configuration in `LoggingConfig.MODEL_CONFIGS`:

```python
MODEL_CONFIGS = {
    "GeospatialModel": {
        "steelo.domain.models": logging.WARNING,  # Override to WARNING
        # ... more overrides
    },
    "PlantAgentsModel": {
        "steelo.domain.models": logging.DEBUG,    # Override to DEBUG
        # ... more overrides
    },
}
```

### 3. Automatic Application with Robust Filtering

When a simulation runs an economic model, the logging configuration is automatically applied using a robust filtering approach:

```python
# In simulation.py
def run_simulation(self) -> None:
    model_name = self.economic_model.__class__.__name__
    with LoggingConfig.simulation_logging(model_name):
        self.economic_model.run(self.bus)  # Logging configured for this model
```

**Important**: The `simulation_logging` context manager uses a sophisticated filtering approach to ensure logging levels are enforced even when `logging.basicConfig` sets the root logger to DEBUG. It:
1. Sets the logger levels according to MODEL_CONFIGS or BASE_LOGGERS
2. Adds `TargetedLevelFilter` instances to the root logger and handlers
3. These filters only affect the specific loggers defined in configurations, not all loggers
4. Automatically removes filters and restores BASE_LOGGERS configuration when the context exits

This approach ensures that logging levels work correctly regardless of how logging was initially configured elsewhere in the codebase (e.g., if some module calls `logging.basicConfig(level=logging.DEBUG)`)

### 4. General vs Model-Specific Logging

**Important**: If a logger is defined in `BASE_LOGGERS` but NOT in a model's `MODEL_CONFIGS`, the base level applies to that model. You only need to add entries to `MODEL_CONFIGS` when you want to override the base setting.

For example:
- `pyomo: logging.WARNING` in BASE_LOGGERS applies to ALL models
- `steelo.utilities.plotting: logging.WARNING` in BASE_LOGGERS applies to ALL models
- No need to repeat these in each MODEL_CONFIGS unless you want a different level

This follows the DRY (Don't Repeat Yourself) principle and makes maintenance easier.

## Adding New Logging Configurations

### Example 1: Adding Logging to a Specific Function

Let's say you want to add detailed logging to a function `calculate_renewable_costs` in the module `steelo.domain.renewables`, but only when running the `GeospatialModel`.

**Step 1: Add the logger to BASE_LOGGERS** (in `logging_config.py`):

```python
BASE_LOGGERS = {
    # ... existing loggers ...
    "steelo.domain.renewables.calculate_renewable_costs": logging.WARNING,  # Default level
}
```

**Step 2: Add model-specific override** (in `logging_config.py`):

```python
MODEL_CONFIGS = {
    "GeospatialModel": {
        # ... existing overrides ...
        "steelo.domain.renewables.calculate_renewable_costs": logging.DEBUG,  # DEBUG only for GeospatialModel
    },
    # Other models will use the default WARNING level
}
```

**Step 3: Use the logger in your function**:

```python
# In steelo/domain/renewables.py
import logging

logger = logging.getLogger(f"{__name__}.calculate_renewable_costs")

def calculate_renewable_costs(params):
    logger.debug(f"Starting calculation with params: {params}")
    
    # Your calculation logic
    result = do_calculation(params)
    
    logger.debug(f"Calculation complete. Result: {result}")
    return result
```

### Example 2: Adding Logging to Multiple Functions in a Module

If you want to control logging for multiple functions in `steelo.domain.capacity_planning`:

**Step 1: Add module-level logger** (in `logging_config.py`):

```python
BASE_LOGGERS = {
    # ... existing loggers ...
    "steelo.domain.capacity_planning": logging.INFO,  # Default for all functions
    "steelo.domain.capacity_planning.optimize": logging.WARNING,  # Specific function override
}
```

**Step 2: Add model-specific configurations**:

```python
MODEL_CONFIGS = {
    "PlantAgentsModel": {
        # ... existing overrides ...
        "steelo.domain.capacity_planning": logging.DEBUG,  # All functions DEBUG
        "steelo.domain.capacity_planning.optimize": logging.DEBUG,  # Specific function also DEBUG
    },
    "AllocationModel": {
        # ... existing overrides ...
        "steelo.domain.capacity_planning": logging.WARNING,  # All functions WARNING
        "steelo.domain.capacity_planning.optimize": logging.INFO,  # But optimize at INFO
    },
}
```

**Step 3: Use in your module**:

```python
# In steelo/domain/capacity_planning.py
import logging

# Module-level logger
logger = logging.getLogger(__name__)

# Function-specific logger
optimize_logger = logging.getLogger(f"{__name__}.optimize")

def plan_capacity(data):
    logger.debug(f"Planning capacity with data: {data}")
    # ... function logic ...
    
def optimize(constraints):
    optimize_logger.debug(f"Starting optimization with constraints: {constraints}")
    optimize_logger.info(f"Using solver: HiGHS")
    # ... optimization logic ...
```

### Example 3: Real Implementation - calculate_costs.py Functions

This example shows how we implemented model-specific logging for NPV calculation functions that should only output DEBUG messages when called from PlantAgentsModel:

**Step 1: Modified the functions to use function-specific loggers**:

```python
# In calculate_costs.py
def calculate_npv_full(...):
    # Use function-specific logger that respects the centralized configuration
    func_logger = logging.getLogger(f"{__name__}.calculate_npv_full")
    
    func_logger.debug(f"[NPV FULL] Capacity: {capacity:,.0f} kt")
    # ... rest of function uses func_logger instead of logger

def calculate_unit_production_cost(...):
    # Use function-specific logger
    func_logger = logging.getLogger(f"{__name__}.calculate_unit_production_cost")
    
    func_logger.debug("[UNIT PRODUCTION COST]: Inputs:")
    # ... rest of function uses func_logger
```

**Step 2: Added to BASE_LOGGERS in logging_config.py**:

```python
BASE_LOGGERS = {
    # ... existing loggers ...
    "steelo.domain.calculate_costs": logging.WARNING,
    "steelo.domain.calculate_costs.calculate_unit_production_cost": logging.WARNING,
    "steelo.domain.calculate_costs.calculate_npv_full": logging.WARNING,
    "steelo.domain.calculate_costs.calculate_npv_costs": logging.WARNING,
    "steelo.domain.calculate_costs.stranding_asset_cost": logging.WARNING,
}
```

**Step 3: Added model-specific overrides**:

```python
MODEL_CONFIGS = {
    "PlantAgentsModel": {
        # ... other configs ...
        # Enable DEBUG for calculate_costs functions ONLY in PlantAgentsModel
        "steelo.domain.calculate_costs": logging.DEBUG,
        "steelo.domain.calculate_costs.calculate_unit_production_cost": logging.DEBUG,
        "steelo.domain.calculate_costs.calculate_npv_full": logging.DEBUG,
        "steelo.domain.calculate_costs.calculate_npv_costs": logging.DEBUG,
        "steelo.domain.calculate_costs.stranding_asset_cost": logging.DEBUG,
    },
    # GeospatialModel and AllocationModel don't override these,
    # so they use the BASE_LOGGERS setting (WARNING)
}
```

**Result**:
- When PlantAgentsModel runs: All DEBUG messages from these functions are displayed
- When GeospatialModel runs: Only WARNING and ERROR messages are shown (DEBUG suppressed)
- When AllocationModel runs: Same as GeospatialModel - only WARNING and ERROR

### Example 4: Temporary Verbose Logging for Debugging

If you need temporary verbose logging for debugging a specific issue:

**Option 1: Add a temporary override**:

```python
# In logging_config.py, temporarily add to MODEL_CONFIGS:
"PlantAgentsModel": {
    # ... existing overrides ...
    "steelo.domain.problem_module": logging.DEBUG,  # Temporary for debugging
    "steelo.domain.problem_module.specific_function": logging.DEBUG,
}
```

**Option 2: Use environment-based configuration**:

```python
# In your module
import logging
import os

# Set logger level based on environment variable
logger = logging.getLogger(__name__)
if os.getenv("DEBUG_PROBLEM_MODULE"):
    logger.setLevel(logging.DEBUG)
```

Then run with: `DEBUG_PROBLEM_MODULE=1 python run_simulation.py`

## Common Patterns

### 1. Hierarchical Logger Names

Use dot notation to create a hierarchy:

```python
"steelo.domain.models": logging.INFO,  # Parent level
"steelo.domain.models.Plant": logging.DEBUG,  # More specific
"steelo.domain.models.Plant.calculate_cost": logging.DEBUG,  # Most specific
```

### 2. Suppressing Noisy Libraries

To suppress verbose third-party libraries:

```python
BASE_LOGGERS = {
    "pyomo": logging.WARNING,  # Only warnings and errors
    "numpy": logging.ERROR,     # Only errors
    "pandas": logging.CRITICAL, # Essentially silent
}
```

### 3. Model-Specific Debug Mode

To enable debug mode for everything when running a specific model:

```python
MODEL_CONFIGS = {
    "DebugModel": {
        "steelo": logging.DEBUG,  # Everything under steelo package
    },
}
```

## Quick Reference

### Logging Levels (from most to least verbose)
- `logging.DEBUG` - Detailed diagnostic information
- `logging.INFO` - General informational messages
- `logging.WARNING` - Warning messages
- `logging.ERROR` - Error messages
- `logging.CRITICAL` - Critical errors only

### Key Functions in LoggingConfig

```python
# Configure all base loggers
LoggingConfig.configure_base_loggers()

# Set a specific logger's level
LoggingConfig.set_logger_level("steelo.domain.models", logging.DEBUG)

# Set all loggers under a module prefix
LoggingConfig.set_module_logging("steelo.domain", logging.INFO)

# Configure for production (less verbose)
LoggingConfig.configure_for_production()

# Configure for development (more verbose)
LoggingConfig.configure_for_development()

# Suppress a module entirely
LoggingConfig.suppress_module("noisy.module")
```

## Best Practices

1. **Use hierarchical names**: Structure your logger names to match your module structure
2. **Set appropriate defaults**: Use WARNING or INFO as defaults, DEBUG only when needed
3. **Model-specific overrides**: Only override for models that need different logging
4. **Clean up**: Remove temporary debug configurations after fixing issues
5. **Document changes**: Add comments when adding unusual logging configurations
6. **Don't repeat yourself**: Define common settings in BASE_LOGGERS, not in each MODEL_CONFIGS

## Frequently Asked Questions

### Q: If I want a logger to have the same level across all models, where do I define it?

**A:** Define it ONLY in `BASE_LOGGERS`. You don't need to add it to any `MODEL_CONFIGS`. The base level will automatically apply to all models unless specifically overridden.

Example:
```python
BASE_LOGGERS = {
    "pyomo": logging.WARNING,  # This applies to ALL models
    "steelo.utilities.plotting": logging.WARNING,  # This also applies to ALL models
}

# No need to add these to MODEL_CONFIGS unless you want a different level for a specific model
```

### Q: When should I add a logger to MODEL_CONFIGS?

**A:** Only when you want that model to use a DIFFERENT level than what's defined in BASE_LOGGERS.

Example:
```python
BASE_LOGGERS = {
    "steelo.domain.models": logging.WARNING,  # Default for all models
}

MODEL_CONFIGS = {
    "PlantAgentsModel": {
        "steelo.domain.models": logging.DEBUG,  # Override to DEBUG for this model only
    },
    # GeospatialModel and AllocationModel will use WARNING from BASE_LOGGERS
}
```

### Q: How do I clean up duplicate entries in MODEL_CONFIGS?

**A:** If you see the same logger with the same level in multiple MODEL_CONFIGS, move it to BASE_LOGGERS and remove from all MODEL_CONFIGS:

```python
# Before (duplicated):
MODEL_CONFIGS = {
    "GeospatialModel": {
        "pyomo": logging.WARNING,
    },
    "PlantAgentsModel": {
        "pyomo": logging.WARNING,
    },
    "AllocationModel": {
        "pyomo": logging.WARNING,
    },
}

# After (cleaned up):
BASE_LOGGERS = {
    "pyomo": logging.WARNING,  # Moved here
}
MODEL_CONFIGS = {
    # pyomo entries removed from all models
}
```

## Troubleshooting

### Logger not working as expected?

1. Check the logger name matches exactly:
   ```python
   logger = logging.getLogger(__name__)
   print(f"Logger name: {logger.name}")  # Verify the name
   ```

2. Verify the configuration is applied:
   ```python
   print(f"Logger level: {logger.level}")
   print(f"Effective level: {logger.getEffectiveLevel()}")
   ```

3. Ensure BASE_LOGGERS has your logger:
   ```python
   from steelo.logging_config import LoggingConfig
   print(LoggingConfig.BASE_LOGGERS.get("your.logger.name"))
   ```

### Logger levels not being respected?

If you've configured a logger to WARNING but still see DEBUG messages, this might be due to `logging.basicConfig` being called elsewhere with `level=logging.DEBUG`. The robust filtering approach in `simulation_logging` handles this automatically, but if you're not using the context manager:

1. **Check if basicConfig is being called**: Search for `logging.basicConfig` in the codebase
2. **Use the context manager**: Ensure your code runs within `LoggingConfig.simulation_logging()`
3. **Manual filtering**: If you can't use the context manager, add a filter manually:

```python
class LevelFilter(logging.Filter):
    def __init__(self, level):
        self.level = level
    
    def filter(self, record):
        return record.levelno >= self.level

logger = logging.getLogger("your.logger.name")
logger.setLevel(logging.WARNING)
logger.addFilter(LevelFilter(logging.WARNING))
```

### Need to debug the logging configuration itself?

Add this to your code:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger().handlers[0].setLevel(logging.DEBUG)
```

This will show all log messages regardless of configuration.

### Understanding the Robust Filtering System

The `simulation_logging` context manager uses `TargetedLevelFilter` to ensure logging levels are enforced. If you need to understand what's happening:

1. The filter is added to both the root logger and its handlers
2. It only filters messages from specific loggers (not all loggers)
3. Filters are automatically removed when the context exits

Example of what happens internally:
```python
# When AllocationModel runs with calculate_unit_production_cost set to WARNING:
# 1. Logger level is set to WARNING
# 2. TargetedLevelFilter(logger_name="steelo.domain.calculate_costs.calculate_unit_production_cost", min_level=WARNING) is added
# 3. Filter blocks DEBUG and INFO messages from that specific logger
# 4. Other loggers are unaffected
# 5. When context exits, filter is removed and BASE_LOGGERS configuration is restored
```