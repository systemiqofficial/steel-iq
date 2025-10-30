"""Test cache CLI functionality without running full simulations."""

import json
import sys


def test_cache_stats_command_standalone(tmp_path):
    """Test cache stats command without running simulation."""
    # For e2e tests, we want to test the actual CLI, but we can optimize
    # by using a lighter approach
    import sys
    from unittest.mock import patch

    # Create a mock cache directory with metadata
    cache_dir = tmp_path / "preparation_cache" / "prep_test456"
    cache_dir.mkdir(parents=True)
    (cache_dir / "metadata.json").write_text(
        json.dumps(
            {
                "master_excel_path": "/tmp/test.xlsx",
                "master_excel_hash": "test456",
                "created_at": "2024-01-01T00:00:00",
                "cache_version": "1.0",
                "preparation_time_seconds": 10.0,
                "file_count": 5,
                "total_size_bytes": 1048576,  # Exactly 1MB
            }
        )
    )

    # Instead of subprocess, call the CLI module directly with mocked argv
    # This is much faster than launching a new Python process
    test_args = [
        "steelo-cache",
        "stats",
        "--cache-dir",
        str(tmp_path / "preparation_cache"),
    ]

    with patch.object(sys, "argv", test_args):
        # Import and run the CLI directly
        from steelo.entrypoints.cache_cli import steelo_cache

        # The CLI might use Rich console output, which we can't easily capture
        # Just ensure it runs without error
        try:
            result = steelo_cache()
            # Should return None or a success message, not an error message
            assert result != "No command specified"
        except SystemExit as e:
            # CLI might call sys.exit(0) on success
            assert e.code == 0


def test_cache_list_command(tmp_path):
    """Test cache list command."""
    from unittest.mock import patch

    # Create mock cache entries
    for i in range(2):
        cache_dir = tmp_path / "preparation_cache" / f"prep_test{i:03d}"
        cache_dir.mkdir(parents=True)
        (cache_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "master_excel_path": f"/tmp/test{i}.xlsx",
                    "master_excel_hash": f"test{i:03d}",
                    "created_at": f"2024-01-0{i + 1}T00:00:00",
                    "cache_version": "1.0",
                    "preparation_time_seconds": 10.0 + i,
                    "file_count": 5 + i,
                    "total_size_bytes": 1048576 * (i + 1),
                }
            )
        )

    # Call CLI directly instead of subprocess
    test_args = [
        "steelo-cache",
        "list",
        "--cache-dir",
        str(tmp_path / "preparation_cache"),
    ]

    with patch.object(sys, "argv", test_args):
        from steelo.entrypoints.cache_cli import steelo_cache

        try:
            result = steelo_cache()
            assert result != "No command specified"
        except SystemExit as e:
            assert e.code == 0


def test_clear_cache_command(tmp_path):
    """Test clear cache command using steelo-cache CLI."""
    from unittest.mock import patch

    # Create a mock cache directory
    cache_dir = tmp_path / "preparation_cache" / "prep_test123"
    cache_dir.mkdir(parents=True)
    (cache_dir / "metadata.json").write_text(
        json.dumps(
            {
                "master_excel_path": "/tmp/test.xlsx",
                "master_excel_hash": "test123",
                "created_at": "2024-01-01T00:00:00",
                "cache_version": "1.0",
                "preparation_time_seconds": 10.0,
                "file_count": 5,
                "total_size_bytes": 1000,
            }
        )
    )

    # Create mock data cache too
    data_cache_dir = tmp_path / "data_cache" / "core-data"
    data_cache_dir.mkdir(parents=True)
    (data_cache_dir / "test.json").write_text('{"test": "data"}')

    # Verify caches exist before clearing
    assert cache_dir.exists()
    assert (cache_dir / "metadata.json").exists()
    assert data_cache_dir.exists()

    # Call CLI directly with mocked input
    test_args = [
        "steelo-cache",
        "clear",
        "--keep-recent",
        "0",
        "--cache-dir",
        str(tmp_path / "preparation_cache"),
        "--data-cache-dir",
        str(tmp_path / "data_cache"),
    ]

    with patch.object(sys, "argv", test_args):
        with patch("builtins.input", return_value="y"):
            from steelo.entrypoints.cache_cli import steelo_cache

            try:
                result = steelo_cache()
                assert result != "No command specified"
            except SystemExit as e:
                assert e.code == 0

    # Check that both cache directories were removed
    assert not cache_dir.exists(), f"Preparation cache directory still exists: {cache_dir}"
    assert not data_cache_dir.exists(), f"Data cache directory still exists: {data_cache_dir}"


# Removed test_clear_data_cache_command as we no longer support clearing individual cache types


def test_clear_all_caches_command(tmp_path):
    """Test clearing all caches using steelo-cache clear."""
    from unittest.mock import patch

    # Create both cache directories
    data_cache_dir = tmp_path / "data_cache" / "geo-data"
    data_cache_dir.mkdir(parents=True)
    (data_cache_dir / "test.json").write_text('{"test": "data"}')

    prep_cache_dir = tmp_path / "preparation_cache" / "prep_test789"
    prep_cache_dir.mkdir(parents=True)
    (prep_cache_dir / "metadata.json").write_text('{"test": "prep"}')

    # Verify both caches exist
    assert data_cache_dir.exists()
    assert prep_cache_dir.exists()

    # Call CLI directly
    test_args = [
        "steelo-cache",
        "clear",
        "--data-cache-dir",
        str(tmp_path / "data_cache"),
        "--cache-dir",
        str(tmp_path / "preparation_cache"),
    ]

    with patch.object(sys, "argv", test_args):
        with patch("builtins.input", return_value="y"):
            from steelo.entrypoints.cache_cli import steelo_cache

            try:
                result = steelo_cache()
                assert result != "No command specified"
            except SystemExit as e:
                assert e.code == 0

    # Verify both caches were cleared
    assert not data_cache_dir.exists()
    assert not prep_cache_dir.exists()
