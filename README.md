# Steel Model

A comprehensive simulation system for modeling the global steel industry.

## Documentation

- **[Public site](https://systemiqofficial.github.io/steel-iq/)** – Full documentation

## Development Quick Reference

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and [just](https://github.com/casey/just), then bootstrap dependencies:

```bash
uv sync
uvx pre-commit install
```

### Core Checks

- `just test` – Run the full pytest suite
- `just typecheck` – Run mypy over `src/`
- `just lint` – Ruff lint

### Documentation

- `just docs` – Incremental Sphinx build
- `just docs-check` – Clean public build (used in CI)
- `just docs-serve` / `just docs-serve-headless` – Live preview
- `just docs-clean-build` – Force a full rebuild
- `just docs-clean` – Remove build artifacts

Run `just` to list every command. See the public docs for detailed guides.
