#!/usr/bin/env python3
"""
Demonstration of Steel-IQ optimization configuration system.

This script shows how to use the configuration-based solver selection
for Steel-IQ trade optimization.
"""

from pathlib import Path

from steelo.config.optimization_config import (
    OptimizationConfig,
    create_default_config,
    create_performance_config,
)


def demo_basic_usage():
    """Demonstrate basic configuration usage."""
    print("=" * 60)
    print("Demo 1: Basic Configuration Usage")
    print("=" * 60)

    # Default configuration (baseline solver)
    config = OptimizationConfig()
    print(f"\nDefault config: {config.solver_mode}")
    print(f"Workers: {config.workers}")
    print(f"Device: {config.geospatial_device}")

    # Create performance config
    perf_config = create_performance_config()
    print(f"\nPerformance config: {perf_config.solver_mode}")
    print(f"GPU preprocessing: {perf_config.gpu_preprocessing}")


def demo_yaml_loading():
    """Demonstrate loading configuration from YAML."""
    print("\n" + "=" * 60)
    print("Demo 2: Loading from YAML")
    print("=" * 60)

    yaml_path = Path("config/optimization_example.yaml")
    if yaml_path.exists():
        config = OptimizationConfig.from_yaml(yaml_path)
        print(f"\nLoaded from {yaml_path}")
        print(f"Solver mode: {config.solver_mode}")
        print(f"Workers: {config.workers}")
        print(f"Check accuracy: {config.check_accuracy}")
    else:
        print(f"\nYAML file not found: {yaml_path}")
        print("Run from project root directory")


def demo_programmatic_config():
    """Demonstrate creating configuration programmatically."""
    print("\n" + "=" * 60)
    print("Demo 3: Programmatic Configuration")
    print("=" * 60)

    # Create custom configuration
    config = OptimizationConfig(
        solver_mode="parallel_gasplan",
        workers=8,
        gpu_preprocessing=True,
        geospatial_device="mps",
        check_accuracy=True,
        tolerance=0.005,  # 0.5% tolerance
    )

    print(f"\nCustom config created:")
    print(f"  Solver mode: {config.solver_mode}")
    print(f"  Workers: {config.workers}")
    print(f"  GPU: {config.gpu_preprocessing}")
    print(f"  Device: {config.geospatial_device}")
    print(f"  Tolerance: {config.tolerance * 100}%")

    # Export to YAML
    output_path = Path("config/custom_config.yaml")
    output_path.parent.mkdir(exist_ok=True)
    config.to_yaml(output_path)
    print(f"\nExported to: {output_path}")


def demo_solver_factory():
    """Demonstrate using the solver factory."""
    print("\n" + "=" * 60)
    print("Demo 4: Solver Factory Usage")
    print("=" * 60)

    from steelo.domain.trade_modelling.solver_factory import (
        SolverFactory,
        get_available_solver_modes,
        is_solver_mode_implemented,
    )

    # Show available solver modes
    modes = get_available_solver_modes()
    print(f"\nAvailable solver modes: {modes}")

    # Check implementation status
    for mode in modes:
        implemented = is_solver_mode_implemented(mode)
        status = "✅ Implemented" if implemented else "⏳ Planned"
        print(f"  {mode:20s} - {status}")

    # Create factory and validate configuration
    config = OptimizationConfig(solver_mode="parallel", workers=4)
    factory = SolverFactory(config)

    print(f"\nFactory created for: {config.solver_mode}")

    # Validate configuration
    validation = factory.validate_configuration()
    print(f"\nConfiguration validation:")
    print(f"  Valid: {validation['valid']}")
    if validation["warnings"]:
        print(f"  Warnings:")
        for warning in validation["warnings"]:
            print(f"    - {warning}")

    # Get solver info
    info = factory.get_solver_info()
    print(f"\nSolver info:")
    for key, value in info.items():
        print(f"  {key}: {value}")


def demo_plant_agent_integration():
    """Demonstrate integration with AllocationModel."""
    print("\n" + "=" * 60)
    print("Demo 5: Plant Agent Integration")
    print("=" * 60)

    print("""
To use optimization config in your simulation:

```python
from steelo.config.optimization_config import OptimizationConfig
from steelo.economic_models.plant_agent import AllocationModel

# Option 1: Use default (baseline)
AllocationModel.run(bus)

# Option 2: Load from YAML
config = OptimizationConfig.from_yaml("config/optimization.yaml")
AllocationModel.run(bus, optimization_config=config)

# Option 3: Create programmatically
config = OptimizationConfig(
    solver_mode="parallel_gasplan",
    workers="auto",
    gpu_preprocessing=True,
)
AllocationModel.run(bus, optimization_config=config)
```

The configuration system is backward compatible:
- No config = baseline solver (default behavior)
- With config = uses configured solver
""")


def demo_solver_comparison():
    """Demonstrate solver comparison framework."""
    print("\n" + "=" * 60)
    print("Demo 6: Solver Comparison Framework")
    print("=" * 60)

    print("""
To compare different solvers:

```python
from steelo.testing.solver_comparison import compare_all_solvers

# Run comprehensive comparison
results = compare_all_solvers(
    trade_lp=your_model,
    tolerance=0.01,
    output_dir=Path("benchmarks")
)

# View results
for result in results:
    print(result.get_summary())
    print(f"Speedup: {result.speedup:.2f}x")
    print(f"Accuracy passed: {result.accuracy_passed}")
```

Generates:
- benchmarks/solver_comparison_results.csv
- benchmarks/solver_comparison_report.md
""")


def main():
    """Run all demonstrations."""
    print("\n")
    print("=" * 60)
    print("Steel-IQ Optimization Configuration Demo")
    print("=" * 60)
    print()

    demo_basic_usage()
    demo_yaml_loading()
    demo_programmatic_config()
    demo_solver_factory()
    demo_plant_agent_integration()
    demo_solver_comparison()

    print("\n" + "=" * 60)
    print("Demo Complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("1. Review config/README.md for detailed documentation")
    print("2. Customize config/optimization_example.yaml for your needs")
    print("3. Use OptimizationConfig in your simulations")
    print("4. Run solver comparisons to validate optimizations")
    print()


if __name__ == "__main__":
    main()
