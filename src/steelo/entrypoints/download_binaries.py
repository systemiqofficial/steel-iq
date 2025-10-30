"""Download Steel Model standalone binaries from S3."""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import httpx
from tqdm import tqdm


BASE_URL = "https://github-action-artifacts-steel-model.s3.eu-north-1.amazonaws.com"


def list_s3_bucket(prefix: str = "builds/") -> list[str]:
    """List objects in the S3 bucket with given prefix.

    Args:
        prefix: Prefix to filter objects (e.g., "builds/")

    Returns:
        List of object keys
    """
    try:
        # S3 REST API endpoint for listing objects
        response = httpx.get(
            BASE_URL,
            params={
                "prefix": prefix,
                "delimiter": "/",
                "max-keys": "1000",
            },
        )
        response.raise_for_status()

        # Parse XML response
        root = ET.fromstring(response.text)

        # Extract namespaces
        ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

        objects = []

        # Get objects (files)
        for contents in root.findall("s3:Contents", ns):
            key = contents.find("s3:Key", ns)
            if key is not None and key.text is not None:
                objects.append(key.text)

        # Get common prefixes (directories)
        for prefix_elem in root.findall("s3:CommonPrefixes", ns):
            prefix_text = prefix_elem.find("s3:Prefix", ns)
            if prefix_text is not None and prefix_text.text is not None:
                objects.append(prefix_text.text)

        return sorted(objects)

    except httpx.HTTPError as e:
        print(f"Error accessing S3 bucket: {e}")
        return []


def parse_build_id(build_id: str) -> tuple[str, str]:
    """Parse build ID to extract commit hash and timestamp.

    Returns:
        Tuple of (commit_hash, timestamp) or (build_id, "") if parsing fails
    """
    parts = build_id.split("-")
    if len(parts) >= 3:
        # Format: YYYYMMDD-HHMMSS-commithash
        commit_hash = parts[-1]  # Last part is commit hash
        timestamp = f"{parts[0]}-{parts[1]}"  # YYYYMMDD-HHMMSS
        return commit_hash, timestamp
    return build_id, ""


def find_build_id_by_commit(commit_id: str) -> Optional[str]:
    """Find the build ID for a given commit ID.

    Args:
        commit_id: The commit hash (can be short or full)

    Returns:
        The full build ID if found, None otherwise
    """
    objects = list_s3_bucket("builds/")

    # Extract unique build IDs
    builds = set()
    for obj in objects:
        parts = obj.split("/")
        if len(parts) >= 2 and parts[0] == "builds" and parts[1]:
            builds.add(parts[1])

    # Find builds that match the commit ID
    matching_builds = []
    for build_id in builds:
        commit_hash, timestamp = parse_build_id(build_id)
        # Support both short and full commit hashes
        if commit_hash.startswith(commit_id) or commit_id.startswith(commit_hash):
            matching_builds.append((build_id, timestamp))

    if not matching_builds:
        return None

    if len(matching_builds) == 1:
        return matching_builds[0][0]

    # If multiple matches, return the most recent one
    matching_builds.sort(key=lambda x: x[1], reverse=True)
    return matching_builds[0][0]


def resolve_identifier(identifier: str) -> str:
    """Resolve identifier to build ID.

    Args:
        identifier: Either a commit ID or build ID

    Returns:
        The resolved build ID

    Raises:
        ValueError: If identifier cannot be resolved
    """
    # Check if it's already a build ID (contains timestamp pattern)
    if "-" in identifier and len(identifier.split("-")) >= 3:
        parts = identifier.split("-")
        # Basic validation: first part should be date-like (8 digits)
        if len(parts[0]) == 8 and parts[0].isdigit():
            return identifier

    # Try to resolve as commit ID
    build_id = find_build_id_by_commit(identifier)
    if build_id:
        return build_id

    raise ValueError(f"Could not resolve '{identifier}' to a build ID. Use 'list-binaries' to see available builds.")


