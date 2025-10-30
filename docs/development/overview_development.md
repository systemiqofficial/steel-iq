# Development Guide Overview

Welcome to the Steel Model Development Guide. This section contains documentation for developers contributing to the Steel Model project, covering development tools, workflows, and best practices.

## Contents

### [Style Guide and Variable Naming](style_guide_and_variable_naming.md)
Coding standards and conventions for the Steel Model project. Covers:
- **General style guide**: PEP 8 compliance and formatting standards
- **Variable naming**: snake_case for variables and functions
- **Class naming**: CamelCase conventions
- **Constants**: UPPERCASE_SNAKE_CASE conventions
- **Function naming**: Descriptive names and boolean prefixes (is_, has_)
- **Common pitfalls**: Avoiding ambiguous names, abbreviations, and magic numbers
- **Parameter dictionary**: Standard names for common parameters (materials_demand_cost, etc.)

### [Just Commands Reference](just_commands.md)
Complete reference for the Just task runner used in this project. Covers:
- **Installing Just**: Setup instructions for macOS and other platforms
- **Documentation commands**: Building, serving, and checking documentation
  - `just docs` - Incremental build for fast iteration
  - `just docs-serve` - Auto-reload server with browser
  - `just docs-check` - Required validation before merging (treats warnings as errors)
  - `just docs-clean` - Remove build artifacts
- **Code quality commands**: Linting, testing, and type checking
  - `just lint` - Run Ruff linter
  - `just test` - Run pytest test suite
  - `just typecheck` - Run mypy type checker
- **Adding documentation**: Guidelines for creating new docs files
- **Troubleshooting**: Common issues and solutions

### [Electron Desktop App](electron_app.md)
Development and maintenance guide for the STEEL-IQ Electron desktop application. Covers:
- **Overview**: Bundled Django app, Python environment, dependencies, and background worker
- **Icon management**: Source icon requirements and platform-specific icon generation
  - Using `png2icons` for high-quality Windows icons
  - Generating `.icns` (macOS), `.ico` (Windows), and `.png` (Linux) icons
  - Icon configuration in electron-builder
- **Building the application**: Quick builds for development and full production builds
- **GitHub Actions**: Automated builds for Windows and macOS via CI/CD
- **Updating icons**: Step-by-step process for icon updates

### [Create ModelRun Native Validation](create_modelrun_native_validation.md)
Validation framework for model run creation in the Django web interface. Covers:
- Validation rules for simulation parameters
- Input data validation
- Error handling and user feedback
- Native Django form validation patterns

### [Technical Debt](technical_debt.md)
Documentation of known technical debt and areas for improvement. Helps developers understand:
- Current technical limitations
- Areas requiring refactoring
- Future improvement opportunities
- Migration plans and deprecations