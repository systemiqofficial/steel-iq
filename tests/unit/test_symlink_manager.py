"""Tests for symlink manager functionality."""

import pytest
import os

from steelo.utils.symlink_manager import (
    create_symlink_with_backup,
    update_data_symlink,
    update_output_symlink,
    setup_legacy_symlinks,
    _cleanup_old_backups,
)


class TestSymlinkManager:
    """Test symlink management functionality."""

    @pytest.fixture
    def test_dir(self, tmp_path):
        """Create a test directory structure."""
        test_root = tmp_path / "test_root"
        test_root.mkdir()

        # Create some test content
        (test_root / "target_dir").mkdir()
        (test_root / "target_dir" / "file.txt").write_text("content")

        return test_root

    def test_create_symlink_basic(self, test_dir):
        """Test creating a basic symlink."""
        target = test_dir / "target_dir"
        link = test_dir / "link"

        create_symlink_with_backup(target, link)

        assert link.is_symlink()
        assert link.resolve() == target.resolve()
        assert (link / "file.txt").read_text() == "content"

    def test_create_symlink_with_existing_file_backup(self, test_dir):
        """Test creating symlink backs up existing file."""
        target = test_dir / "target_dir"
        link = test_dir / "link"

        # Create existing file
        link.write_text("existing content")

        create_symlink_with_backup(target, link)

        # Check symlink created
        assert link.is_symlink()
        assert link.resolve() == target.resolve()

        # Check backup created
        backups = list(test_dir.glob("link_backup_*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "existing content"

    def test_create_symlink_with_existing_directory_backup(self, test_dir):
        """Test creating symlink backs up existing directory."""
        target = test_dir / "target_dir"
        link = test_dir / "link"

        # Create existing directory
        link.mkdir()
        (link / "existing.txt").write_text("existing dir content")

        create_symlink_with_backup(target, link)

        # Check symlink created
        assert link.is_symlink()
        assert link.resolve() == target.resolve()

        # Check backup created
        backups = list(test_dir.glob("link_backup_*"))
        assert len(backups) == 1
        assert backups[0].is_dir()
        assert (backups[0] / "existing.txt").read_text() == "existing dir content"

    def test_create_symlink_replaces_existing_symlink(self, test_dir):
        """Test creating symlink replaces existing symlink without backup."""
        target1 = test_dir / "target_dir"
        target2 = test_dir / "target_dir2"
        target2.mkdir()
        link = test_dir / "link"

        # Create first symlink
        create_symlink_with_backup(target1, link)
        assert link.resolve() == target1.resolve()

        # Replace with new symlink
        create_symlink_with_backup(target2, link)
        assert link.resolve() == target2.resolve()

        # No backup should be created for symlinks
        backups = list(test_dir.glob("link_backup_*"))
        assert len(backups) == 0

    def test_cleanup_old_backups(self, test_dir):
        """Test cleanup of old backups."""
        # Create multiple backups with different timestamps
        for i in range(7):
            backup = test_dir / f"test_backup_{20240101000000 + i}"
            backup.write_text(f"backup {i}")

        # Keep only 3 most recent
        _cleanup_old_backups(test_dir, "test_backup_", max_backups=3)

        # Check that only 3 remain
        remaining = list(test_dir.glob("test_backup_*"))
        assert len(remaining) == 3

        # Check that the most recent ones are kept
        names = sorted([b.name for b in remaining])
        assert names == ["test_backup_20240101000004", "test_backup_20240101000005", "test_backup_20240101000006"]

    def test_update_data_symlink(self, test_dir):
        """Test updating data symlink."""
        steelo_home = test_dir / "steelo_home"
        steelo_home.mkdir()

        data_prep_dir = test_dir / "prep_dir"
        data_prep_dir.mkdir()
        (data_prep_dir / "fixtures").mkdir()
        (data_prep_dir / "fixtures" / "plants.json").write_text("{}")

        update_data_symlink(steelo_home, data_prep_dir)

        data_link = steelo_home / "data"
        assert data_link.is_symlink()
        assert data_link.resolve() == data_prep_dir.resolve()
        assert (data_link / "fixtures" / "plants.json").exists()

    def test_update_output_symlink(self, test_dir):
        """Test updating output symlink."""
        steelo_home = test_dir / "steelo_home"
        steelo_home.mkdir()

        sim_output_dir = test_dir / "sim_20240101_120000"
        sim_output_dir.mkdir()
        (sim_output_dir / "plots").mkdir()

        update_output_symlink(steelo_home, sim_output_dir)

        output_link = steelo_home / "output_latest"
        assert output_link.is_symlink()
        assert output_link.resolve() == sim_output_dir.resolve()
        assert (output_link / "plots").exists()

    def test_setup_legacy_symlinks(self, test_dir):
        """Test setting up legacy symlinks in project root."""
        project_root = test_dir / "project"
        project_root.mkdir()

        steelo_home = test_dir / "steelo_home"
        steelo_home.mkdir()

        # Create steelo_home structure
        data_dir = steelo_home / "data"
        data_dir.mkdir()
        (data_dir / "fixtures").mkdir()

        output_dir = steelo_home / "output_latest"
        output_dir.mkdir()
        (output_dir / "plots").mkdir()

        # Setup legacy symlinks
        setup_legacy_symlinks(project_root, steelo_home)

        # Check symlinks created in project root
        assert (project_root / "data").is_symlink()
        assert (project_root / "data").resolve() == data_dir.resolve()
        assert (project_root / "data" / "fixtures").exists()

        assert (project_root / "output").is_symlink()
        assert (project_root / "output").resolve() == output_dir.resolve()
        assert (project_root / "output" / "plots").exists()

    def test_setup_legacy_symlinks_with_existing_dirs(self, test_dir):
        """Test setting up legacy symlinks backs up existing directories."""
        project_root = test_dir / "project"
        project_root.mkdir()

        # Create existing data and output directories
        (project_root / "data").mkdir()
        (project_root / "data" / "old_file.txt").write_text("old data")

        (project_root / "output").mkdir()
        (project_root / "output" / "old_output.txt").write_text("old output")

        steelo_home = test_dir / "steelo_home"
        steelo_home.mkdir()

        # Create steelo_home structure
        data_dir = steelo_home / "data"
        data_dir.mkdir()

        output_dir = steelo_home / "output_latest"
        output_dir.mkdir()

        # Setup legacy symlinks
        setup_legacy_symlinks(project_root, steelo_home)

        # Check symlinks created
        assert (project_root / "data").is_symlink()
        assert (project_root / "output").is_symlink()

        # Check backups created
        data_backups = list(project_root.glob("data_backup_*"))
        assert len(data_backups) == 1
        assert (data_backups[0] / "old_file.txt").read_text() == "old data"

        output_backups = list(project_root.glob("output_backup_*"))
        assert len(output_backups) == 1
        assert (output_backups[0] / "old_output.txt").read_text() == "old output"

    def test_create_symlink_nonexistent_target_raises(self, test_dir):
        """Test that creating symlink to nonexistent target raises error."""
        target = test_dir / "nonexistent"
        link = test_dir / "link"

        with pytest.raises(ValueError, match="Target path does not exist"):
            create_symlink_with_backup(target, link)

    def test_relative_symlink_same_parent(self, test_dir):
        """Test that symlinks use relative paths when in same parent directory."""
        parent = test_dir / "parent"
        parent.mkdir()

        target = parent / "target"
        target.mkdir()

        link = parent / "link"

        create_symlink_with_backup(target, link)

        # Check that the symlink uses relative path
        assert link.is_symlink()
        # Read the raw symlink target
        raw_target = os.readlink(str(link))
        assert raw_target == "target"  # Should be relative, not absolute
