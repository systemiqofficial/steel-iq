# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

from __future__ import annotations

import os
import subprocess
from typing import Any

project = "Steel Model"
author = "Steel Model Team"
copyright = "2025, Steel Model Team"
version = "2.0.0"  # Can be automated from pyproject.toml later
release = version

# Track whether we are building the public site (set via `-t public`)
IS_PUBLIC_BUILD = False
_sphinx_tags: Any = globals().get("tags")
if _sphinx_tags is not None and hasattr(_sphinx_tags, "has"):
    IS_PUBLIC_BUILD = _sphinx_tags.has("public")

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "myst_parser",
    "sphinx_reredirects",
    # Future: 'sphinx.ext.autodoc' when API docs needed
    # Future: 'sphinx.ext.napoleon' for Google-style docstrings
    # Future: 'sphinx.ext.autodoc_typehints' for type hint rendering
]

# Old page path -> new location, relative to the old page. Keeps published links working after a move.
redirects = {
    "domain_simulation_logic/co2_storage_gate": "plant_agent_model/co2_storage_gate.html",
    "domain_simulation_logic/capacity_replacement_policy": "plant_agent_model/capacity_replacement_policy.html",
    "domain_simulation_logic/capacity_replacement_policy_reference": (
        "plant_agent_model/capacity_replacement_policy_reference.html"
    ),
    "domain_simulation_logic/plant_agent_model/plant_agent_model_logic": (
        "overview_plant_agent_model.html#how-the-agents-decide"
    ),
    "user_guide/commandline_entrypoints": "cli_commands.html#data-preparation-commands",
    "domain_simulation_logic/environment": "overarching_elements/environment.html",
    "domain_simulation_logic/sub_national_geography": "overarching_elements/sub_national_geography.html",
    "domain_simulation_logic/checkpoint": "overarching_elements/checkpoint.html",
    "domain_simulation_logic/outputs_and_postprocessing": "overarching_elements/outputs_and_postprocessing.html",
}

templates_path = ["_templates"]
exclude_patterns = [
    "_build",  # Build output directory
    "Thumbs.db",  # Windows
    ".DS_Store",  # macOS
    "**/.ipynb_checkpoints",  # Jupyter
]

# -- Source configuration ----------------------------------------------------

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# Default root document for internal builds
root_doc = "README"

PUBLIC_EXCLUDE_PATTERNS = [
    "README.md",  # Internal front page; its toctrees would leak into the public sidebar
    "architecture/adr/**",
    "dashboard/custom_repositories.md",
    "dashboard/parallel_workers.md",
    "data_management/**",
    "development/**",
    "legacy_docs/**",
]

if IS_PUBLIC_BUILD:
    root_doc = "public_index"
    exclude_patterns.extend(PUBLIC_EXCLUDE_PATTERNS)
    redirects["README"] = "public_index.html"
else:
    exclude_patterns.append("public_index.md")

# -- MyST parser configuration -----------------------------------------------

myst_enable_extensions = [
    "colon_fence",  # ::: fences for directives
    "deflist",  # Definition lists
    "tasklist",  # Task lists (- [ ] and - [x])
    "fieldlist",  # Field lists
]

myst_heading_anchors = 4

# Suppress warnings for known issues in existing documentation
# These should be fixed over time but don't block the documentation build
suppress_warnings = [
    "myst.header",  # Non-consecutive header levels (existing docs)
    "misc.highlighting_failure",  # Unknown lexers (mermaid, dotenv, etc.)
    "toc.excluded",  # Internal toctree entries dropped from public build
    "toc.not_readable",  # Internal-only documents excluded from build
]

# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "furo"
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
    # Future: Add logo, colors, etc. as needed
}
html_static_path = ["_static"]
html_css_files = []  # Custom CSS files can be added here
# Base URL is only relevant for the published public documentation
html_baseurl = "https://systemiqofficial.github.io/steel-iq/public_index.html" if IS_PUBLIC_BUILD else ""


def _compute_short_commit_sha() -> str:
    env_sha = os.environ.get("GITHUB_SHA")
    if env_sha:
        return env_sha[:7]

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return "unknown"

    return result.stdout.strip() or "unknown"


html_context = {
    "commit_short_sha": _compute_short_commit_sha(),
    "is_public_build": IS_PUBLIC_BUILD,
}

# -- Future API documentation setup ------------------------------------------
# When API documentation is needed, uncomment and configure:
#
# extensions.extend([
#     'sphinx.ext.autodoc',
#     'sphinx.ext.napoleon',
#     'sphinx.ext.autodoc_typehints',
# ])
#
# autodoc_typehints = 'description'  # Better type hint rendering
#
# # Add Python path if needed:
# import os
# import sys
# sys.path.insert(0, os.path.abspath('../src'))
