"""Download latest Steel Model binaries from S3."""

import argparse
import sys
from pathlib import Path
from typing import Optional

from steelo.entrypoints.download_binaries import download_binaries
from steelo.entrypoints.list_binaries import list_builds


def find_latest_build_with_platform(platform: str) -> Optional[str]:
    """Find the latest build that has binaries for the specified platform.

    Args:
        platform: Platform to search for ('windows' or 'macos')

    Returns:
        Build ID of the latest build with the platform, or None if not found
    """
    builds = list_builds()

    if not builds:
        return None

    # Check each build (already sorted by newest first) for the platform
    for build_id, _, _ in builds:
        # Check if this build has files for the requested platform
        from steelo.entrypoints.list_binaries import get_build_platforms

        available_platforms = get_build_platforms(build_id)

        if platform.lower() == "windows":
            if any("Windows" in p for p in available_platforms):
                return build_id
        elif platform.lower() == "macos":
            if any("macOS" in p for p in available_platforms):
                return build_id

    return None


def download_latest_platform(
    platform: str,
    output_dir: Path = Path("./dist/github-action-builds"),
) -> None:
    """Download the latest binary for the specified platform.

    Args:
        platform: Platform to download ('windows' or 'macos')
        output_dir: Directory to save the downloaded files
    """
    print(f"Searching for latest {platform} build...")

    build_id = find_latest_build_with_platform(platform)

    if not build_id:
        print(f"No {platform} builds found")
        sys.exit(1)

    print(f"Found latest {platform} build: {build_id}")

    # Download the specific platform
    download_binaries(
        identifier=build_id,
        output_dir=output_dir,
        platforms=[platform.lower()],
    )


def main():
    """Main entry point for the download-latest command."""
    parser = argparse.ArgumentParser(
        description="Download latest Steel Model binaries from S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download latest Windows binary
  download-latest --platform windows
  
  # Download latest macOS binary
  download-latest --platform macos
  
  # Download to custom directory
  download-latest --platform windows --output-dir ./my-downloads
""",
    )

    parser.add_argument(
        "--platform",
        choices=["windows", "macos"],
        required=True,
        help="Platform to download (windows or macos)",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./dist/github-action-builds"),
        help="Directory to save downloaded files (default: ./dist/github-action-builds)",
    )

    args = parser.parse_args()

    try:
        download_latest_platform(
            platform=args.platform,
            output_dir=args.output_dir,
        )
    except KeyboardInterrupt:
        print("\n\nDownload cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
