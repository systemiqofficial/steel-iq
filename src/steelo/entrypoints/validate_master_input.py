#!/usr/bin/env python
"""
Command-line tool to validate master input Excel files.

Usage:
    validate-master-input <excel_file>
    validate-master-input --analyze <excel_file>
"""

import argparse
import sys
from pathlib import Path
from steelo.adapters.dataprocessing.master_excel_validator import (
    MasterExcelValidator,
    print_validation_report,
    analyze_excel_structure,
)


def analyze_structure(excel_path: Path):
    """Analyze and print Excel file structure"""
    print(f"\nAnalyzing structure of: {excel_path}")
    print("=" * 60)

    structure = analyze_excel_structure(excel_path)

    if "error" in structure:
        print(f"Error analyzing file: {structure['error']}")
        return

    for sheet_name, sheet_info in structure.items():
        print(f"\nSheet: '{sheet_name}'")
        print("-" * 40)

        if isinstance(sheet_info, dict):
            if "error" in sheet_info:
                print(f"  Error: {sheet_info['error']}")
            else:
                print(f"  Shape: {sheet_info.get('shape', 'Unknown')}")
                print(f"  Columns ({len(sheet_info.get('columns', []))}):")
                for col in sheet_info.get("columns", [])[:20]:  # Show first 20 columns
                    print(f"    - {col}")
                if len(sheet_info.get("columns", [])) > 20:
                    print(f"    ... and {len(sheet_info['columns']) - 20} more columns")


def validate_file(excel_path: Path, verbose: bool = False):
    """Validate master input file and print results"""
    print(f"\nValidating: {excel_path}")
    print("-" * 60)

    validator = MasterExcelValidator()
    report = validator.validate_file(excel_path)

    print_validation_report(report)

    if verbose and report.has_errors():
        print("\nHint: Use --analyze to explore the Excel file structure")

    return 0 if not report.has_errors() else 1


def main():
    parser = argparse.ArgumentParser(
        description="Validate master input Excel files for steel model simulations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  validate-master-input input.xlsx
  validate-master-input --analyze input.xlsx
  validate-master-input --verbose input.xlsx
        """,
    )

    parser.add_argument("excel_file", type=Path, help="Path to the master input Excel file")

    parser.add_argument("--analyze", action="store_true", help="Analyze Excel structure instead of validating")

    parser.add_argument("--verbose", "-v", action="store_true", help="Show additional information")

    args = parser.parse_args()

    # Check if file exists
    if not args.excel_file.exists():
        print(f"Error: File not found: {args.excel_file}")
        return 1

    # Run analysis or validation
    if args.analyze:
        analyze_structure(args.excel_file)
        return 0
    else:
        return validate_file(args.excel_file, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
