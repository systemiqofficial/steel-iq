# Steel-IQ Optimization Testing Guide

**Date**: 2025-11-10
**Target Hardware**: Apple M4 Pro (10P+4E cores, 24GB RAM, Metal GPU)
**Optimization Goal**: 100-150x speedup

---

## Quick Start

### 1. Setup and Test Environment

```bash
# Run complete setup and tests
python scripts/setup_and_test.py --all

# Check environment only
python scripts/setup_and_test.py --check-only

# Run quick benchmark
python scripts/setup_and_test.py --benchmark
```

### 2. Install Dependencies

If PyTorch is not installed:

```bash
# For Apple Silicon (M-series)
pip install torch>=2.0.0

# For CUDA (NVIDIA GPU)
pip install torch>=2.0.0 torchvision

# CPU only
pip install torch>=2.0.0 --extra-index-url https://download.pytorch.org/whl/cpu
```

### 3. Verify Hardware Detection

```python
from steelo.benchmarking.hardware_detector import detect_apple_silicon

hw = detect_apple_silicon()
print(hw)
# Expected: HardwareInfo(chip=Apple M4 Pro, cores=10P+4E, memory=24.0GB)
```

---

## Testing Strategy

### Phase 1: Environment Verification ✅

**Goal**: Ensure all dependencies and hardware are properly detected

**Commands**:
```bash
# Full environment check
python scripts/setup_and_test.py --check-only --verbose

# Test individual modules
python -c "from steelo.benchmarking.hardware_detector import detect_apple_silicon; print(detect_apple_silicon())"
python -c "import torch; print(f'PyTorch: {torch.__version__}, MPS: {torch.backends.mps.is_available()}')"
```

**Expected Results**:
- ✅ Python 3.9+
- ✅ M4 Pro detected (10P + 4E cores)
- ✅ PyTorch with MPS support
- ✅ All required packages installed

---

### Phase 2: Module Testing ✅

**Goal**: Verify each optimization module works independently

#### Test 1: Hardware Detection

```python
from steelo.benchmarking.hardware_detector import detect_apple_silicon

hw = detect_apple_silicon()
assert hw.is_apple_silicon == True
assert hw.performance_cores == 10
assert hw.efficiency_cores == 4
assert hw.optimal_workers('parallel') == 9  # 10 P-cores - 1
print("✓ Hardware detection working")
```

#### Test 2: Benchmarking Framework

```python
from steelo.benchmarking.performance_profiler import SolverBenchmark

benchmark = SolverBenchmark()

def test_function():
    return sum(range(1000))

result = benchmark.profile(
    func=test_function,
    component="test",
    operation="sum",
    iterations=100,
)

assert result.duration_ms > 0
assert result.iterations == 100
print(f"✓ Benchmarking working: {result.avg_duration_ms():.3f}ms avg")
```

#### Test 3: Configuration System

```python
from steelo.config.optimization_config import OptimizationConfig

# Test default
config = OptimizationConfig()
assert config.lp_solver_mode == "baseline"

# Test custom
config = OptimizationConfig(
    lp_solver_mode="parallel_gasplan",
    workers=10,
    gpu_preprocessing=True,
)
assert config.lp_solver_mode == "parallel_gasplan"
assert config.workers == 10

# Test YAML loading
config = OptimizationConfig.from_yaml("config/optimization_example.yaml")
print(f"✓ Configuration system working: mode={config.lp_solver_mode}")
```

#### Test 4: GPU Modules (Optional)

```python
# Test if PyTorch and GPU modules load
try:
    from steelo.domain.trade_modelling.gpu_matrix_builder import GPUMatrixBuilder
    from steelo.adapters.geospatial.gpu_geospatial import GPUGeospatialCalculator

    builder = GPUMatrixBuilder()
    calc = GPUGeospatialCalculator()

    print(f"✓ GPU modules loaded")
    print(f"  Matrix builder device: {builder.device}")
    print(f"  Geospatial device: {calc.device}")

    if builder.is_gpu_available():
        print("  ✓ Metal GPU acceleration available!")
    else:
        print("  ⚠ Using CPU fallback (PyTorch MPS not available)")

except ImportError as e:
    print(f"⚠ GPU modules not available (PyTorch not installed): {e}")
```

---

### Phase 3: Integration Testing 🔄

**Goal**: Test optimization implementations with mock/small data

#### Test 5: Parallel Solver (Mock Data)

