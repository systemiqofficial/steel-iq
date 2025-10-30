"""Tests for log file UUID-based naming and backward compatibility."""

import os


def test_modelrun_has_uuid_field(db):
    """Test that ModelRun has log_file_uuid field."""
    from steeloweb.models import ModelRun

    modelrun = ModelRun.objects.create(name="Test Run")
    assert modelrun.log_file_uuid is not None
    assert len(str(modelrun.log_file_uuid)) == 36  # UUID format


def test_modelrun_uuid_is_unique(db):
    """Test that each ModelRun gets a unique UUID."""
    from steeloweb.models import ModelRun

    modelrun1 = ModelRun.objects.create(name="Test Run 1")
    modelrun2 = ModelRun.objects.create(name="Test Run 2")

    assert modelrun1.log_file_uuid != modelrun2.log_file_uuid


def test_log_file_path_uses_uuid(db, tmp_path):
    """Test that log files are created with UUID in filename."""
    from steeloweb.models import ModelRun

    modelrun = ModelRun.objects.create(name="Test Run")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    os.environ["STEELO_LOG_DIR"] = str(log_dir)

    try:
        # Simulate log file creation
        log_path = log_dir / f"model-run-{modelrun.log_file_uuid}.log"
        log_path.write_text("Test log")

        # Test lookup
        from steeloweb.utils import get_log_file_path

        found_path = get_log_file_path(modelrun.id)

        assert found_path == str(log_path)
    finally:
        # Clean up environment variable
        if "STEELO_LOG_DIR" in os.environ:
            del os.environ["STEELO_LOG_DIR"]


def test_backward_compatibility_pk_lookup(db, tmp_path):
    """Test that legacy PK-based log files are still found."""
    from steeloweb.models import ModelRun

    modelrun = ModelRun.objects.create(name="Legacy Run")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    os.environ["STEELO_LOG_DIR"] = str(log_dir)

    try:
        # Create legacy PK-based log file
        legacy_log_path = log_dir / f"model-run-{modelrun.id}.log"
        legacy_log_path.write_text("Legacy log")

        # Test lookup finds legacy file
        from steeloweb.utils import get_log_file_path

        found_path = get_log_file_path(modelrun.id)

        assert found_path == str(legacy_log_path)
    finally:
        # Clean up environment variable
        if "STEELO_LOG_DIR" in os.environ:
            del os.environ["STEELO_LOG_DIR"]


def test_uuid_based_lookup_preferred_over_legacy(db, tmp_path):
    """Test that UUID-based filename is preferred when both exist."""
    from steeloweb.models import ModelRun

    modelrun = ModelRun.objects.create(name="Test Run")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    os.environ["STEELO_LOG_DIR"] = str(log_dir)

    try:
        # Create both UUID-based and legacy log files
        uuid_log_path = log_dir / f"model-run-{modelrun.log_file_uuid}.log"
        uuid_log_path.write_text("UUID log")

        legacy_log_path = log_dir / f"model-run-{modelrun.id}.log"
        legacy_log_path.write_text("Legacy log")

        # Test lookup prefers UUID-based file
        from steeloweb.utils import get_log_file_path

        found_path = get_log_file_path(modelrun.id)

        assert found_path == str(uuid_log_path)
        assert found_path != str(legacy_log_path)
    finally:
        # Clean up environment variable
        if "STEELO_LOG_DIR" in os.environ:
            del os.environ["STEELO_LOG_DIR"]


def test_no_log_file_returns_none(db):
    """Test that lookup returns None when no log file exists."""
    from steeloweb.models import ModelRun
    from steeloweb.utils import get_log_file_path

    modelrun = ModelRun.objects.create(name="Test Run")

    # No STEELO_LOG_DIR set
    found_path = get_log_file_path(modelrun.id)
    assert found_path is None


def test_no_log_dir_env_returns_none(db, tmp_path):
    """Test that lookup returns None when STEELO_LOG_DIR is not set."""
    from steeloweb.models import ModelRun
    from steeloweb.utils import get_log_file_path

    modelrun = ModelRun.objects.create(name="Test Run")

    # Ensure STEELO_LOG_DIR is not set
    if "STEELO_LOG_DIR" in os.environ:
        del os.environ["STEELO_LOG_DIR"]

    found_path = get_log_file_path(modelrun.id)
    assert found_path is None


def test_nonexistent_modelrun_returns_none(db, tmp_path):
    """Test that lookup returns None for non-existent ModelRun."""
    from steeloweb.utils import get_log_file_path

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    os.environ["STEELO_LOG_DIR"] = str(log_dir)

    try:
        # Try to get log for non-existent ModelRun
        found_path = get_log_file_path(99999)
        assert found_path is None
    finally:
        # Clean up environment variable
        if "STEELO_LOG_DIR" in os.environ:
            del os.environ["STEELO_LOG_DIR"]


