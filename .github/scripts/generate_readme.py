#!/usr/bin/env python3
"""
Generate a README file for platform-specific standalone builds.

The script reads configuration exclusively from environment variables so it can
be invoked safely from GitHub Actions without relying on heredocs inside YAML.
"""

from __future__ import annotations

import os
import sys
import textwrap

REQUIRED_ENV_VARS = {
    "README_PLATFORM",
    "README_COMMIT_SHORT",
    "README_COMMIT_FULL",
    "README_BRANCH",
    "README_BUILD_DATE",
    "README_TOTAL_SIZE",
    "README_FILE_COUNT",
    "README_ZIP_SIZE",
    "README_ZIP_FILENAME",
    "README_INSTALL_STEP",
    "README_REQUIREMENTS",
    "README_EXECUTABLE",
    "README_OUTPUT_PATH",
}


def main() -> int:
    env = os.environ
    missing = sorted(var for var in REQUIRED_ENV_VARS if var not in env)
    if missing:
        missing_str = ", ".join(missing)
        sys.stderr.write(
            f"Missing required environment variables: {missing_str}\n",
        )
        return 1

    content = textwrap.dedent(
        f"""\
        # STEEL-IQ Application with Task Worker

        **Build Information:**
        - Platform: {env["README_PLATFORM"]}
        - Commit: {env["README_COMMIT_SHORT"]} (full: {env["README_COMMIT_FULL"]})
        - Branch: {env["README_BRANCH"]}
        - Build Date: {env["README_BUILD_DATE"]}
        - Original Size: {env["README_TOTAL_SIZE"]} MB ({env["README_FILE_COUNT"]} files)
        - Compressed Size: {env["README_ZIP_SIZE"]} MB

        **Features:**
        - Django web server for the user interface
        - Django Task Worker (django_tasks) for background job processing
        - Fully portable Python environment (no dependencies required)

        **Installation Instructions:**
        1. Download the archive file: {env["README_ZIP_FILENAME"]}
        2. Extract the archive to your desired location
        3. Navigate to the extracted folder
        4. {env["README_INSTALL_STEP"]}

        **System Requirements:**
        {env["README_REQUIREMENTS"]}

        **What's Running:**
        When you start the application, the following services automatically start:
        1. Django database migrations (ensures database is up to date)
        2. Django web server (serves the user interface on http://127.0.0.1:8000)
        3. Django Task Worker (processes background jobs from the database queue)
        4. Electron window (displays the Django web interface)

        **Background Jobs:**
        The built-in task worker will automatically process any background jobs
        you submit through django_tasks. Jobs are stored in the SQLite database
        and processed asynchronously by the worker.

        **Contents:**
        - {env["README_EXECUTABLE"]} (main application)
        - Django backend bundle with Python environment
        - Django Task Worker (django_tasks) for background job processing
        - SQLite database for data storage and job queue
        - All necessary libraries and dependencies
        """,
    )

    output_path = env["README_OUTPUT_PATH"]
    with open(output_path, "w", encoding="utf-8") as file_handle:
        file_handle.write(content)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
