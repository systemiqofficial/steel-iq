# Steel-IQ Optimization Configuration

This directory contains configuration files for Steel-IQ optimization solvers.

## Configuration Files

### `optimization_example.yaml`
Default configuration with explanatory comments. Use as a template for custom configurations.

### `optimization_performance.yaml`
High-performance configuration that enables all available optimizations.

## Configuration Structure

```yaml
optimization:
  lp_solver:
    mode: baseline              # Solver implementation
    workers: auto               # Number of parallel workers
    gpu_preprocessing: false    # GPU preprocessing

  geospatial:
    device: auto                # Geospatial computation device

  validation:
    check_accuracy: true        # Validate against baseline
    tolerance: 0.01             # Accuracy tolerance (1%)

  detect_apple_silicon: true
  optimize_for_m4: true
```

## Solver Modes

### `baseline` (default)
Standard Pyomo/HiGHS solver. Always available and fully implemented.

**Use when:**
- Running production simulations
- Validating other solvers
- Maximum stability is required

### `parallel` (future)
Parallel decomposition solver using multiple CPU cores.

**Use when:**
- Large problem instances
- Multi-core CPU available
- Faster solve times needed

### `gasplan` (future)
Specialized network solver for minimum-cost flow problems.

**Use when:**
- Network structure is dominant
- Memory efficiency is critical
- Faster iterations needed

### `parallel_gasplan` (future)
Combined parallel decomposition with Gasplan network solving.

**Use when:**
- Maximum performance needed
- Large problem with network structure
- Multi-core CPU available

## Usage

### Python Code

```python
from steelo.config.optimization_config import OptimizationConfig
from steelo.economic_models.plant_agent import AllocationModel

# Load from YAML
config = OptimizationConfig.from_yaml("config/optimization_performance.yaml")

# Or create programmatically
config = OptimizationConfig(
    solver_mode="parallel_gasplan",
    workers=8,
    gpu_preprocessing=True,
)

# Use in AllocationModel
AllocationModel.run(bus, optimization_config=config)
```

### Default Behavior

If no configuration is provided, the system uses baseline solver:

```python
# Uses baseline solver (backward compatible)
AllocationModel.run(bus)

# Equivalent to:
AllocationModel.run(bus, optimization_config=OptimizationConfig())
```

## Testing and Comparison

Compare different solver implementations:

```python
from steelo.testing.solver_comparison import compare_all_solvers

# Compare all solvers against baseline
results = compare_all_solvers(
    trade_lp=model,
    tolerance=0.01,
    output_dir=Path("benchmarks")
)

# Results include timing, memory, and accuracy metrics
for result in results:
    print(result.get_summary())
```

## Hardware Detection

The configuration system automatically detects:

- **CPU cores**: Auto-configures worker count
- **Apple Silicon**: Enables MPS for geospatial operations
- **NVIDIA GPUs**: Enables CUDA if available
- **M4 chips**: Applies M4-specific optimizations

Override auto-detection:

```yaml
optimization:
  lp_solver:
    workers: 4  # Force 4 workers instead of auto-detect

  geospatial:
    device: cpu  # Force CPU instead of auto-detect

  detect_apple_silicon: false
  optimize_for_m4: false
```

## Validation

Optimized solvers are automatically validated against baseline:

```yaml
optimization:
  validation:
    check_accuracy: true  # Enable validation
    tolerance: 0.01       # Allow 1% difference
```

Validation ensures:
- Solutions are within tolerance
- No allocations are missing
- Constraint violations are detected

## Examples

### Production (stable, validated)
```yaml
optimization:
  lp_solver:
    mode: baseline
    workers: auto
```

### Development (fast iteration)
```yaml
optimization:
  lp_solver:
    mode: parallel_gasplan
    workers: 4
  validation:
    check_accuracy: false  # Skip validation for speed
```

### Benchmarking (comprehensive comparison)
```python
from pathlib import Path
from steelo.config.optimization_config import OptimizationConfig
from steelo.testing.solver_comparison import compare_all_solvers

# Run comprehensive comparison
results = compare_all_solvers(
    trade_lp=your_model,
    tolerance=0.01,
    output_dir=Path("benchmarks")
)

# Generates:
# - benchmarks/solver_comparison_results.csv
# - benchmarks/solver_comparison_report.md
```

## Implementation Status

| Solver Mode        | Status              | Notes                          |
|--------------------|---------------------|--------------------------------|
| `baseline`         | ✅ Implemented      | Pyomo/HiGHS, production-ready  |
| `parallel`         | ⏳ Planned (Phase 2)| Parallel decomposition         |
| `gasplan`          | ⏳ Planned (Phase 4)| Network flow solver            |
| `parallel_gasplan` | ⏳ Planned (Phase 5)| Combined parallel + Gasplan    |

**Note**: Unimplemented solvers fall back to baseline with a warning message.

## Troubleshooting

### GPU preprocessing enabled but no GPU detected
```
WARNING: GPU preprocessing enabled but PyTorch not installed.
```
**Solution**: Install PyTorch or set `gpu_preprocessing: false`

### More workers than CPUs
```
WARNING: Configured 16 workers but only 8 CPUs available.
```
**Solution**: Reduce `workers` or use `auto`

### Advanced solver not implemented
```
WARNING: Parallel solver not yet implemented. Falling back to baseline.
```
**Expected**: Advanced solvers are placeholders. System uses baseline until implemented.

## See Also

- `src/steelo/config/optimization_config.py` - Configuration implementation
- `src/steelo/domain/trade_modelling/solver_factory.py` - Solver factory
- `src/steelo/testing/solver_comparison.py` - Testing framework
- `docs/optimization_phases.md` - Implementation roadmap
