"""Unit tests for cache index functionality."""

import pytest
import json
from pathlib import Path
from datetime import datetime

from steelo.data.cache_manager import DataPreparationCache


class TestCacheIndex:
    """Test cache index functionality."""

    @pytest.fixture
    def cache_dir(self, tmp_path):
        """Create a temporary cache directory."""
        cache_root = tmp_path / "test_cache"
        cache_root.mkdir()
        return cache_root

    @pytest.fixture
    def cache_manager(self, cache_dir):
        """Create a cache manager with test directory."""
        return DataPreparationCache(cache_root=cache_dir)

    def test_index_created_on_init(self, cache_manager):
        """Test that index is created when cache manager is initialized."""
        assert cache_manager.index_path.exists()
        index_data = json.loads(cache_manager.index_path.read_text())
        assert index_data["version"] == cache_manager.CACHE_VERSION
        assert "created_at" in index_data
        assert "entries" in index_data
        assert isinstance(index_data["entries"], dict)

    def test_index_rebuilt_on_missing_file(self, cache_dir):
        """Test that index is rebuilt if file is missing."""
        # Create a cache entry manually
        prep_dir = cache_dir / "prep_abc123"
        prep_dir.mkdir()
        data_dir = prep_dir / "data"
        data_dir.mkdir()

        metadata = {
            "master_excel_path": "/path/to/excel.xlsx",
            "created_at": datetime.now().isoformat(),
            "preparation_time_seconds": 10.5,
            "file_count": 5,
            "total_size_bytes": 1024,
        }
        (prep_dir / "metadata.json").write_text(json.dumps(metadata))

        # Initialize cache manager - should rebuild index
        cache_manager = DataPreparationCache(cache_root=cache_dir)

        # Check index contains the entry
        assert "abc123" in cache_manager.index["entries"]
        entry = cache_manager.index["entries"]["abc123"]
        assert entry["master_excel_path"] == "/path/to/excel.xlsx"
        assert entry["file_count"] == 5

    def test_index_rebuilt_on_version_mismatch(self, cache_dir):
        """Test that index is rebuilt when version doesn't match."""
        # Create an old index with different version
        old_index = {
            "version": "0.9",
            "created_at": datetime.now().isoformat(),
            "entries": {"old_entry": {"master_excel_path": "/old/path.xlsx", "created_at": datetime.now().isoformat()}},
        }
        index_path = cache_dir / "index.json"
        index_path.write_text(json.dumps(old_index))

        # Initialize cache manager - should rebuild index
        cache_manager = DataPreparationCache(cache_root=cache_dir)

        # Check index was rebuilt with new version
        assert cache_manager.index["version"] == cache_manager.CACHE_VERSION
        assert "old_entry" not in cache_manager.index["entries"]

    def test_get_cached_preparation_uses_index(self, cache_manager, tmp_path):
        """Test that get_cached_preparation uses index for fast lookup."""
        # Create a mock Excel file
        excel_path = tmp_path / "test.xlsx"
        excel_path.write_bytes(b"mock excel content")

        # Get cache key
        cache_key = cache_manager.get_cache_key(excel_path)

        # Add entry to index manually
        cache_manager.index["entries"][cache_key] = {
            "master_excel_path": str(excel_path),
            "created_at": datetime.now().isoformat(),
            "preparation_time_seconds": 5.0,
            "file_count": 3,
            "total_size_bytes": 512,
        }

        # Create the actual cache directory
        cache_dir = cache_manager.cache_root / f"prep_{cache_key}"
        cache_dir.mkdir()
        data_dir = cache_dir / "data"
        data_dir.mkdir()

        # Get cached preparation should find it via index
        result = cache_manager.get_cached_preparation(excel_path)
        assert result == data_dir

    def test_index_updated_on_save(self, cache_manager, tmp_path):
        """Test that index is updated when saving a preparation."""
        # Create test data
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "test.json").write_text("{}")

        excel_path = tmp_path / "test.xlsx"
        excel_path.write_bytes(b"mock excel content")

        # Save preparation
        cache_manager.save_preparation(source_dir, excel_path, 7.5)

        # Check index was updated
        cache_key = cache_manager.get_cache_key(excel_path)
        assert cache_key in cache_manager.index["entries"]
        entry = cache_manager.index["entries"][cache_key]
        assert entry["master_excel_path"] == str(excel_path)
        assert entry["preparation_time_seconds"] == 7.5
        assert entry["file_count"] == 1

        # Check index file was saved
        index_data = json.loads(cache_manager.index_path.read_text())
        assert cache_key in index_data["entries"]

    def test_index_updated_on_clear(self, cache_manager, tmp_path):
        """Test that index is updated when clearing cache."""
        # Create multiple cache entries
        for i in range(3):
            source_dir = tmp_path / f"source_{i}"
            source_dir.mkdir()
            (source_dir / "test.json").write_text("{}")

            excel_path = tmp_path / f"test_{i}.xlsx"
            excel_path.write_bytes(f"mock excel content {i}".encode())

            cache_manager.save_preparation(source_dir, excel_path, i * 1.5)

        # Check all entries are in index
        assert len(cache_manager.index["entries"]) == 3

        # Clear all but one cache
        cache_manager.clear_cache(keep_recent=1)

        # Check index was updated
        assert len(cache_manager.index["entries"]) == 1

        # Check index file was saved
        index_data = json.loads(cache_manager.index_path.read_text())
        assert len(index_data["entries"]) == 1

    def test_index_synced_when_out_of_date(self, cache_manager, tmp_path):
        """Test that index is synced when filesystem doesn't match."""
        excel_path = tmp_path / "test.xlsx"
        excel_path.write_bytes(b"mock excel content")

        cache_key = cache_manager.get_cache_key(excel_path)

        # Add entry to index without creating directory
        cache_manager.index["entries"][cache_key] = {
            "master_excel_path": str(excel_path),
            "created_at": datetime.now().isoformat(),
            "preparation_time_seconds": 5.0,
            "file_count": 3,
            "total_size_bytes": 512,
        }

        # Try to get cached preparation - should remove from index
        result = cache_manager.get_cached_preparation(excel_path)
        assert result is None
        assert cache_key not in cache_manager.index["entries"]

    def test_index_handles_corrupted_file(self, cache_dir):
        """Test that corrupted index file triggers rebuild."""
        # Write corrupted index
        index_path = cache_dir / "index.json"
        index_path.write_text("corrupted json data {")

        # Initialize cache manager - should rebuild index
        cache_manager = DataPreparationCache(cache_root=cache_dir)

        # Check index was rebuilt
        assert cache_manager.index["version"] == cache_manager.CACHE_VERSION
        assert isinstance(cache_manager.index["entries"], dict)

    def test_find_cache_by_excel_name(self, cache_manager, tmp_path):
        """Test finding caches by Excel filename (useful feature)."""
        # Create caches for different Excel files
        excel_files = ["model_v1.xlsx", "model_v2.xlsx", "other_model.xlsx"]

        for excel_name in excel_files:
            source_dir = tmp_path / f"source_{excel_name}"
            source_dir.mkdir()
            (source_dir / "test.json").write_text("{}")

            excel_path = tmp_path / excel_name
            excel_path.write_bytes(f"content for {excel_name}".encode())

            cache_manager.save_preparation(source_dir, excel_path, 1.0)

        # Add method to find caches by filename pattern
        def find_caches_by_pattern(pattern):
            results = []
            for cache_key, entry in cache_manager.index["entries"].items():
                excel_name = Path(entry["master_excel_path"]).name
                if pattern in excel_name:
                    results.append((cache_key, entry))
            return results

        # Test finding by pattern
        model_caches = find_caches_by_pattern("model_v")
        assert len(model_caches) == 2

        other_caches = find_caches_by_pattern("other")
        assert len(other_caches) == 1
        assert "other_model.xlsx" in other_caches[0][1]["master_excel_path"]
