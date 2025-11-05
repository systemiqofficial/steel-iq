"""List available Steel Model binaries on S3."""

import argparse
import sys
import xml.etree.ElementTree as ET

import httpx


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


def get_build_platforms(build_id: str) -> list[str]:
    """Get available platforms for a specific build.

    Args:
        build_id: The build identifier

    Returns:
        List of platform names (e.g., ['Windows', 'macOS'])
    """
    prefix = f"builds/{build_id}/"
    objects = list_s3_bucket(prefix)

    platforms = set()
    for obj in objects:
        if obj.startswith(prefix) and not obj.endswith("/"):
            filename = obj[len(prefix) :]
            platform = get_platform_description(filename)
            if platform not in ["README", "Unknown"]:
                platforms.add(platform)

    return sorted(platforms)


def get_download_url(build_id: str, filename: str) -> str:
    """Get HTTP download URL for a specific file.

    Args:
        build_id: The build identifier
        filename: The filename within the build

    Returns:
        HTTP download URL
    """
    return f"{BASE_URL}/builds/{build_id}/{filename}"


def list_builds() -> list[tuple[str, str, str]]:
    """List all available builds.

    Returns:
        List of (build_id, commit_hash, timestamp) tuples
    """
    objects = list_s3_bucket("builds/")

    # Extract unique build IDs
    builds = set()
    for obj in objects:
        parts = obj.split("/")
        if len(parts) >= 2 and parts[0] == "builds" and parts[1]:
            builds.add(parts[1])

    # Parse and sort builds by timestamp (newest first)
    build_info = []
    for build_id in builds:
        commit_hash, timestamp = parse_build_id(build_id)
        build_info.append((build_id, commit_hash, timestamp))

    # Sort by timestamp descending
    build_info.sort(key=lambda x: x[2], reverse=True)

    return build_info


def get_platform_description(filename: str) -> str:
    """Get a human-readable platform description from filename."""
    filename_lower = filename.lower()

    if filename_lower.endswith(".txt"):
        if "readme-macos" in filename_lower:
            return "macOS README"
        if "readme-windows" in filename_lower:
            return "Windows README"
        if "readme-linux" in filename_lower:
            return "Linux README"
        return "README"

    if "win" in filename_lower:
        return "Windows"

    if "mac" in filename_lower or "macos" in filename_lower:
        if "arm64" in filename_lower:
            return "macOS (Apple Silicon)"
        if "x64" in filename_lower:
            return "macOS (Intel)"
        return "macOS"

    if "linux" in filename_lower or filename_lower.endswith(".appimage"):
        return "Linux"

    return "Unknown"


def list_build_files(build_id: str) -> list[tuple[str, int, str]]:
    """List files in a specific build.

    Args:
        build_id: The build identifier

    Returns:
        List of (filename, size, platform) tuples
    """
    prefix = f"builds/{build_id}/"
    objects = list_s3_bucket(prefix)

    files = []
    for obj in objects:
        if obj.startswith(prefix) and not obj.endswith("/"):
            filename = obj[len(prefix) :]
            platform = get_platform_description(filename)
            # Try to get file size
            try:
                response = httpx.head(f"{BASE_URL}/{obj}")
                size = int(response.headers.get("content-length", 0))
                files.append((filename, size, platform))
            except:  # noqa
                files.append((filename, 0, platform))

    return sorted(files, key=lambda x: (x[2], x[0]))  # Sort by platform, then filename


def format_size(size: int) -> str:
    """Format file size in human-readable format."""
    size_float = float(size)
    for unit in ["B", "KB", "MB", "GB"]:
        if size_float < 1024.0:
            return f"{size_float:.1f} {unit}"
        size_float /= 1024.0
    return f"{size_float:.1f} TB"


def main():
    """Main entry point for the list-binaries command."""
    parser = argparse.ArgumentParser(
        description="List available Steel Model binaries on S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # List all available builds
  list-binaries
  
  # List files in a specific build
  list-binaries --build a8dc48073c62ec8d8af38161c72c3ed7c60e407b-20250621-060029
  
  # List only recent builds
  list-binaries --recent 5
""",
    )

    parser.add_argument(
        "--build",
        help="List files in a specific build",
    )

    parser.add_argument(
        "--recent",
        type=int,
        help="Show only the N most recent builds",
    )

    args = parser.parse_args()

    try:
        if args.build:
            # List files in specific build
            print(f"\nFiles in build {args.build}:\n")
            files = list_build_files(args.build)

            if not files:
                print("No files found or unable to access build.")
                sys.exit(1)

            # Display files in a table
            max_name_len = max(len(f[0]) for f in files) if files else 0
            max_platform_len = max(len(f[2]) for f in files) if files else 0

            print(f"{'Platform':<{max_platform_len}}  {'File':<{max_name_len}}  {'Size':>10}")
            print("-" * (max_platform_len + max_name_len + 15))

            total_size = 0
            for filename, size, platform in files:
                print(f"{platform:<{max_platform_len}}  {filename:<{max_name_len}}  {format_size(size):>10}")
                total_size += size

            print("-" * (max_platform_len + max_name_len + 15))
            print(f"{'':<{max_platform_len}}  {'Total':<{max_name_len}}  {format_size(total_size):>10}")

        else:
            # List all builds
            print("\nAvailable builds:\n")
            builds = list_builds()

            if not builds:
                print("No builds found or unable to access S3 bucket.")
                print("\nNote: The S3 bucket might not allow listing.")
                print("If you know a specific build ID, try:")
                print("  list-binaries --build <build-id>")
                sys.exit(1)

            # Apply recent filter if specified
            if args.recent:
                builds = builds[: args.recent]  # Already sorted newest first

            # Display builds in a table with platforms and download info
            print(f"{'Commit':<10} {'Date/Time':<20} {'Platforms':<25} {'Download URLs'}")
            print("-" * 120)

            for build_id, commit_hash, timestamp in builds:
                # Format timestamp for display
                if timestamp:
                    date_part, time_part = timestamp.split("-")
                    formatted_time = f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]} {time_part[:2]}:{time_part[2:4]}:{time_part[4:]}"
                else:
                    formatted_time = "Unknown"

                # Get available platforms
                platforms = get_build_platforms(build_id)
                platforms_str = ", ".join(platforms) if platforms else "None"

                # Generate download URLs for main binaries
                download_urls = []
                if "Windows" in platforms:
                    windows_file = f"STEEL-IQ-windows-{build_id}.zip"
                    download_urls.append(f"Win: {get_download_url(build_id, windows_file)}")
                if "macOS" in platforms or "macOS (Apple Silicon)" in platforms or "macOS (Intel)" in platforms:
                    macos_file = f"STEEL-IQ-macos-{build_id}.tar.gz"
                    download_urls.append(f"Mac: {get_download_url(build_id, macos_file)}")
                if "Linux" in platforms:
                    linux_file = f"STEEL-IQ-linux-{build_id}.tar.gz"
                    download_urls.append(f"Linux: {get_download_url(build_id, linux_file)}")

                urls_str = " | ".join(download_urls) if download_urls else "None"

                print(f"{commit_hash:<10} {formatted_time:<20} {platforms_str:<25} {urls_str}")

            print(f"\nTotal: {len(builds)} build(s)")
            print("\nTo see files in a build, use:")
            print("  list-binaries --build <build-id>")

    except KeyboardInterrupt:
        print("\n\nListing cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\nError: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
