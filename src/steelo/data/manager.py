"""Data management for Steel Model.

This module handles downloading, caching, and validation of data files.
"""

import hashlib
import json
import logging
import shutil
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from tqdm import tqdm

from .exceptions import DataDownloadError, DataIntegrityError
from .manifest import DataManifest, DataPackage

logger = logging.getLogger(__name__)


class DataManager:
    """Manages data downloads and caching for the Steel Model."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        manifest_path: Path | None = None,
        offline_mode: bool = False,
    ):
        """Initialize data manager.

        Args:
            cache_dir: Directory for caching downloaded data
            manifest_path: Path to data manifest file
            offline_mode: If True, only use cached data
        """
        self.cache_dir = cache_dir or Path.home() / ".steelo" / "data_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        default_manifest = Path(__file__).parent / "manifest.json"
        self.manifest_path = manifest_path or default_manifest
        self.manifest = DataManifest.load(self.manifest_path)
        self.offline_mode = offline_mode

    def download_required_data(self, force: bool = False) -> None:
        """Download all required data packages.

        Args:
            force: Force re-download even if data exists
        """
        required_packages = self.manifest.get_required_packages()

        for package in required_packages:
            if self._is_package_cached(package) and not force:
                logger.info(f"Package {package.name} v{package.version} already cached")
                continue

            if self.offline_mode:
                raise DataDownloadError(f"Package {package.name} not cached and offline mode is enabled")

            self._download_package(package)

    def download_package(self, package_name: str, version: str | None = None, force: bool = False) -> None:
        """Download a specific data package.

        Args:
            package_name: Name of the package to download
            version: Specific version to download (optional)
            force: Force re-download even if data exists
        """
        package = self.manifest.get_package(package_name, version)
        if not package:
            if version:
                raise ValueError(f"Unknown package: {package_name} v{version}")
            else:
                raise ValueError(f"Unknown package: {package_name}")

        if self._is_package_cached(package) and not force:
            logger.info(f"Package {package.name} v{package.version} already cached")
            return

        if self.offline_mode:
            raise DataDownloadError(f"Package {package.name} not cached and offline mode is enabled")

        self._download_package(package)

    def get_package_path(self, package_name: str, version: str | None = None) -> Path:
        """Get the path to a downloaded package.

        Args:
            package_name: Name of the package
            version: Specific version (optional)

        Returns:
            Path to the package directory

        Raises:
            ValueError: If package not found or not downloaded
        """
        package = self.manifest.get_package(package_name, version)
        if not package:
            if version:
                raise ValueError(f"Unknown package: {package_name} v{version}")
            else:
                raise ValueError(f"Unknown package: {package_name}")

        package_dir = self._get_package_dir(package)
        if not package_dir.exists():
            raise ValueError(f"Package {package_name} v{package.version} not downloaded")

        return package_dir

    def verify_data_integrity(self) -> dict[str, bool]:
        """Verify integrity of all cached packages.

        Returns:
            Dictionary mapping package names to verification status
        """
        results = {}
        for package in self.manifest.packages:
            if self._is_package_cached(package):
                results[package.name] = self._verify_package_integrity(package)
            else:
                results[package.name] = False
        return results

    def clear_cache(self, package_name: str | None = None) -> None:
        """Clear cached data.

        Args:
            package_name: If provided, only clear this package
        """
        if package_name:
            package = self.manifest.get_package(package_name)
            if package:
                package_dir = self._get_package_dir(package)
                if package_dir.exists():
                    shutil.rmtree(package_dir)
                    logger.info(f"Cleared cache for {package_name}")
        else:
            shutil.rmtree(self.cache_dir)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Cleared all cached data")

    def list_packages(self) -> list[dict[str, Any]]:
        """List all available packages with their status.

        Returns:
            List of package information dictionaries
        """
        packages = []
        for package in self.manifest.packages:
            info = package.to_dict()
            info["cached"] = self._is_package_cached(package)
            info["cache_path"] = str(self._get_package_dir(package))
            packages.append(info)
        return packages

    def _get_package_dir(self, package: DataPackage) -> Path:
        """Get directory for a package."""
        return self.cache_dir / f"{package.name}-v{package.version}"

    def _is_package_cached(self, package: DataPackage) -> bool:
        """Check if a package is cached."""
        package_dir = self._get_package_dir(package)
        metadata_file = package_dir / ".metadata.json"
        return package_dir.exists() and metadata_file.exists()

    def _download_package(self, package: DataPackage) -> None:
        """Download and extract a package."""
        logger.info(f"Downloading {package.name} v{package.version} ({package.size_mb} MB)")
        logger.info(f"  URL: {package.url}")
        logger.info(f"  Expected checksum: {package.checksum}")

        package_dir = self._get_package_dir(package)
        package_dir.mkdir(parents=True, exist_ok=True)

        # Download to temporary file
        temp_file = package_dir / f"{package.name}.tmp"

        try:
            # Download with progress bar - 10 minute timeout for large files
            timeout = httpx.Timeout(600.0, connect=10.0, read=60.0, write=60.0, pool=5.0)
            with httpx.stream("GET", package.url, follow_redirects=True, timeout=timeout) as response:
                response.raise_for_status()

                total_size = int(response.headers.get("content-length", 0))
                # Use larger block size for large files (>10MB)
                block_size = 65536 if total_size > 10 * 1024 * 1024 else 8192
                logger.info(f"  Content-Length: {total_size} bytes")
                logger.info(f"  Using block size: {block_size} bytes")

                bytes_downloaded = 0
                with open(temp_file, "wb") as f:
                    with tqdm(total=total_size, unit="iB", unit_scale=True) as pbar:
                        for chunk in response.iter_bytes(block_size):
                            f.write(chunk)
                            bytes_downloaded += len(chunk)
                            pbar.update(len(chunk))
                    f.flush()
                    import os

                    os.fsync(f.fileno())  # Force write to disk

                logger.info(f"  Downloaded: {bytes_downloaded} bytes")
                logger.info(f"  File size on disk: {temp_file.stat().st_size} bytes")

                # Verify the download is complete
                if total_size > 0 and bytes_downloaded != total_size:
                    raise DataDownloadError(
                        f"Incomplete download: expected {total_size} bytes, got {bytes_downloaded} bytes"
                    )

            # Verify checksum
            if not self._verify_checksum(temp_file, package.checksum):
                raise DataIntegrityError(f"Checksum verification failed for {package.name}")

            # Extract if it's a zip file
            url_path = urlparse(package.url).path
            if url_path.endswith(".zip"):
                logger.info(f"Extracting {package.name}...")
                with zipfile.ZipFile(temp_file, "r") as zip_ref:
                    zip_ref.extractall(package_dir)
                temp_file.unlink()
            else:
                # For single-file packages, use the name from the files list if available
                if len(package.files) == 1:
                    # Use the filename specified in the manifest
                    final_file = package_dir / package.files[0]
                else:
                    # Fall back to the URL filename for backward compatibility
                    final_file = package_dir / Path(urlparse(package.url).path).name
                # On Windows, rename fails if target exists, so remove it first
                if final_file.exists():
                    final_file.unlink()
                temp_file.rename(final_file)

            # Save metadata
            import time

            metadata = {
                "package": package.to_dict(),
                "download_timestamp": time.time(),
            }
            with open(package_dir / ".metadata.json", "w") as f:
                json.dump(metadata, f, indent=2)

            logger.info(f"Successfully downloaded {package.name}")

        except Exception as e:
            # Clean up on failure
            if temp_file.exists():
                temp_file.unlink()
            if package_dir.exists():
                shutil.rmtree(package_dir)
            raise DataDownloadError(f"Failed to download {package.name}: {e}")

    def _verify_package_integrity(self, package: DataPackage) -> bool:
        """Verify integrity of a cached package."""
        package_dir = self._get_package_dir(package)

        # Check metadata
        metadata_file = package_dir / ".metadata.json"
        if not metadata_file.exists():
            return False

        try:
            with open(metadata_file) as f:
                metadata = json.load(f)

            # Verify package info matches
            stored_package = DataPackage.from_dict(metadata["package"])
            if stored_package.checksum != package.checksum:
                return False

            # Verify files exist
            for file_name in package.files:
                if not (package_dir / file_name).exists():
                    return False

            return True

        except Exception:
            return False

    def _verify_checksum(self, file_path: Path, expected_checksum: str) -> bool:
        """Verify file checksum."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        actual_checksum = sha256_hash.hexdigest()
        if actual_checksum != expected_checksum:
            logger.error(f"Checksum mismatch for {file_path.name}:")
            logger.error(f"  Expected: {expected_checksum}")
            logger.error(f"  Actual:   {actual_checksum}")
        return actual_checksum == expected_checksum