```python
from steelo.domain.trade_modelling.parallel_solver import ParallelAllocationSolver
from steelo.benchmarking.hardware_detector import detect_apple_silicon

# Create solver
hw = detect_apple_silicon()
solver = ParallelAllocationSolver(
    message_bus=None,  # Mock bus
    batch_size=5,
    max_workers=hw.optimal_workers('parallel'),
)

print(f"✓ Parallel solver created")
print(f"  Workers: {solver.workers}")
print(f"  Batch size: {solver.batch_size}")

# Note: Full testing requires real Steel-IQ data
print("  ⚠ Full test requires MessageBus integration")
```

#### Test 6: GASPLAN Solver (Mock Data)

```python
from steelo.domain.trade_modelling.gasplan_solver import GASPLANDecompositionSolver

# Note: Requires real TradeLPModel for full testing
print("✓ GASPLAN solver module imports successfully")
print("  ⚠ Full test requires TradeLPModel integration")
```

#### Test 7: GPU Matrix Builder

```python
from steelo.domain.trade_modelling.gpu_matrix_builder import GPUMatrixBuilder
import numpy as np

try:
    import torch

    builder = GPUMatrixBuilder(benchmark=True)

    # Test small sparse matrix
    indices = np.array([[0, 1, 2], [0, 1, 2]])  # 3 non-zero entries
    values = np.array([1.0, 2.0, 3.0])
    shape = (3, 3)

    if builder.is_gpu_available():
        A_gpu = builder.build_constraint_matrix_gpu(
            indices=indices,
            values=values,
            shape=shape,
        )
        print("✓ GPU matrix construction working")
        print(f"  Device: {builder.device}")
    else:
        print("⚠ GPU not available, using CPU fallback")

except ImportError:
    print("⚠ PyTorch not installed, skipping GPU test")
```

#### Test 8: GPU Geospatial

```python
from steelo.adapters.geospatial.gpu_geospatial import GPUGeospatialCalculator
import numpy as np

try:
    import torch

    calc = GPUGeospatialCalculator(benchmark=True)

    # Test distance calculation
    lats = np.array([51.5074, 48.8566])  # London, Paris
    lons = np.array([-0.1278, 2.3522])
    target_lat, target_lon = 52.5200, 13.4050  # Berlin

    if calc.is_gpu_available:
        distances = calc.gpu_haversine_distance(
            lats, lons, target_lat, target_lon
        )
        print("✓ GPU geospatial calculations working")
        print(f"  London to Berlin: {distances[0]:.1f} km")
        print(f"  Paris to Berlin: {distances[1]:.1f} km")
    else:
        print("⚠ GPU not available, using CPU fallback")

except ImportError:
    print("⚠ PyTorch not installed, skipping GPU test")
```

---

### Phase 4: Performance Benchmarking 📊

**Goal**: Measure actual performance improvements

#### Benchmark 1: Baseline Performance

```bash
# Run baseline benchmark (5 years to start)
python -m steelo.benchmarking.run_benchmark \
    --years 2020-2024 \
    --mode baseline \
    --output-dir benchmarks/baseline

# Check results
cat benchmarks/baseline/benchmark_baseline_report.md
```

**Expected Output**:
```
Benchmark Results:
- Total years: 5
- Average time per year: ~500ms (placeholder)
- Peak memory: TBD
```

#### Benchmark 2: Compare All Solvers

```bash
# Compare all optimization modes
python -m steelo.benchmarking.run_benchmark \
    --years 2020-2024 \
    --compare-all \
    --output-dir benchmarks/comparison
```

**Expected Speedups**:
- Baseline: 1.0x (reference)
- Parallel: ~10x (if integrated)
- GASPLAN: ~2x (if integrated)
- Parallel+GASPLAN: ~20x (if integrated)

#### Benchmark 3: GPU Acceleration

```python
# Benchmark GPU matrix construction
from steelo.domain.trade_modelling.gpu_matrix_builder import benchmark_matrix_construction

results = benchmark_matrix_construction([
    (1000, 1000, 10000),   # Small problem
    (5000, 5000, 50000),   # Medium problem
    (10000, 10000, 100000) # Large problem
])

for size, (cpu_time, gpu_time) in zip(['small', 'medium', 'large'], results):
    speedup = cpu_time / gpu_time if gpu_time > 0 else 0
    print(f"{size}: CPU={cpu_time:.3f}s, GPU={gpu_time:.3f}s, Speedup={speedup:.2f}x")
```

