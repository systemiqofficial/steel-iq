"""
CLI entrypoints for developer tools.
"""

import os
import fnmatch
import argparse
from pathlib import Path


def llm_content():
    """
    Output all relevant code / documentation in the project including
    the relative path and content of each file.
    """

    def echo_filename_and_content(files):
        """Print the relative path and content of each file."""
        for f in files:
            print(f)
            contents = f.read_text()
            relative_path = f.relative_to(project_root)
            print(relative_path)
            print("---")
            print(contents)
            print("---")

    parser = argparse.ArgumentParser(description="Output project code/documentation for LLM context")
    parser.add_argument(
        "--exclude-dirs",
        nargs="+",
        default=[
            ".venv",
            "node_modules",
            "dist",
            "build",
            "htmlcov",
            "output",
            "data",
            "outputs",
            ".pytest_cache",
            "django-bundle",
        ],
        help="Directories to exclude",
    )
    parser.add_argument(
        "--patterns",
        nargs="+",
        default=["*.py", "*.rst", "*.js", "*.ts", "*.html", "*.md"],
        help="File patterns to include",
    )
    parser.add_argument(
        "--filenames-only",
        action="store_true",
        default=False,
    )

    args = parser.parse_args()

    project_root = Path.cwd()
    # Exclude files and directories. This is tuned to make the project fit into the
    # 200k token limit of the claude 3 models.
    exclude_files = {}
    exclude_dirs = set(args.exclude_dirs)
    patterns = args.patterns
    all_files = []

    for root, dirs, files in os.walk(project_root):
        root = Path(root)
        # d is the plain directory name
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for pattern in patterns:
            for filename in fnmatch.filter(files, pattern):
                if filename not in exclude_files:
                    path = root / filename
                    all_files.append(path)

    if args.filenames_only:
        for path in all_files:
            print(path)
    else:
        echo_filename_and_content(all_files)
