# Steel-IQ Scripts

Utility scripts for Steel-IQ optimization testing and setup.

## setup_and_test.py

Comprehensive environment setup and testing script for all optimization implementations.

### Usage

```bash
# Run all checks and tests
python scripts/setup_and_test.py --all

# Check environment only (no tests)
python scripts/setup_and_test.py --check-only

# Run quick benchmark
python scripts/setup_and_test.py --benchmark

# Verbose output
python scripts/setup_and_test.py --all --verbose
```

### What It Checks

1. **Python Version**: Ensures Python 3.9+
2. **Hardware Detection**: Verifies M4 Pro detection (10P+4E cores)
3. **Dependencies**: Checks required packages (Pyomo, Pandas, NumPy, SciPy)
4. **PyTorch**: Checks Metal GPU support
5. **Module Tests**: Tests hardware detection, benchmarking, configuration
6. **GPU Tests**: Verifies GPU modules load correctly
7. **Quick Benchmark**: Runs a simple performance test

### Expected Output

```
======================================================================
                Steel-IQ Optimization Setup & Test
======================================================================

Starting environment setup and testing...
Platform: Darwin 23.0.0
Architecture: arm64

======================================================================
                      Checking Python Version
======================================================================

Python version: 3.11.5
✓ Python 3.11 is compatible

======================================================================
                       Detecting Hardware
======================================================================

Hardware detected: HardwareInfo(chip=Apple M4 Pro, cores=10P+4E, memory=24.0GB)
✓ Apple Silicon detected: Apple M4 Pro
  - Cores: 10P + 4E
  - Memory: 24.0 GB
  - Optimal workers (parallel): 9

...

======================================================================
                          Test Summary
======================================================================

Total tests: 9
Passed: 9
Failed: 0

  ✓ PASS - Python Version
  ✓ PASS - Hardware Detection
  ✓ PASS - Dependencies
  ✓ PASS - PyTorch
  ✓ PASS - Hardware Detection Module
  ✓ PASS - Benchmarking Framework
  ✓ PASS - Configuration System
  ✓ PASS - GPU Modules
  ✓ PASS - Quick Benchmark

✓ All tests passed! (9/9)

🎉 Environment is ready for Steel-IQ optimization!

Next steps:
  1. Review configuration: config/optimization_example.yaml
  2. Run full benchmark: python -m steelo.benchmarking.run_benchmark --years 2020-2025
  3. Compare solvers: python -m steelo.benchmarking.run_benchmark --compare-all
```

### Exit Codes

- `0`: All tests passed or most tests passed
- `1`: Multiple test failures

### Output Files

When running with `--benchmark`, creates:
- `test_benchmarks/quick_benchmark.csv` - Benchmark results in CSV
- `test_benchmarks/quick_benchmark_report.md` - Markdown report

## Integration with CI/CD

This script can be used in continuous integration:

```yaml
# .github/workflows/test.yml
- name: Setup and test optimizations
  run: python scripts/setup_and_test.py --all
```

## Troubleshooting

### PyTorch Not Found

```bash
pip install torch>=2.0.0
```

### Metal GPU Not Detected

Ensure you're running on Apple Silicon with macOS 12.3+:

```bash
python -c "import platform; print(f'{platform.system()} {platform.machine()}')"
# Expected: Darwin arm64
```

### Tests Failing

Run with verbose output to see detailed error messages:

```bash
python scripts/setup_and_test.py --all --verbose
```

## See Also

- `TESTING_GUIDE.md` - Comprehensive testing documentation
- `docs/IMPLEMENTATION_COMPLETE.md` - Implementation summary
- `config/README.md` - Configuration guide