```python
# Benchmark GPU geospatial
from steelo.adapters.geospatial.gpu_geospatial import benchmark_geospatial_operations

results = benchmark_geospatial_operations([
    (100, 100),     # 10k points
    (1000, 1000),   # 1M points
    (1800, 3600),   # 6.5M points (full globe at 0.1° resolution)
])

for size, times in results.items():
    cpu_time = times['cpu'][0]
    gpu_time = times['gpu'][0]
    speedup = cpu_time / gpu_time if gpu_time > 0 else 0
    print(f"{size}: Speedup={speedup:.2f}x")
```

---

### Phase 5: Accuracy Validation ✓

**Goal**: Ensure optimized solvers produce correct results (within 1% tolerance)

#### Validation Test

```python
from steelo.testing.solver_comparison import compare_all_solvers
from pathlib import Path

# Compare solvers (requires real TradeLPModel)
results = compare_all_solvers(
    trade_lp=your_trade_lp_model,  # From Steel-IQ simulation
    tolerance=0.01,  # 1% tolerance
    output_dir=Path("validation_results"),
)

# Check results
for result in results:
    print(result.get_summary())

    if result.accuracy_check:
        if result.accuracy_check['passed']:
            print(f"✓ {result.solver_name}: Accuracy validated")
        else:
            print(f"✗ {result.solver_name}: Accuracy failed")
            print(f"  Max difference: {result.accuracy_check['max_relative_diff']:.4f}")
            print(f"  Violations: {result.accuracy_check['violations']}")
```

---

### Phase 6: Full Simulation Testing 🚀

**Goal**: Run complete Steel-IQ simulation with optimizations

#### Full Simulation with Baseline

```python
from steelo.config.optimization_config import OptimizationConfig
from steelo.simulation import run_simulation

# Baseline configuration
config = OptimizationConfig(
    lp_solver_mode="baseline",
    check_accuracy=True,
)

# Run simulation
results = run_simulation(
    config=config,
    start_year=2020,
    end_year=2050,
)

print(f"Baseline simulation complete: {len(results)} years")
```

#### Full Simulation with Optimizations

```python
# Optimized configuration
config = OptimizationConfig.from_yaml("config/optimization_performance.yaml")

# Should have:
# - solver_mode: parallel_gasplan
# - workers: auto (will detect M4 Pro)
# - gpu_preprocessing: true
# - geospatial_device: mps

results_optimized = run_simulation(
    config=config,
    start_year=2020,
    end_year=2050,
)

print(f"Optimized simulation complete: {len(results_optimized)} years")

# Compare timing
# (This comparison requires simulation timing to be captured)
```

---

## Common Issues and Solutions

### Issue 1: PyTorch Not Found

**Symptoms**:
```
ImportError: No module named 'torch'
```

**Solution**:
```bash
pip install torch>=2.0.0
```

### Issue 2: Metal GPU Not Detected

**Symptoms**:
```
torch.backends.mps.is_available() returns False
```

**Possible Causes**:
- Not running on Apple Silicon
- PyTorch version < 2.0
- macOS version < 12.3

**Solution**:
```bash
# Check PyTorch version
python -c "import torch; print(torch.__version__)"

# Update if needed
pip install --upgrade torch

# Check system
python -c "import platform; print(f'System: {platform.system()}, Machine: {platform.machine()}')"
# Should show: System: Darwin, Machine: arm64
```

### Issue 3: Hardware Detection Shows Wrong Core Count

**Symptoms**:
```
HardwareInfo shows incorrect performance/efficiency core split
```

**Solution**:
The fallback uses known chip models. If detection fails:
```python
from steelo.config.optimization_config import OptimizationConfig

# Manually specify workers
config = OptimizationConfig(
    lp_solver_mode="parallel",
    workers=10,  # Specify based on your M4 Pro
)
```

### Issue 4: Memory Issues During Optimization

**Symptoms**:
```
MemoryError or system slowdown during parallel solving
```

**Solutions**:
1. Reduce batch size:
   ```python
   solver = ParallelAllocationSolver(
       message_bus=bus,
       batch_size=5,  # Reduce from default 10
   )
   ```

2. Reduce workers:
   ```python
   config = OptimizationConfig(
       solver_mode="parallel",
       workers=5,  # Use fewer cores
   )
   ```

3. Use GASPLAN to reduce problem size:
   ```python
   config = OptimizationConfig(
       solver_mode="gasplan",  # Reduces memory usage
   )
   ```

### Issue 5: Solver Results Don't Match Baseline

**Symptoms**:
```
accuracy_check['passed'] = False
max_relative_diff > 0.01
```

