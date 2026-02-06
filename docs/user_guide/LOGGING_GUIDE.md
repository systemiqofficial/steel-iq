# Logging Configuration Guide for Steel Model

## Quick Start

### Enable DEBUG logging for a module
Edit `logging_config.yaml` at repo root:
```yaml
modules:
  geo: DEBUG   # GeospatialModel - set to DEBUG
  pam: INFO    # PlantAgentsModel
  tm: INFO     # AllocationModel (Trade Model)
```

### Add logging to a new function
```python
import logging

def my_function(params):
    logger = logging.getLogger(f"{__name__}.my_function")
    logger.debug(f"Starting with params: {params}")
    # ... function logic ...
    logger.info("Completed successfully")
```

### Suppress a noisy function
Add to `logging_config.yaml`:
```yaml
function_overrides:
  noisy_function_name: WARNING  # Only WARNING+ from this function
```

### Run simulation with DEBUG output
```bash
source .venv/bin/activate
run_simulation --master-excel input.xlsx --log-level DEBUG --end-year 2027 > debug.log 2>&1
```

---

## Overview

The steel model uses YAML-based logging configuration with context-aware filtering. Key features:

- **Module-specific levels**: Control DEBUG output per model (geo/pam/tm)
- **Function overrides**: Suppress or enable specific functions
- **CLI ceiling**: `--log-level INFO` suppresses all DEBUG regardless of YAML
- **Thread-safe**: Context tracking works correctly across concurrent calls

### Configuration Files
- **YAML config**: `logging_config.yaml` (repo root)
- **Python module**: `src/steelo/logging_config.py`

---

## How It Works

### Module Contexts

Each economic model runs within a logging context:

| Model | Context | YAML Key |
|-------|---------|----------|
| GeospatialModel | geo | `modules.geo` |
| PlantAgentsModel | pam | `modules.pam` |
| AllocationModel | tm | `modules.tm` |

DEBUG logs only appear when:
1. The current module context's level is DEBUG in YAML
2. CLI `--log-level` allows DEBUG (not overridden to INFO/WARNING)
3. No function override suppresses it

INFO, WARNING, and ERROR logs always appear regardless of module context.

### YAML Configuration Structure

```yaml
version: 1
global_level: WARNING

features:
  furnace_group_debug: true  # Feature flag example

modules:
  geo: DEBUG   # Enables DEBUG for GeospatialModel
  pam: INFO    # Only INFO+ for PlantAgentsModel
  tm: INFO     # Only INFO+ for AllocationModel

function_overrides:
  calculate_unit_production_cost: WARNING  # Suppress even when module is DEBUG
  my_debug_function: DEBUG                 # Enable even when module is INFO

external:
  pyomo: ERROR  # Suppress noisy third-party libraries
```

### CLI Ceiling

The `--log-level` flag acts as a ceiling:
- `--log-level DEBUG`: Respects YAML module levels
- `--log-level INFO`: Suppresses ALL DEBUG, regardless of YAML
- `--log-level WARNING`: Only WARNING+ logs appear

---

## Adding Logging to Functions

### Pattern: Function-Level Logger

Always use function-level loggers (not module-level):

```python
import logging

def calculate_renewable_costs(params):
    logger = logging.getLogger(f"{__name__}.calculate_renewable_costs")

    logger.debug(f"Starting calculation with params: {params}")
    result = do_calculation(params)
    logger.debug(f"Calculation complete. Result: {result}")

    return result
```

**Why function-level loggers?**

The `function_overrides` feature in YAML requires function-level loggers to work. When a log
record is created, the `ContextAwareFilter` extracts the function name from the logger name:

```python
# Logger name: steelo.domain.models.calculate_renewable_costs
#                                    ^^^^^^^^^^^^^^^^^^^^^^^^
#                                    Filter extracts this part
```

With a module-level logger (`logging.getLogger(__name__)`), the filter would only see `models`
and cannot match individual function overrides. Function-level loggers enable:

- Per-function suppression of noisy DEBUG output
- Per-function enabling of DEBUG in otherwise INFO-only modules
- Granular control without modifying code

### For Class Methods

```python
class MyClass:
    def my_method(self):
        logger = logging.getLogger(f"{__name__}.MyClass.my_method")
        logger.debug("Method executing")
```

### Enable DEBUG for Your Function

If your function runs within a module context (geo/pam/tm), DEBUG logs appear when that module is set to DEBUG in YAML.

