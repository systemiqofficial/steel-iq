# Architectural Decision Record: Project Infrastructure

```
Status: Proposed
Date: 2021-11-11
```

## Python Version

Just use the latest version of Python available at the time of development.
This ensures that we can leverage the latest language features and libraries,
as well as benefit from performance improvements and bug fixes.

## Package Management

We will use [uv](https://github.com/astral-sh/uv) for package management in Python.
For a convincing video on why you should use `uv`, see [this video](https://youtu.be/8UuW8o4bHbw?si=ArhdeGZ4Ee3zYb9Q).

Package Management also includes:

- **Dependency Management**: Which packages to install for production / development
- **Lockfile Management**: Ensuring that the same versions of packages are installed across different environments
- **Building and Publishing Packages**: Creating and distributing packages for reuse

## Virtual Environment

We will use `uv` for creating virtual environments in Python. By default it creates
a virtual environment in the `.venv` directory.

## Project Metadata

We will use `pyproject.toml` for project metadata and configuration.

## Code Formatting

We will use `ruff` for code formatting and basic linting in Python. We use
pre-commit hooks to ensure that the code is formatted correctly before it is
committed. Goal is to adhere to the [PEP 8](https://pep8.org/) style guide.

## Source Code Organization

- The source code lies in the `src` directory
- Tests lie in the `tests` directory