**Diagnosis**:
```python
from steelo.benchmarking.performance_profiler import SolverBenchmark

benchmark = SolverBenchmark()
comparison = benchmark.compare_solutions(
    baseline=baseline_solution,
    optimized=optimized_solution,
    tolerance=0.01,
)

print(f"Max difference: {comparison['max_relative_diff']:.6f}")
print(f"Violations: {comparison['violations']}")
print(f"Missing allocations: {comparison['missing_in_optimized']}")
print(f"Extra allocations: {comparison['extra_in_optimized']}")
```

**Solutions**:
- Check if problem is infeasible for optimized solver
- Verify warm-start is working correctly
- Increase GASPLAN iterations for better convergence
- Check constraint violations in solver output

---

## Performance Expectations

### M4 Pro (10P + 4E cores, 24GB)

| Optimization | Expected Speedup | Conservative | Optimistic |
|--------------|-----------------|--------------|------------|
| Parallel (10 P-cores) | 10x | 8x | 12x |
| GASPLAN decomposition | 2x | 1.5x | 3x |
| GPU matrix preprocessing | 1.2x | 1.1x | 1.5x |
| GPU geospatial | 10x | 5x | 15x |
| **Combined (parallel + GASPLAN)** | **20x** | **12x** | **30x** |
| **With GPU optimizations** | **100-150x** | **50x** | **200x** |

### Memory Usage

| Configuration | Expected Memory | Notes |
|---------------|----------------|-------|
| Baseline | 17.7GB | Current peak usage |
| GASPLAN | ~2GB | 10x reduction in LP size |
| Parallel (10 workers) | ~20GB | 10 LPs in memory |
| Parallel + GASPLAN | ~5GB | Optimal |

---

## Testing Checklist

Use this checklist to validate your installation:

### Environment Setup
- [ ] Python 3.9+ installed
- [ ] All required dependencies installed (Pyomo, Pandas, NumPy, SciPy)
- [ ] PyTorch installed with MPS support (optional but recommended)
- [ ] M4 Pro detected correctly (10P + 4E cores)

### Module Tests
- [ ] Hardware detection working
- [ ] Benchmarking framework functional
- [ ] Configuration system loads YAML correctly
- [ ] GPU modules import (if PyTorch installed)

### Performance Tests
- [ ] Baseline benchmark runs successfully
- [ ] Parallel solver can be instantiated
- [ ] GASPLAN solver module imports
- [ ] GPU matrix builder works (if PyTorch installed)
- [ ] GPU geospatial calculations work (if PyTorch installed)

### Integration Tests
- [ ] Configuration can switch between solver modes
- [ ] Accuracy validation framework works
- [ ] Benchmarking comparison runs

### Full Simulation (Final Validation)
- [ ] Baseline simulation completes
- [ ] Optimized simulation completes
- [ ] Results match within 1% tolerance
- [ ] Performance improvement measured
- [ ] Memory usage within limits

---

## Next Steps After Testing

Once all tests pass:

1. **Run full baseline benchmark** (30 years):
   ```bash
   python -m steelo.benchmarking.run_benchmark --years 2020-2050 --mode baseline
   ```

2. **Run optimized benchmark** (30 years):
   ```bash
   python -m steelo.benchmarking.run_benchmark --years 2020-2050 --mode parallel_gasplan
   ```

3. **Compare and validate**:
   ```bash
   python -m steelo.benchmarking.run_benchmark --years 2020-2050 --compare-all
   ```

4. **Generate performance report**:
   - Review `benchmarks/benchmark_report.md`
   - Check speedup metrics
   - Validate accuracy within tolerance

5. **Deploy to production** (if validation successful):
   - Update default configuration to use optimizations
   - Monitor first production run
   - Validate results against historical baseline

---

## Support and Troubleshooting

For issues:
1. Run `python scripts/setup_and_test.py --all --verbose`
2. Check logs in `benchmarks/` directory
3. Review documentation in `docs/` directory
4. Check configuration in `config/` directory

**Documentation**:
- `docs/IMPLEMENTATION_COMPLETE.md` - Full implementation summary
- `docs/focused_optimization_plan.md` - Optimization strategy
- `docs/planet_network_solver_analysis.md` - GASPLAN algorithm
- `docs/gpu_acceleration_analysis.md` - GPU feasibility analysis
- `config/README.md` - Configuration guide

---

**Document Version**: 1.0
**Last Updated**: 2025-11-10
**Target Hardware**: Apple M4 Pro
**Expected Speedup**: 100-150x