To enable DEBUG for a specific function regardless of module level:
```yaml
function_overrides:
  your_function_name: DEBUG
```

---

## Common Tasks

### Temporarily Enable Verbose Logging

**Option 1**: Edit YAML (recommended)
```yaml
modules:
  pam: DEBUG  # Temporarily set to DEBUG
```

**Option 2**: Use function override
```yaml
function_overrides:
  function_to_debug: DEBUG
```

Remember to revert after debugging.

### Suppress Noisy Third-Party Libraries

```yaml
external:
  pyomo: ERROR
  matplotlib.font_manager: ERROR
  numpy: ERROR
```

### Debug a Specific Function Only

Set module to INFO but enable the specific function:
```yaml
modules:
  pam: INFO

function_overrides:
  the_function_to_debug: DEBUG
```

---

## Logging Levels Reference

From most to least verbose:
- `DEBUG` - Detailed diagnostic information
- `INFO` - General informational messages
- `WARNING` - Warning messages
- `ERROR` - Error messages
- `CRITICAL` - Critical errors only

---

## Best Practices

1. **Use function-level loggers**: `logging.getLogger(f"{__name__}.function_name")`
2. **Choose appropriate levels**:
   - DEBUG: Verbose details (filtered by context)
   - INFO: Key milestones (always visible)
   - WARNING: Unexpected situations
   - ERROR: Errors requiring attention
3. **Include context in messages**: `f"[FUNCTION]: description {value}"`
4. **Clean up**: Remove temporary DEBUG settings after fixing issues
5. **Use British English**: "organised", "analysed", "behaviour"

---

## Troubleshooting

### DEBUG logs not appearing?

1. **Check module context**: Is the function called from geo/pam/tm context?
   - Functions in `simulation.py` (orchestration) run outside module context
   - DEBUG is suppressed outside module context by design

2. **Check YAML module level**: Is the module set to DEBUG?
   ```yaml
   modules:
     geo: DEBUG  # Must be DEBUG, not INFO
   ```

3. **Check CLI level**: Are you running with `--log-level INFO`?
   - INFO ceiling suppresses all DEBUG

4. **Check function override**: Is there an override suppressing it?
   ```yaml
   function_overrides:
     your_function: WARNING  # This would suppress DEBUG
   ```

### Verify logger name

```python
logger = logging.getLogger(f"{__name__}.my_function")
print(f"Logger name: {logger.name}")  # Should be: steelo.domain.module.my_function
```

### Check current configuration

```python
from steelo.logging_config import LoggingConfig
# Feature flags
print(f"Furnace debug: {LoggingConfig.ENABLE_FURNACE_GROUP_DEBUG}")
```

---

## Architecture Details

### Context-Aware Filtering

The `ContextAwareFilter` class controls DEBUG log visibility:

1. **Function override check**: If function has override, use that level
2. **Non-DEBUG passthrough**: INFO/WARNING/ERROR always allowed
3. **Module context check**: For DEBUG, check if current module allows it

Thread-local storage (`_current_module`) tracks which model is executing, ensuring correct filtering even when functions are called from different contexts.

### Automatic Context Setting

The `simulation_logging()` context manager automatically sets module context:

```python
# In simulation.py - handled automatically
with LoggingConfig.simulation_logging("PlantAgentsModel"):
    # Context "pam" is set for this block
    model.run()
```

### Files Outside Module Context

The module context system (geo/pam/tm) is designed for the **modelling run** where
DEBUG granularity matters. Code that runs outside these contexts uses standard logging:

**Simulation orchestration** (`simulation.py`):
- Year progress, timing, model sequencing
- Uses INFO level - always visible
- No DEBUG control needed for orchestration

**Data preparation** (`data-prepare` CLI):
- Uses Rich console for user-facing output
- Underlying modules (`excel_reader.py`, `preparation.py`) have logging calls
- INFO/WARNING/ERROR appear; DEBUG suppressed
- No module context needed - data prep is a separate workflow

**Application startup** (`bootstrap.py`):
- Configuration loading, initialisation
- INFO/WARNING/ERROR always log

**Plotting utilities** (`plotting.py`):
- Mixed context - called from GEO, PAM, and outside contexts
- Currently uses module-level logger, DEBUG suppressed everywhere
- Will be rethought during plotting revamp (future work)

For all files outside module context:
- INFO/WARNING/ERROR always log
- DEBUG is suppressed (by design - no module context set)
- Use INFO level for important messages
