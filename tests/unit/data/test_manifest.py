"""Tests for data manifest module."""

from pathlib import Path

import pytest

from steelo.data.manifest import DataManifest, DataPackage


@pytest.fixture
def sample_package():
    """Create a sample data package."""
    return DataPackage(
        name="test-package",
        version="1.0.0",
        url="https://example.com/test.zip",
        size_mb=10.5,
        checksum="abc123",
        description="Test package",
        required=True,
        files=["file1.json", "file2.csv"],
    )


@pytest.fixture
def sample_manifest(sample_package):
    """Create a sample manifest."""
    manifest = DataManifest()
    manifest.add_package(sample_package)
    return manifest


def test_data_package_creation():
    """Test creating a data package."""
    package = DataPackage(
        name="test",
        version="1.0.0",
        url="https://example.com/test.zip",
        size_mb=5.0,
        checksum="xyz789",
        description="Test",
    )
    assert package.name == "test"
    assert package.version == "1.0.0"
    assert package.required is True  # default
    assert package.files == []  # default


def test_data_package_to_dict(sample_package):
    """Test converting package to dict."""
    data = sample_package.to_dict()
    assert data["name"] == "test-package"
    assert data["version"] == "1.0.0"
    assert data["files"] == ["file1.json", "file2.csv"]


def test_data_package_from_dict():
    """Test creating package from dict."""
    data = {
        "name": "test",
        "version": "2.0.0",
        "url": "https://example.com/test2.zip",
        "size_mb": 20.0,
        "checksum": "def456",
        "description": "Test 2",
        "required": False,
    }
    package = DataPackage.from_dict(data)
    assert package.name == "test"
    assert package.version == "2.0.0"
    assert package.required is False


def test_manifest_add_package():
    """Test adding packages to manifest."""
    manifest = DataManifest()
    package1 = DataPackage(
        name="pkg1",
        version="1.0.0",
        url="https://example.com/pkg1.zip",
        size_mb=1.0,
        checksum="aaa",
        description="Package 1",
    )
    package2 = DataPackage(
        name="pkg2",
        version="2.0.0",
        url="https://example.com/pkg2.zip",
        size_mb=2.0,
        checksum="bbb",
        description="Package 2",
    )

    manifest.add_package(package1)
    manifest.add_package(package2)

    assert len(manifest.packages) == 2
    assert manifest.packages[0].name == "pkg1"
    assert manifest.packages[1].name == "pkg2"


def test_manifest_get_package(sample_manifest):
    """Test getting package by name."""
    package = sample_manifest.get_package("test-package")
    assert package is not None
    assert package.name == "test-package"

    missing = sample_manifest.get_package("missing-package")
    assert missing is None


def test_manifest_get_required_packages():
    """Test getting required packages."""
    manifest = DataManifest()
    required_pkg = DataPackage(
        name="required",
        version="1.0.0",
        url="https://example.com/req.zip",
        size_mb=1.0,
        checksum="req123",
        description="Required package",
        required=True,
    )
    optional_pkg = DataPackage(
        name="optional",
        version="1.0.0",
        url="https://example.com/opt.zip",
        size_mb=1.0,
        checksum="opt123",
        description="Optional package",
        required=False,
    )

    manifest.add_package(required_pkg)
    manifest.add_package(optional_pkg)

    required = manifest.get_required_packages()
    assert len(required) == 1
    assert required[0].name == "required"


def test_manifest_save_and_load(tmp_path, sample_manifest):
    """Test saving and loading manifest."""
    manifest_file = tmp_path / "manifest.json"

    # Save
    sample_manifest.save(manifest_file)
    assert manifest_file.exists()

    # Load
    loaded = DataManifest.load(manifest_file)
    assert len(loaded.packages) == 1
    assert loaded.packages[0].name == "test-package"
    assert loaded.s3_bucket == "steelo-data"


def test_manifest_load_nonexistent():
    """Test loading from non-existent file."""
    manifest = DataManifest.load(Path("/nonexistent/manifest.json"))
    assert len(manifest.packages) == 0
    assert manifest.s3_bucket == "steelo-data"
