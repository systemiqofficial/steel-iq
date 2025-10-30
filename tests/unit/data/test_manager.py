"""Tests for data manager module."""

import json
from unittest.mock import Mock, patch

import pytest

from steelo.data.exceptions import DataDownloadError
from steelo.data.manager import DataManager
from steelo.data.manifest import DataManifest, DataPackage


@pytest.fixture
def test_manifest(tmp_path):
    """Create a test manifest."""
    manifest = DataManifest()
    manifest.add_package(
        DataPackage(
            name="test-data",
            version="1.0.0",
            url="https://example.com/test-data.zip",
            size_mb=1.0,
            checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",  # SHA256 of empty file
            description="Test data",
            required=True,
            files=["test.json"],
        )
    )
    manifest_file = tmp_path / "test_manifest.json"
    manifest.save(manifest_file)
    return manifest_file


@pytest.fixture
def data_manager(tmp_path, test_manifest):
    """Create a data manager instance."""
    cache_dir = tmp_path / "cache"
    return DataManager(cache_dir=cache_dir, manifest_path=test_manifest)


def test_data_manager_init(data_manager):
    """Test data manager initialization."""
    assert data_manager.cache_dir.exists()
    assert len(data_manager.manifest.packages) == 1
    assert data_manager.offline_mode is False


def test_list_packages(data_manager):
    """Test listing packages."""
    packages = data_manager.list_packages()
    assert len(packages) == 1
    assert packages[0]["name"] == "test-data"
    assert packages[0]["cached"] is False


def test_get_package_path_not_downloaded(data_manager):
    """Test getting path for non-downloaded package."""
    with pytest.raises(ValueError, match="not downloaded"):
        data_manager.get_package_path("test-data")


def test_get_package_path_unknown_package(data_manager):
    """Test getting path for unknown package."""
    with pytest.raises(ValueError, match="Unknown package"):
        data_manager.get_package_path("unknown-package")


@patch("httpx.stream")
def test_download_package_success(mock_stream, data_manager, tmp_path):
    """Test successful package download."""
    import zipfile
    import io

    # Create a valid zip file in memory
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("test.json", '{"test": "data"}')
    zip_content = zip_buffer.getvalue()

    # Create a mock response that returns the zip content
    mock_response = Mock()
    mock_response.headers = {"content-length": str(len(zip_content))}
    mock_response.iter_bytes = Mock(return_value=[zip_content])
    mock_response.raise_for_status = Mock()
    mock_response.__enter__ = Mock(return_value=mock_response)
    mock_response.__exit__ = Mock(return_value=None)

    mock_stream.return_value = mock_response

    # Create a test zip file
    package_dir = data_manager._get_package_dir(data_manager.manifest.packages[0])
    package_dir.mkdir(parents=True, exist_ok=True)
    _temp_file = package_dir / "test-data.tmp"

    # Mock the checksum verification to pass
    with patch.object(data_manager, "_verify_checksum", return_value=True):
        data_manager.download_package("test-data")

    # Check that package is marked as cached
    assert data_manager._is_package_cached(data_manager.manifest.packages[0])


def test_download_package_offline_mode(data_manager):
    """Test download in offline mode."""
    data_manager.offline_mode = True
    with pytest.raises(DataDownloadError, match="offline mode"):
        data_manager.download_package("test-data")


def test_clear_cache_all(data_manager):
    """Test clearing all cache."""
    # Create some cached data
    package_dir = data_manager.cache_dir / "test-package-v1.0.0"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "test.txt").write_text("test")

    data_manager.clear_cache()

    assert data_manager.cache_dir.exists()
    assert not package_dir.exists()


def test_clear_cache_specific_package(data_manager):
    """Test clearing specific package cache."""
    # Create cached data for the test package
    package = data_manager.manifest.packages[0]
    package_dir = data_manager._get_package_dir(package)
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "test.txt").write_text("test")

    data_manager.clear_cache("test-data")

    assert not package_dir.exists()


def test_verify_data_integrity_empty(data_manager):
    """Test verifying integrity with no cached packages."""
    results = data_manager.verify_data_integrity()
    assert results["test-data"] is False


def test_verify_data_integrity_with_cached(data_manager):
    """Test verifying integrity with cached package."""
    # Create a properly cached package
    package = data_manager.manifest.packages[0]
    package_dir = data_manager._get_package_dir(package)
    package_dir.mkdir(parents=True, exist_ok=True)

    # Create metadata file
    metadata = {
        "package": package.to_dict(),
        "download_timestamp": 123456789,
    }
    with open(package_dir / ".metadata.json", "w") as f:
        json.dump(metadata, f)

    # Create expected files
    for file_name in package.files:
        (package_dir / file_name).write_text("{}")

    results = data_manager.verify_data_integrity()
    assert results["test-data"] is True