def download_file(url: str, output_path: Path, description: str) -> None:
    """Download a file with progress bar."""
    try:
        with httpx.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()

            total_size = int(response.headers.get("content-length", 0))

            with open(output_path, "wb") as f:
                with tqdm(
                    total=total_size,
                    unit="iB",
                    unit_scale=True,
                    desc=description,
                ) as pbar:
                    for chunk in response.iter_bytes(chunk_size=8192):
                        f.write(chunk)
                        pbar.update(len(chunk))

        print(f"[OK] Downloaded: {output_path}")

    except httpx.HTTPError as e:
        print(f"[FAILED] Failed to download {url}: {e}")
        raise


def download_binaries(
    identifier: str,
    output_dir: Path = Path("./dist/github-action-builds"),
    platforms: Optional[list[str]] = None,
) -> None:
    """Download standalone binaries for specified platforms.

    Args:
        identifier: The build identifier or commit ID (e.g., "a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029" or "4e979b2")
        output_dir: Directory to save the downloaded files
        platforms: List of platforms to download. If None, downloads all available.
                  Options: "macos", "windows"
    """
    # Resolve identifier to build ID
    try:
        build_id = resolve_identifier(identifier)
    except ValueError as e:
        print(f"Error: {e}")
        return

    if build_id != identifier:
        print(f"Resolved '{identifier}' to build ID: {build_id}")
    if platforms is None:
        platforms = ["macos", "windows"]

    # Create output directory
    build_dir = output_dir / "builds" / build_id
    build_dir.mkdir(parents=True, exist_ok=True)

    # List available files for this build
    print(f"Checking available files for build {build_id}...")
    available_files = []

    # Check which files exist
    test_files = [
        (f"STEEL-IQ-macos-{build_id}.tar.gz", "macos", "macOS binary"),
        (f"STEEL-IQ-windows-{build_id}.zip", "windows", "Windows binary"),
        ("README-macos.txt", "macos", "macOS README"),
        ("README-windows.txt", "windows", "Windows README"),
    ]

    for filename, platform, description in test_files:
        url = f"{BASE_URL}/builds/{build_id}/{filename}"
        try:
            response = httpx.head(url)
            if response.status_code == 200:
                available_files.append((filename, platform, description))
        except:  # noqa
            pass

    if not available_files:
        print(f"No files found for build {build_id}")
        return

    # Download files for each requested platform
    downloaded = False
    for platform in platforms:
        platform_files = [(f, d) for f, p, d in available_files if p == platform]

        if not platform_files:
            print(f"\nNo {platform} files found in this build")
            continue

        print(f"\nDownloading {platform} files...")

        platform_dir = build_dir / platform
        platform_dir.mkdir(exist_ok=True)

        for filename, description in platform_files:
            url = f"{BASE_URL}/builds/{build_id}/{filename}"
            output_path = platform_dir / filename

            # Skip if file already exists
            if output_path.exists():
                print(f"[OK] Already exists: {output_path}")
                continue

            try:
                download_file(url, output_path, description)
                downloaded = True
            except Exception as e:
                print(f"Error downloading {filename}: {e}")
                continue

    if downloaded:
        print(f"\n[OK] Downloads completed. Files saved to: {build_dir}")
    else:
        print("\n[FAILED] No new files downloaded")


def main():
    """Main entry point for the download_binaries command."""
    parser = argparse.ArgumentParser(
        description="Download Steel Model standalone binaries from S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download using commit ID (as shown in list-binaries)
  download-binaries 4e979b2
  
  # Download using full build ID
  download-binaries a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029
  
  # Download only macOS binaries using commit ID
  download-binaries 4e979b2 --platforms macos
  
  # Download only Windows binaries using commit ID
  download-binaries 4e979b2 --platforms windows
  
  # Download to a custom directory
  download-binaries 4e979b2 --output-dir ./my-downloads
""",
    )

    parser.add_argument(
        "identifier",
        help="Build identifier or commit ID (e.g., '4e979b2' or 'a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029')",
    )

    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./dist/github-action-builds"),
        help="Directory to save downloaded files (default: ./dist/github-action-builds)",
    )

    parser.add_argument(
        "--platforms",
        nargs="+",
        choices=["macos", "windows"],
        help="Platforms to download (default: all platforms)",
    )

    args = parser.parse_args()

    try:
        download_binaries(
            identifier=args.identifier,
            output_dir=args.output_dir,
            platforms=args.platforms,
        )
    except KeyboardInterrupt:
        print("\n\nDownload cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
