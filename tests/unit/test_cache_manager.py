from datetime import datetime

from steelo.data.cache_manager import DataPreparationCache, get_preparation_hash


def test_hash_generation_consistent(tmp_path):
    """Test that hash generation is consistent."""
    # Create a sample file
    sample_file = tmp_path / "sample.xlsx"
    sample_file.write_bytes(b"PK\x03\x04" + b"X" * 1000)  # Fake Excel header + content

    hash1 = get_preparation_hash(sample_file)
    hash2 = get_preparation_hash(sample_file)

    assert hash1 == hash2
    assert len(hash1) == 16  # 16 character hash
    assert all(c in "0123456789abcdef" for c in hash1)


def test_hash_changes_with_content(tmp_path):
    """Test that hash changes when file content changes."""
    sample_file = tmp_path / "sample.xlsx"
    sample_file.write_bytes(b"PK\x03\x04" + b"X" * 1000)

    hash1 = get_preparation_hash(sample_file)

    # Modify file
    with open(sample_file, "ab") as f:
        f.write(b"MORE_CONTENT")

    hash2 = get_preparation_hash(sample_file)
    assert hash1 != hash2


def test_hash_changes_with_version(tmp_path):
    """Test that hash changes with different versions."""
    sample_file = tmp_path / "sample.xlsx"
    sample_file.write_bytes(b"PK\x03\x04" + b"X" * 1000)

    hash1 = get_preparation_hash(sample_file, version="1.0")
    hash2 = get_preparation_hash(sample_file, version="1.1")

    assert hash1 != hash2


def test_cache_not_found_initially(tmp_path):
    """Test that cache returns None for uncached file."""
    cache_manager = DataPreparationCache(cache_root=tmp_path / "cache")
    sample_file = tmp_path / "sample.xlsx"
    sample_file.write_bytes(b"PK\x03\x04" + b"X" * 1000)

    cached = cache_manager.get_cached_preparation(sample_file)
    assert cached is None


def test_save_and_retrieve_cache(tmp_path):
    """Test saving and retrieving cached preparation."""
    cache_manager = DataPreparationCache(cache_root=tmp_path / "cache")
    sample_file = tmp_path / "sample.xlsx"
    sample_file.write_bytes(b"PK\x03\x04" + b"X" * 1000)

    # Create source data
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "test.txt").write_text("test content")
    (source_dir / "subdir").mkdir()
    (source_dir / "subdir" / "nested.txt").write_text("nested content")

    # Save to cache
    cache_dir = cache_manager.save_preparation(
        source_dir=source_dir, master_excel_path=sample_file, preparation_time=10.5
    )

    # Verify cache structure
    assert cache_dir.exists()
    assert (cache_dir / "data").exists()
    assert (cache_dir / "metadata.json").exists()
    assert (cache_dir / "data" / "test.txt").read_text() == "test content"
    assert (cache_dir / "data" / "subdir" / "nested.txt").read_text() == "nested content"

    # Retrieve from cache
    cached = cache_manager.get_cached_preparation(sample_file)
    assert cached is not None
    assert cached == cache_dir / "data"


def test_cache_metadata(tmp_path):
    """Test that metadata is correctly saved."""
    cache_manager = DataPreparationCache(cache_root=tmp_path / "cache")
    sample_file = tmp_path / "sample.xlsx"
    sample_file.write_bytes(b"PK\x03\x04" + b"X" * 1000)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "file1.txt").write_text("content1")
    (source_dir / "file2.txt").write_text("content2")

    before_save = datetime.now()
    cache_dir = cache_manager.save_preparation(
        source_dir=source_dir, master_excel_path=sample_file, preparation_time=5.25
    )
    after_save = datetime.now()

    # Load and verify metadata
    import json

    metadata = json.loads((cache_dir / "metadata.json").read_text())

    assert metadata["master_excel_path"] == str(sample_file)
    assert len(metadata["master_excel_hash"]) == 16
    assert metadata["cache_version"] == DataPreparationCache.CACHE_VERSION
    assert metadata["preparation_time_seconds"] == 5.25
    assert metadata["file_count"] == 2
    assert metadata["total_size_bytes"] > 0

    # Check timestamp is reasonable
    created = datetime.fromisoformat(metadata["created_at"])
    assert before_save <= created <= after_save


def test_cache_stats_empty(tmp_path):
    """Test cache stats on empty cache."""
    cache_manager = DataPreparationCache(cache_root=tmp_path / "cache")
    stats = cache_manager.get_cache_stats()

    assert stats["total_preparations"] == 0
    assert stats["total_size_bytes"] == 0
    assert stats["oldest_cache"] is None
    assert stats["newest_cache"] is None


