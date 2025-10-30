"""
Test tech switches extraction from Master Excel in DataPreparationService.
"""

import pytest
import pandas as pd
from unittest.mock import patch
from django.core.files.uploadedfile import SimpleUploadedFile

from steeloweb.models import DataPreparation, DataPackage, MasterExcelFile
from steeloweb.services import DataPreparationService


@pytest.fixture
def core_package(db):
    return DataPackage.objects.create(
        name="core-data", version="v1.0.0", source_type=DataPackage.SourceType.S3, source_url="s3://test/core.zip"
    )


@pytest.fixture
def geo_package(db):
    return DataPackage.objects.create(
        name="geo-data", version="v1.0.0", source_type=DataPackage.SourceType.S3, source_url="s3://test/geo.zip"
    )


@pytest.fixture
def master_excel_with_tech_switches(tmp_path):
    """Create a master Excel file with tech switches sheet."""
    excel_path = tmp_path / "master_with_tech_switches.xlsx"

    # Create tech switches data - rows are source tech, columns are target tech
    technologies = ["BF", "BOF", "DRI", "EAF", "ESF", "MOE"]
    # Initialize with empty strings
    data = {tech: [""] * len(technologies) for tech in technologies}

    # Set specific transitions
    # BF -> BOF
    data["BOF"][technologies.index("BF")] = "YES"
    # BOF -> EAF
    data["EAF"][technologies.index("BOF")] = "YES"
    # DRI -> BOF
    data["BOF"][technologies.index("DRI")] = "YES"
    # DRI -> EAF
    data["EAF"][technologies.index("DRI")] = "YES"
    # EAF -> ESF
    data["ESF"][technologies.index("EAF")] = "YES"
    # MOE -> EAF
    data["EAF"][technologies.index("MOE")] = "YES"

    tech_switches_df = pd.DataFrame(data, index=technologies)

    # Write Excel file
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        tech_switches_df.to_excel(writer, sheet_name="Allowed tech switches")
        # Add other sheets that might be required
        pd.DataFrame().to_excel(writer, sheet_name="Iron and steel plants")
        pd.DataFrame().to_excel(writer, sheet_name="Bill of Materials")

    # Create MasterExcelFile object
    with open(excel_path, "rb") as f:
        excel_file = SimpleUploadedFile("master_with_tech_switches.xlsx", f.read())
        return MasterExcelFile.objects.create(
            name="Master Excel with Tech Switches", file=excel_file, validation_status="valid"
        )


@pytest.fixture
def master_excel_missing_tech_switches(tmp_path):
    """Create a master Excel file without tech switches sheet."""
    excel_path = tmp_path / "master_no_tech_switches.xlsx"

    # Write Excel file without tech switches sheet
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        pd.DataFrame().to_excel(writer, sheet_name="Iron and steel plants")
        pd.DataFrame().to_excel(writer, sheet_name="Bill of Materials")

    # Create MasterExcelFile object
    with open(excel_path, "rb") as f:
        excel_file = SimpleUploadedFile("master_no_tech_switches.xlsx", f.read())
        return MasterExcelFile.objects.create(
            name="Master Excel without Tech Switches", file=excel_file, validation_status="valid"
        )


@pytest.mark.django_db
def test_tech_switches_extraction_success(core_package, geo_package, master_excel_with_tech_switches, tmp_path):
    """Test successful extraction of tech switches from Master Excel."""
    # Create DataPreparation
    prep = DataPreparation.objects.create(
        name="Test Prep with Tech Switches",
        core_data_package=core_package,
        geo_data_package=geo_package,
        master_excel=master_excel_with_tech_switches,
    )

    service = DataPreparationService()

    # Mock the underlying steelo service to verify tech switches extraction
    from steelo.data import PreparationResult, PreparedFile, FileSource

    mock_result = PreparationResult()
    mock_result.total_duration = 1.0

    # Create a mock tech_switches_allowed.csv file
    tech_switches_file = PreparedFile(
        filename="tech_switches_allowed.csv",
        source=FileSource.MASTER_EXCEL,
        source_detail="Allowed tech switches",
        duration=0.5,
        path=tmp_path / "tech_switches_allowed.csv",
    )
    mock_result.files = [tech_switches_file]

    # Create the actual CSV file that would be created
    tech_switches_path = tmp_path / "tech_switches_allowed.csv"
    # Create expected content based on our test data
    import pandas as pd

    technologies = ["BF", "BOF", "DRI", "EAF", "ESF", "MOE"]
    data = {tech: [""] * len(technologies) for tech in technologies}
    data["BOF"][technologies.index("BF")] = "YES"
    data["EAF"][technologies.index("BOF")] = "YES"
    data["BOF"][technologies.index("DRI")] = "YES"
    data["EAF"][technologies.index("DRI")] = "YES"
    data["ESF"][technologies.index("EAF")] = "YES"
    data["EAF"][technologies.index("MOE")] = "YES"
    df = pd.DataFrame(data, index=technologies)
    df.to_csv(tech_switches_path)

    with (
        patch.object(service.service, "prepare_data", return_value=mock_result),
        patch("tempfile.mkdtemp", return_value=str(tmp_path)),
        patch("shutil.move"),
    ):
        success, message = service.prepare_data(prep)

    # Check that preparation succeeded
    assert success is True
    assert prep.status == DataPreparation.Status.READY

    # Verify the file was tracked with correct source
    timing_data = prep.timing_data
    assert "file_timings" in timing_data
    tech_switches_entries = [f for f in timing_data["file_timings"] if f["filename"] == "tech_switches_allowed.csv"]
    assert len(tech_switches_entries) == 1
    assert tech_switches_entries[0]["source"] == "master-excel - Allowed tech switches"


@pytest.mark.django_db
def test_tech_switches_extraction_missing_sheet(core_package, geo_package, master_excel_missing_tech_switches):
    """Test that extraction fails when tech switches sheet is missing."""
    # Create DataPreparation
    prep = DataPreparation.objects.create(
        name="Test Prep without Tech Switches",
        core_data_package=core_package,
        geo_data_package=geo_package,
        master_excel=master_excel_missing_tech_switches,
    )

    service = DataPreparationService()

    # The extraction should fail with an error about missing sheet
    with (
        patch.object(
            service.service,
            "prepare_data",
            side_effect=Exception("Sheet 'Allowed tech switches' not found in Excel file"),
        ),
        patch("tempfile.mkdtemp", return_value="/tmp/test"),
        patch("shutil.move"),
    ):
        success, message = service.prepare_data(prep)

    # Refresh from database to get updated status
    prep.refresh_from_db()

    # Check that preparation failed
    assert success is False
    assert prep.status == DataPreparation.Status.FAILED
    assert "Allowed tech switches" in message
