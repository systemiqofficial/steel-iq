import pytest
import tempfile
from pathlib import Path

from steelo.data import DataPreparationService, DataManager
from steelo.data.cache_manager import DataPreparationCache


@pytest.fixture
def temp_dirs():
    """Create temporary directories for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)
        dirs = {
            "cache": base / "cache",
            "output": base / "output",
            "excel": base / "excel",
        }
        for d in dirs.values():
            d.mkdir(parents=True)
        yield dirs


@pytest.fixture
def mock_master_excel(temp_dirs):
    """Create a mock master Excel file."""
    excel_path = temp_dirs["excel"] / "master_input.xlsx"
    # Create a minimal valid Excel file (ZIP format)
    excel_path.write_bytes(b"PK\x03\x04" + b"X" * 10000)
    return excel_path


@pytest.fixture
def prep_service(temp_dirs):
    """Create data preparation service with test cache."""
    cache_manager = DataPreparationCache(cache_root=temp_dirs["cache"])
    return DataPreparationService(
        data_manager=DataManager(offline_mode=True), cache_manager=cache_manager, use_cache=True
    )


@pytest.fixture
def prep_service_with_mock_data(temp_dirs):
    """Create a data preparation service with mocked data manager."""
    from unittest.mock import Mock

    # Create a mock data manager that doesn't download
    mock_data_manager = Mock(spec=DataManager)

    # Mock the download_package method
    mock_data_manager.download_package = Mock()

    # Mock get_package_path to return a test directory
    package_dir = temp_dirs["cache"] / "test_package"
    package_dir.mkdir(parents=True)

    # Create a minimal master Excel file
    master_excel = package_dir / "master_input.xlsx"
    master_excel.write_bytes(b"PK\x03\x04" + b"X" * 10000)

    mock_data_manager.get_package_path = Mock(return_value=package_dir)

    cache_manager = DataPreparationCache(cache_root=temp_dirs["cache"])
    return DataPreparationService(data_manager=mock_data_manager, cache_manager=cache_manager, use_cache=True)


def test_first_preparation_creates_cache_mock(prep_service_with_mock_data, temp_dirs):
    """Test that first preparation creates cache using mocked data."""
    # Use the mock master excel created in fixture
    master_excel = temp_dirs["cache"] / "test_package" / "master_input.xlsx"
    output_dir = temp_dirs["output"] / "run1"

    # Mock the internal prepare method to simulate data preparation
    from unittest.mock import patch

    with patch.object(prep_service_with_mock_data, "_prepare_data_internal") as mock_prepare:
        # Create a fake result
        from steelo.data.preparation import PreparationResult, PreparedFile, FileSource, PreparationStep

        fake_result = PreparationResult()
        fake_result.output_directory = output_dir / "fixtures"
        fake_result.master_excel_path = master_excel
        fake_result.total_duration = 5.0
        fake_result.add_file(
            PreparedFile(
                filename="test.json",
                source=FileSource.MASTER_EXCEL,
                source_detail="Test sheet",
                duration=1.0,
                path=output_dir / "fixtures" / "test.json",
            )
        )
        fake_result.add_step(PreparationStep("Test step", 1.0))
        mock_prepare.return_value = fake_result

        # Create the output file
        (output_dir / "fixtures").mkdir(parents=True, exist_ok=True)
        (output_dir / "fixtures" / "test.json").write_text("{}")

        # First run - should prepare and cache
        result1 = prep_service_with_mock_data.prepare_data(
            output_dir=output_dir / "fixtures", master_excel_path=master_excel, skip_existing=True, verbose=False
        )

    assert result1.total_duration > 0
    assert len(result1.files) > 0
    assert result1.master_excel_path is not None

    # Verify cache was created
    cache_manager = prep_service_with_mock_data.cache_manager
    cached = cache_manager.get_cached_preparation(result1.master_excel_path)
    assert cached is not None


def test_second_preparation_uses_cache_mock(prep_service_with_mock_data, temp_dirs):
    """Test that second preparation uses cache with mocked data."""
    master_excel = temp_dirs["cache"] / "test_package" / "master_input.xlsx"

    # Create test data directory
    test_data_dir = temp_dirs["cache"] / "test_data"
    test_data_dir.mkdir(parents=True)
    (test_data_dir / "test.json").write_text('{"test": "data"}')

    # Manually save to cache
    cache_manager = prep_service_with_mock_data.cache_manager
    cache_manager.save_preparation(source_dir=test_data_dir, master_excel_path=master_excel, preparation_time=10.0)

    # Now test that prepare_data uses the cache
    output_dir = temp_dirs["output"] / "run2"
    result = prep_service_with_mock_data.prepare_data(
        output_dir=output_dir / "fixtures", master_excel_path=master_excel, verbose=False
    )

    # Should be very fast (just copying from cache)
    assert result.total_duration < 1.0
    assert (output_dir / "fixtures" / "test.json").exists()
    assert (output_dir / "fixtures" / "test.json").read_text() == '{"test": "data"}'


def test_force_refresh_bypasses_cache_mock(prep_service_with_mock_data, temp_dirs):
    """Test that force_refresh bypasses cache with mocked data."""
    master_excel = temp_dirs["cache"] / "test_package" / "master_input.xlsx"

    # Create and save to cache
    test_data_dir = temp_dirs["cache"] / "test_data"
    test_data_dir.mkdir(parents=True)
    (test_data_dir / "cached.json").write_text('{"cached": true}')

    cache_manager = prep_service_with_mock_data.cache_manager
    cache_manager.save_preparation(source_dir=test_data_dir, master_excel_path=master_excel, preparation_time=5.0)

    # Mock the internal prepare method
    from unittest.mock import patch

    with patch.object(prep_service_with_mock_data, "_prepare_data_internal") as mock_prepare:
        from steelo.data.preparation import PreparationResult

        fake_result = PreparationResult()
        fake_result.total_duration = 10.0  # Longer than cache copy
        fake_result.master_excel_path = master_excel
        mock_prepare.return_value = fake_result

        output_dir = temp_dirs["output"] / "run_force"
        result = prep_service_with_mock_data.prepare_data(
            output_dir=output_dir / "fixtures", master_excel_path=master_excel, force_refresh=True, verbose=False
        )

        # Should have called internal method (not used cache)
        mock_prepare.assert_called_once()
        assert result.total_duration >= 10.0


def test_cache_disabled_mock(temp_dirs):
    """Test preparation with cache disabled using mocks."""
    from unittest.mock import Mock

    mock_data_manager = Mock(spec=DataManager)
    cache_manager = DataPreparationCache(cache_root=temp_dirs["cache"])

    prep_service = DataPreparationService(
        data_manager=mock_data_manager,
        cache_manager=cache_manager,
        use_cache=False,  # Disable cache
    )

    master_excel = temp_dirs["excel"] / "test.xlsx"
    master_excel.write_bytes(b"PK\x03\x04" + b"X" * 1000)

    # Mock the internal prepare method
    from unittest.mock import patch

    with patch.object(prep_service, "_prepare_data_internal") as mock_prepare:
        from steelo.data.preparation import PreparationResult

        fake_result = PreparationResult()
        fake_result.master_excel_path = master_excel
        fake_result.total_duration = 5.0
        mock_prepare.return_value = fake_result

        output_dir = temp_dirs["output"] / "run1"
        prep_service.prepare_data(output_dir=output_dir / "fixtures", master_excel_path=master_excel, verbose=False)

    # Cache should not be created since use_cache=False
    cached = cache_manager.get_cached_preparation(master_excel)
    assert cached is None


def test_cache_with_different_master_excel(prep_service, temp_dirs):
    """Test that different master Excel files use different caches."""
    # Create two different master Excel files
    excel1 = temp_dirs["excel"] / "master1.xlsx"
    excel1.write_bytes(b"PK\x03\x04" + b"Content1" * 1000)

    excel2 = temp_dirs["excel"] / "master2.xlsx"
    excel2.write_bytes(b"PK\x03\x04" + b"Content2" * 1000)

    # Mock the preparation process
    output_dir1 = temp_dirs["output"] / "run1"
    output_dir1.mkdir(parents=True)
    (output_dir1 / "test.txt").write_text("test1")

    output_dir2 = temp_dirs["output"] / "run2"
    output_dir2.mkdir(parents=True)
    (output_dir2 / "test.txt").write_text("test2")

    # Save preparations for both Excel files
    cache_manager = prep_service.cache_manager
    cache_manager.save_preparation(output_dir1, excel1, 1.0)
    cache_manager.save_preparation(output_dir2, excel2, 1.0)

    # Verify different caches
    cached1 = cache_manager.get_cached_preparation(excel1)
    cached2 = cache_manager.get_cached_preparation(excel2)

    assert cached1 is not None
    assert cached2 is not None
    assert cached1 != cached2
    assert (cached1 / "test.txt").read_text() == "test1"
    assert (cached2 / "test.txt").read_text() == "test2"