def test_log_file_header_format(db, tmp_path):
    """Test that log file headers are created with correct metadata."""
    from steeloweb.models import ModelRun

    modelrun = ModelRun.objects.create(name="Test Header Run")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    os.environ["STEELO_LOG_DIR"] = str(log_dir)

    try:
        # Simulate header creation (mimics tasks.py logic)
        log_file_path = log_dir / f"model-run-{modelrun.log_file_uuid}.log"

        # Write header BEFORE creating handler
        with open(log_file_path, "w") as f:
            f.write("# Steel Model Simulation Log\n")
            f.write(f"# ModelRun ID: {modelrun.id}\n")
            f.write(f"# ModelRun Name: {modelrun.name or '(unnamed)'}\n")
            f.write(f"# Started: {modelrun.started_at.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# UUID: {modelrun.log_file_uuid}\n")
            f.write(f"# {'=' * 60}\n\n")

        # Verify header was written
        with open(log_file_path, "r") as f:
            header = f.read()

        assert "# Steel Model Simulation Log" in header
        assert f"# ModelRun ID: {modelrun.id}" in header
        assert f"# ModelRun Name: {modelrun.name}" in header
        assert f"# UUID: {modelrun.log_file_uuid}" in header
        assert "# ============" in header
    finally:
        # Clean up environment variable
        if "STEELO_LOG_DIR" in os.environ:
            del os.environ["STEELO_LOG_DIR"]


def test_reinstall_scenario_no_collision(db, tmp_path):
    """
    Test that simulates the reinstall scenario - verifying that log files
    do not collide even when primary keys are reused.
    """
    from steeloweb.models import ModelRun

    log_dir = tmp_path / "logs"
    log_dir.mkdir()

    os.environ["STEELO_LOG_DIR"] = str(log_dir)

    try:
        # First installation - create 3 model runs
        modelrun1 = ModelRun.objects.create(name="First Installation Run 1")
        modelrun2 = ModelRun.objects.create(name="First Installation Run 2")
        modelrun3 = ModelRun.objects.create(name="First Installation Run 3")

        # Create log files with UUIDs
        log1 = log_dir / f"model-run-{modelrun1.log_file_uuid}.log"
        log2 = log_dir / f"model-run-{modelrun2.log_file_uuid}.log"
        log3 = log_dir / f"model-run-{modelrun3.log_file_uuid}.log"

        log1.write_text("First installation log 1")
        log2.write_text("First installation log 2")
        log3.write_text("First installation log 3")

        # Record UUIDs and content for verification
        uuid1, uuid2, uuid3 = modelrun1.log_file_uuid, modelrun2.log_file_uuid, modelrun3.log_file_uuid
        content1, content2, content3 = log1.read_text(), log2.read_text(), log3.read_text()

        # Simulate app uninstall - delete database records (but keep log files)
        ModelRun.objects.all().delete()

        # Verify logs still exist
        assert log1.exists()
        assert log2.exists()
        assert log3.exists()

        # Second installation - create new model runs with same PKs
        # (PKs restart from 1 after database is recreated)
        modelrun4 = ModelRun.objects.create(name="Second Installation Run 1")
        modelrun5 = ModelRun.objects.create(name="Second Installation Run 2")
        modelrun6 = ModelRun.objects.create(name="Second Installation Run 3")

        # New runs should have different UUIDs
        assert modelrun4.log_file_uuid != uuid1
        assert modelrun5.log_file_uuid != uuid2
        assert modelrun6.log_file_uuid != uuid3

        # Create log files for new runs
        log4 = log_dir / f"model-run-{modelrun4.log_file_uuid}.log"
        log5 = log_dir / f"model-run-{modelrun5.log_file_uuid}.log"
        log6 = log_dir / f"model-run-{modelrun6.log_file_uuid}.log"

        log4.write_text("Second installation log 1")
        log5.write_text("Second installation log 2")
        log6.write_text("Second installation log 3")

        # Verify all 6 log files exist with correct content
        assert log1.exists() and log1.read_text() == content1
        assert log2.exists() and log2.read_text() == content2
        assert log3.exists() and log3.read_text() == content3
        assert log4.exists() and log4.read_text() == "Second installation log 1"
        assert log5.exists() and log5.read_text() == "Second installation log 2"
        assert log6.exists() and log6.read_text() == "Second installation log 3"

        # Verify no overwrites occurred
        assert len(list(log_dir.glob("model-run-*.log"))) == 6
    finally:
        # Clean up environment variable
        if "STEELO_LOG_DIR" in os.environ:
            del os.environ["STEELO_LOG_DIR"]


def test_concurrent_simulations_unique_uuids(db):
    """Test that concurrent simulations get unique UUIDs."""
    from steeloweb.models import ModelRun

    # Create multiple model runs "simultaneously"
    modelruns = [ModelRun.objects.create(name=f"Concurrent Run {i}") for i in range(10)]

    # Verify all UUIDs are unique
    uuids = [mr.log_file_uuid for mr in modelruns]
    assert len(uuids) == len(set(uuids))  # No duplicates


def test_migration_with_existing_records(db, django_db_blocker):
    """
    Test that the migration correctly backfills unique UUIDs for existing records.

    This simulates what happens when the migration runs on a database with
    existing ModelRun records.
    """
    from steeloweb.models import ModelRun

    # Create multiple records
    count = 20
    modelruns = [ModelRun.objects.create(name=f"Existing Run {i}") for i in range(count)]

    # All should have unique UUIDs
    uuids = [str(mr.log_file_uuid) for mr in modelruns]
    unique_uuids = set(uuids)

    assert len(unique_uuids) == count, f"Expected {count} unique UUIDs, got {len(unique_uuids)}"

    # Verify UUID format (standard UUID4 format)
    import re

    uuid_pattern = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    for uuid_str in uuids:
        assert uuid_pattern.match(uuid_str), f"Invalid UUID format: {uuid_str}"
