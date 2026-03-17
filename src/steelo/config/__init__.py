"""Configuration package for Steel-IQ."""

# Import project_root from the parent config module for backward compatibility
from pathlib import Path

# Get project root the same way as in steelo/config.py
project_root = Path(__file__).resolve(strict=True).parent.parent.parent.parent

__all__ = ["project_root"]
