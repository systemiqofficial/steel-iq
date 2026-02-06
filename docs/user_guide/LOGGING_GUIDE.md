# Logging Configuration Guide for Steel Model

## Quick Start

### Enable DEBUG logging for a module
Edit `logging_config.yaml` in the repository root:
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

## Log Format

Logs use a structured format with aligned columns:

```
INFO    | CORE | bootstrap: Using fixtures directory...
INFO    | TM   | set_up_steel_trade_lp: Setting up LP model...
WARNING | TM   | enforce_trade_tariffs_on_allocations: cannot find average prices
DEBUG   | PAM  | calculate_subsidies: Processing H2 subsidies for DEU
```

Format: `LEVEL   | MODULE | function_name: message`

| Column | Width | Description |
|--------|-------|-------------|
| LEVEL | 7 chars | DEBUG, INFO, WARNING, ERROR |
| MODULE | 4 chars | GEO, PAM, TM, or CORE (outside model context) |
| function | variable | Last component of logger name |

The MODULE column reflects which economic model is currently executing. The same function shows different prefixes depending on which model called it:
- `PAM  | calculate_subsidies` - called during PlantAgentsModel
- `GEO  | calculate_subsidies` - called during GeospatialModel

---

## Overview

The Steel Model uses YAML-based logging configuration with context-aware filtering. Key features:

- **Module-specific levels**: Control DEBUG output per model (geo/pam/tm)
- **Function overrides**: Suppress or enable specific functions
- **CLI ceiling**: `--log-level INFO` suppresses all DEBUG regardless of YAML
- **Thread-safe**: Context tracking works correctly across concurrent calls

### Configuration File

The logging configuration file `logging_config.yaml` is located in the repository root and ships with sensible defaults.

---

## How It Works

### Module Contexts

The simulation runs three economic models in sequence. Each model has its own logging context:

| Model | Context | YAML Key | Description |
|-------|---------|----------|-------------|
| GeospatialModel | geo | `modules.geo` | Determines optimal locations for new steel plants |
| PlantAgentsModel | pam | `modules.pam` | Simulates individual plant investment decisions |
| AllocationModel | tm | `modules.tm` | Optimises steel trade flows between regions |

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
  furnace_group_breakdown: true  # Show detailed furnace-level logs

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

### CLI Level Behaviour

The `--log-level` flag controls logging differently inside and outside module contexts:

**Inside module contexts (geo/pam/tm):**
- Acts as a ceiling for the YAML module levels
- `--log-level INFO` suppresses DEBUG even if YAML says `geo: DEBUG`
- The more restrictive level (CLI or YAML) wins

**Outside module contexts (orchestration, data preparation, startup):**
- CLI level is used directly
- `--log-level DEBUG` shows DEBUG logs from code outside models
- `--log-level INFO` shows only INFO+ from outside models

| CLI Level | Inside Context | Outside Context |
|-----------|----------------|-----------------|
| DEBUG | Respects YAML | Shows DEBUG |
| INFO | Suppresses DEBUG | Shows INFO+ |
| WARNING | Only WARNING+ | Only WARNING+ |

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

## Troubleshooting

### DEBUG logs not appearing?

1. **Check module context**: Is the function called from within a model (geo/pam/tm)?
   - Code that runs outside model contexts (orchestration, data preparation) suppresses DEBUG by design
   - Use INFO level for messages that should always appear

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

---

## Adding Custom Logging

This section is for contributors adding logging to new functions.

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

The `function_overrides` feature requires function-level loggers. The filter extracts the function name from the logger name:

```python
# Logger name: steelo.domain.models.calculate_renewable_costs
#                                    ^^^^^^^^^^^^^^^^^^^^^^^^
#                                    Filter extracts this part
```

With a module-level logger (`logging.getLogger(__name__)`), the filter cannot match individual function overrides. Function-level loggers enable:

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

### Best Practices

1. **Use function-level loggers**: `logging.getLogger(f"{__name__}.function_name")`
2. **Choose appropriate levels**:
   - DEBUG: Verbose details (filtered by context)
   - INFO: Key milestones (always visible)
   - WARNING: Unexpected situations
   - ERROR: Errors requiring attention
3. **Include context in messages**: `f"[FUNCTION]: description {value}"`
4. **Clean up**: Remove temporary DEBUG settings after fixing issues