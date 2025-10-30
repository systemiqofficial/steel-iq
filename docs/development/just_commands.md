# Just Commands Reference

This project uses [Just](https://github.com/casey/just) as a task runner to simplify common development and documentation tasks.

## Installing Just

### macOS (Homebrew)

```bash
brew install just
```

This is the recommended installation method for macOS users.

### Other Platforms (Future Work)

Support for other platforms (Linux, Windows) is documented as future work. If you need to install Just on other platforms, please refer to the [official Just installation guide](https://github.com/casey/just#installation).

## Quick Reference

Run `just` without arguments to see all available commands:

```bash
just
```

This will display a list of all recipes with their descriptions.

## Documentation Commands

### `just docs`

Build the documentation incrementally (fastest for iteration).

```bash
just docs
```

The built documentation will be available in `docs/_build/html/`. Open `docs/_build/html/index.html` in your browser to view it.

### `just docs-serve`

Build and serve the documentation locally with auto-reload and automatically open your browser.

```bash
just docs-serve
```

This command:
- Builds the documentation
- Starts a local web server
- Opens your default browser
- Watches for changes and rebuilds automatically

**To stop the server:** Press `Ctrl+C` in your terminal.

### `just docs-serve-headless`

Build and serve the documentation without opening a browser. Useful for SSH sessions or headless macOS environments.

```bash
just docs-serve-headless
```

Navigate to the URL shown in the terminal output (typically `http://127.0.0.1:8000`) to view the documentation.

**To stop the server:** Press `Ctrl+C` in your terminal.

### `just docs-clean-build`

Build the documentation from scratch, forcing a complete rebuild.

```bash
just docs-clean-build
```

This is useful if you've made structural changes or want to ensure a clean build state.

### `just docs-clean`

Remove all documentation build artifacts.

```bash
just docs-clean
```

This cleans the `docs/_build/` directory.

### `just docs-check`

Verify that the documentation builds successfully without warnings or errors. **Required before merging changes.**

```bash
just docs-check
```

This command:
- Removes the entire build directory to catch orphaned HTML from deleted files
- Rebuilds the documentation from scratch
- Treats all warnings as errors
- Fails if there are any issues

**Use this before creating a PR or committing documentation changes.**

### `just docs-check-strict`

Optional strict documentation check that also catches all missing references.

```bash
just docs-check-strict
```

This adds extra validation but may be too strict initially. Use for comprehensive validation when needed.

## Code Quality Commands

### `just lint`

Run code linting with Ruff.

```bash
just lint
```

### `just test`

Run the test suite with pytest.

```bash
just test
```

### `just typecheck`

Run static type checking with mypy.

```bash
just typecheck
```

## How Just Works

Just uses a `justfile` in the project root that defines all available commands. Each command is called a "recipe" and can contain one or more shell commands.

All commands in this project use `uv run` to ensure:
- Correct Python 3.13 environment
- Proper dependency resolution
- Isolation from system Python

## Adding Documentation

When you add new documentation files:

1. Create your `.md` file in the appropriate directory under `docs/`
2. Use lowercase-with-hyphens for filenames (e.g., `my-new-guide.md`)
3. Add the file to the relevant `toctree` directive in `docs/index.md` or a section landing page
4. Run `just docs-check` to verify the build succeeds
5. View your changes with `just docs-serve`

See the [Navigation Contract](../index.md) section in the main index for details on how documentation navigation works.

## Troubleshooting

### "command not found: just"

Make sure Just is installed and available in your PATH. Run `which just` to verify.

### Documentation build fails

1. Make sure you've run `uv sync` to install all dependencies
2. Check that all files referenced in toctrees exist
3. Look for warnings in the build output
4. Run `just docs-clean` and then `just docs-check` for a fresh build

### Auto-reload not working

If `just docs-serve` doesn't reload automatically when you make changes:
1. Stop the server with `Ctrl+C`
2. Run `just docs-clean`
3. Start again with `just docs-serve`

## Further Reading

- [Just documentation](https://just.systems/)
- [Sphinx documentation](https://www.sphinx-doc.org/)
- [MyST Parser (Markdown for Sphinx)](https://myst-parser.readthedocs.io/)
- [Furo theme](https://pradyunsg.me/furo/)