def test_cache_stats_with_data(tmp_path):
    """Test cache stats with cached data."""
    import json

    cache_manager = DataPreparationCache(cache_root=tmp_path / "cache")
    sample_file = tmp_path / "sample.xlsx"

    # Create multiple cached preparations with different timestamps
    for i in range(3):
        # Create unique Excel file for each preparation
        sample_file.write_bytes(b"PK\x03\x04" + f"content_{i}".encode())

        source_dir = tmp_path / f"source_{i}"
        source_dir.mkdir()
        (source_dir / f"file_{i}.txt").write_text(f"content {i}" * 100)

        cache_dir = cache_manager.save_preparation(
            source_dir=source_dir, master_excel_path=sample_file, preparation_time=i + 1.0
        )

        # Manually update the metadata timestamp to ensure proper ordering
        # No need for sleep - just set different timestamps
        metadata_path = cache_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        # Set timestamps with hour intervals to ensure clear ordering
        fake_time = datetime(2024, 1, 1, 10 + i, 0, 0)
        metadata["created_at"] = fake_time.isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2))

    stats = cache_manager.get_cache_stats()

    assert stats["total_preparations"] == 3
    assert stats["total_size_bytes"] > 0
    assert stats["total_size_mb"] > 0
    assert stats["oldest_cache"] is not None
    assert stats["newest_cache"] is not None
    assert stats["oldest_cache"] < stats["newest_cache"]


def test_clear_cache_all(tmp_path):
    """Test clearing entire cache."""
    cache_manager = DataPreparationCache(cache_root=tmp_path / "cache")
    sample_file = tmp_path / "sample.xlsx"
    sample_file.write_bytes(b"PK\x03\x04" + b"X" * 1000)

    # Create cached data
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "test.txt").write_text("test")

    cache_manager.save_preparation(source_dir, sample_file, 1.0)

    # Verify cache exists
    assert cache_manager.get_cached_preparation(sample_file) is not None

    # Clear cache
    removed = cache_manager.clear_cache()
    assert removed == 1

    # Verify cache is gone
    assert cache_manager.get_cached_preparation(sample_file) is None


def test_clear_cache_keep_recent(tmp_path, monkeypatch):
    """Test clearing cache while keeping recent entries."""
    import json

    cache_manager = DataPreparationCache(cache_root=tmp_path / "cache")
    excel_files = []

    # Create 5 cached preparations with mocked timestamps
    for i in range(5):
        # Create unique Excel file
        excel_file = tmp_path / f"excel_{i}.xlsx"
        excel_file.write_bytes(b"PK\x03\x04" + f"content_{i}".encode())
        excel_files.append(excel_file)

        source_dir = tmp_path / f"source_{i}"
        source_dir.mkdir()
        (source_dir / "file.txt").write_text(f"content {i}")

        # Save preparation
        cache_dir = cache_manager.save_preparation(source_dir, excel_file, 1.0)

        # Manually update the metadata timestamp to ensure proper ordering
        metadata_path = cache_dir / "metadata.json"
        metadata = json.loads(metadata_path.read_text())
        # Set timestamps with i-minute intervals to ensure clear ordering
        fake_time = datetime(2024, 1, 1, 12, i, 0)
        metadata["created_at"] = fake_time.isoformat()
        metadata_path.write_text(json.dumps(metadata, indent=2))

    # Clear keeping 2 most recent
    removed = cache_manager.clear_cache(keep_recent=2)
    assert removed == 3

    # Verify oldest 3 are gone
    for i in range(3):
        assert cache_manager.get_cached_preparation(excel_files[i]) is None

    # Verify newest 2 remain
    for i in range(3, 5):
        assert cache_manager.get_cached_preparation(excel_files[i]) is not None


def test_cache_version_invalidation(tmp_path):
    """Test that changing cache version invalidates old caches."""
    cache_manager = DataPreparationCache(cache_root=tmp_path / "cache")
    sample_file = tmp_path / "sample.xlsx"
    sample_file.write_bytes(b"PK\x03\x04" + b"X" * 1000)

    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "test.txt").write_text("test")

    # Save with version 1.0
    cache_manager.CACHE_VERSION = "1.0"
    cache_manager.save_preparation(source_dir, sample_file, 1.0)

    # Should find cache with same version
    assert cache_manager.get_cached_preparation(sample_file) is not None

    # Change version
    cache_manager.CACHE_VERSION = "1.1"

    # Should not find cache with different version
    assert cache_manager.get_cached_preparation(sample_file) is None
