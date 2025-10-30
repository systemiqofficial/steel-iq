# List available commands (default)
default:
    @just --list

# Build documentation (incremental by default)
docs:
    uv run sphinx-build -b html docs docs/_build/html

# Build documentation from scratch (force rebuild)
docs-clean-build:
    uv run sphinx-build -aE -b html docs docs/_build/html

# Build public documentation subset
docs-public:
    uv run sphinx-build -b html -t public docs docs/_build/public_html

# Build + serve public docs locally
docs-public-serve:
    just docs-public
    cd docs/_build/public_html && python3 -m http.server 8000

# Clean build + warnings-as-errors for public docs
docs-public-check:
    @echo "Cleaning public build directory..."
    @uv run python -c "import shutil; shutil.rmtree('docs/_build/public_html', ignore_errors=True)"
    @echo "Building public documentation (clean build)..."
    @uv run sphinx-build -aE -W -b html -t public docs docs/_build/public_html

# Build and serve documentation locally with auto-reload
docs-serve:
    uv run sphinx-autobuild docs docs/_build/html --open-browser

# Build and serve documentation (headless - no browser)
docs-serve-headless:
    uv run sphinx-autobuild docs docs/_build/html

# Clean build artifacts (cross-platform)
docs-clean:
    uv run python -c "import shutil; shutil.rmtree('docs/_build', ignore_errors=True)"

# Verify documentation builds successfully (for pre-merge checks)
# Cleans build dir first to remove orphaned HTML from deleted docs
# Uses -aE for clean build and -W to treat warnings as errors
docs-check:
    @echo "Cleaning build directory..."
    @uv run python -c "import shutil; shutil.rmtree('docs/_build', ignore_errors=True)"
    @echo "Building documentation (clean build)..."
    @uv run sphinx-build -aE -W -b html docs docs/_build/html
    @echo "✓ Documentation build succeeded"

# Strict documentation check (optional - adds -n for nitpicky reference checking)
docs-check-strict:
    @echo "Cleaning build directory..."
    @uv run python -c "import shutil; shutil.rmtree('docs/_build', ignore_errors=True)"
    @echo "Building documentation (strict mode)..."
    @uv run sphinx-build -aE -W -n -b html docs docs/_build/html
    @echo "✓ Documentation build succeeded (strict)"

# Run linting
lint:
    uv run ruff check src/

# Run tests
test:
    uv run pytest

# Run type checking
typecheck:
    uv run mypy src/
