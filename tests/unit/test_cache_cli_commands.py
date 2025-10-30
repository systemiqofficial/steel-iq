"""Unit tests for cache CLI commands."""

import pytest
from pathlib import Path
import json
from unittest.mock import patch, MagicMock

from steelo.data.cache_manager import DataPreparationCache
from steelo.entrypoints.cache_cli import steelo_cache


class TestCacheCLICommands:
    """Test the cache CLI commands directly."""

    @pytest.fixture
    def mock_cache_dir(self, tmp_path):
        """Create a mock cache directory with test data."""
        cache_dir = tmp_path / "preparation_cache"
        cache_dir.mkdir()

        # Create multiple test caches
        for i in range(3):
            prep_dir = cache_dir / f"prep_test{i:03d}"
            prep_dir.mkdir()
            (prep_dir / "data").mkdir()

            # Create some dummy files
            for j in range(2):
                (prep_dir / "data" / f"file{j}.json").write_text(f"content {i}-{j}")

            # Create metadata
            metadata = {
                "master_excel_path": f"/tmp/test{i}.xlsx",
                "master_excel_hash": f"test{i:03d}",
                "created_at": f"2024-01-0{i + 1}T00:00:00",
                "cache_version": "1.0",
                "preparation_time_seconds": 10.0 + i,
                "file_count": 2,
                "total_size_bytes": 1000 * (i + 1),
            }
            (prep_dir / "metadata.json").write_text(json.dumps(metadata))

        return cache_dir

    def test_cache_stats_functionality(self, mock_cache_dir):
        """Test cache stats functionality directly."""
        cache_manager = DataPreparationCache(cache_root=mock_cache_dir)
        stats = cache_manager.get_cache_stats()

        assert stats["total_preparations"] == 3
        assert stats["total_size_bytes"] > 0
        assert stats["oldest_cache"] is not None
        assert stats["newest_cache"] is not None
        assert stats["cache_directory"] == str(mock_cache_dir)

    def test_cache_list_functionality(self, mock_cache_dir):
        """Test listing cache entries."""
        DataPreparationCache(cache_root=mock_cache_dir)

        # Get all cache directories
        cache_entries = []
        for prep_dir in sorted(mock_cache_dir.glob("prep_*")):
            if prep_dir.is_dir():
                metadata_path = prep_dir / "metadata.json"
                if metadata_path.exists():
                    metadata = json.loads(metadata_path.read_text())
                    cache_entries.append(
                        {
                            "hash": prep_dir.name.replace("prep_", ""),
                            "created": metadata["created_at"],
                            "size_mb": metadata["total_size_bytes"] / (1024 * 1024),
                            "files": metadata["file_count"],
                            "excel": Path(metadata["master_excel_path"]).name,
                        }
                    )

        assert len(cache_entries) == 3
        assert cache_entries[0]["hash"] == "test000"
        assert cache_entries[0]["files"] == 2

    def test_cache_clear_functionality(self, mock_cache_dir):
        """Test clearing cache functionality."""
        cache_manager = DataPreparationCache(cache_root=mock_cache_dir)

        # Clear all caches
        removed = cache_manager.clear_cache()
        assert removed == 3

        # Verify all caches are gone
        remaining = list(mock_cache_dir.glob("prep_*"))
        assert len(remaining) == 0

    def test_cache_clear_keep_recent(self, mock_cache_dir):
        """Test clearing cache while keeping recent entries."""
        cache_manager = DataPreparationCache(cache_root=mock_cache_dir)

        # Clear keeping 1 most recent
        removed = cache_manager.clear_cache(keep_recent=1)
        assert removed == 2

        # Verify only the newest remains
        remaining = list(mock_cache_dir.glob("prep_*"))
        assert len(remaining) == 1
        assert remaining[0].name == "prep_test002"  # The last one created

    @patch("sys.argv", ["steelo-cache", "stats", "--cache-dir", "/tmp/cache"])
    @patch("steelo.entrypoints.cache_cli.Console")
    def test_cache_cli_stats_command(self, mock_console, mock_cache_dir):
        """Test the CLI stats command directly."""
        mock_console_instance = MagicMock()
        mock_console.return_value = mock_console_instance

        with patch("steelo.entrypoints.cache_cli.DataPreparationCache") as mock_cache_class:
            mock_cache = MagicMock()
            mock_cache.get_cache_stats.return_value = {
                "cache_directory": str(mock_cache_dir),
                "total_preparations": 3,
                "total_size_bytes": 3000,
                "total_size_mb": 0.003,
                "oldest_cache": "2024-01-01T00:00:00",
                "newest_cache": "2024-01-03T00:00:00",
            }
            mock_cache_class.return_value = mock_cache

            # Run the command
            result = steelo_cache()

            # Verify stats were retrieved
            mock_cache.get_cache_stats.assert_called_once()

            # Verify output was printed
            assert mock_console_instance.print.call_count >= 1
            assert "Cache stats completed" in result

    @patch("sys.argv", ["steelo-cache", "clear", "--keep-recent", "0", "--cache-dir", "/tmp/cache"])
    @patch("steelo.entrypoints.cache_cli.Console")
    def test_cache_cli_clear_command(self, mock_console, mock_cache_dir):
        """Test the CLI clear command directly."""
        mock_console_instance = MagicMock()
        mock_console.return_value = mock_console_instance

        # Mock user confirmation
        mock_console_instance.input.return_value = "y"

        with patch("steelo.entrypoints.cache_cli.DataPreparationCache") as mock_cache_class:
            mock_cache = MagicMock()
            mock_cache.clear_cache.return_value = 3
            mock_cache_class.return_value = mock_cache

            # Run the command
            result = steelo_cache()

            # Verify clear was called
            mock_cache.clear_cache.assert_called_once_with(keep_recent=0)

            # Verify output was printed
            assert "Cache clear completed" in result

    # Removed test_cache_cli_clear_preparation_only and test_cache_cli_clear_data_only
    # as we no longer support clearing individual cache types

    @patch("sys.argv", ["steelo-cache", "clear"])
    @patch("steelo.entrypoints.cache_cli.Console")
    def test_cache_cli_clear_all_caches(self, mock_console, mock_cache_dir):
        """Test clearing all caches."""
        mock_console_instance = MagicMock()
        mock_console.return_value = mock_console_instance
        mock_console_instance.input.return_value = "y"

        with patch("steelo.entrypoints.cache_cli.DataPreparationCache") as mock_cache_class:
            # Patch DataManager at the module level where it's imported inside the function
            with patch("steelo.data.DataManager") as mock_data_manager_class:
                mock_cache = MagicMock()
                mock_cache.clear_cache.return_value = 3
                mock_cache_class.return_value = mock_cache

                mock_data_manager = MagicMock()
                mock_data_manager_class.return_value = mock_data_manager

                result = steelo_cache()

                # Verify both caches were cleared
                mock_cache.clear_cache.assert_called_once()
                mock_data_manager.clear_cache.assert_called_once()
                assert "Cache clear completed" in result
