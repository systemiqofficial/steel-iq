"""Tests for MasterExcelReader technology extraction functionality."""

import pandas as pd
from pathlib import Path
import tempfile
import json
from unittest.mock import patch

from steelo.adapters.dataprocessing.master_excel_reader import MasterExcelReader


class TestMasterExcelReaderTechnologies:
    """Test technology extraction in MasterExcelReader."""

    def test_read_technologies_config_success(self):
        """Test successful technology configuration extraction."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test Excel file
            test_data = {
                "Technology": ["BF", "EAF", "DRI"],
                "Name in the Dashboard": ["Blast Furnace", "Electric Arc Furnace", "Direct Reduction"],
                "Product": ["iron", "steel", "iron"],
            }
            df = pd.DataFrame(test_data)

            excel_path = Path(tmpdir) / "test.xlsx"
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Techno-economic details", index=False)

            output_dir = Path(tmpdir) / "output"
            reader = MasterExcelReader(excel_path, output_dir)

            result = reader.read_technologies_config()

            assert result.success is True
            assert result.file_path is not None
            assert result.file_path.exists()
            assert result.file_path.name == "technologies.json"

            # Check the expected path structure
            expected_path = output_dir / "data" / "fixtures" / "technologies.json"
            assert result.file_path == expected_path

            # Verify JSON content
            with open(result.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                assert "technologies" in data
                assert "bf" in data["technologies"]
                assert "eaf" in data["technologies"]
                assert "dri" in data["technologies"]
                assert data["technologies"]["bf"]["display_name"] == "Blast Furnace"

    def test_read_technologies_config_missing_sheet(self):
        """Test extraction when the required sheet is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Excel file without the required sheet
            test_data = {"dummy": ["data"]}
            df = pd.DataFrame(test_data)

            excel_path = Path(tmpdir) / "test.xlsx"
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Wrong Sheet", index=False)

            output_dir = Path(tmpdir) / "output"
            reader = MasterExcelReader(excel_path, output_dir)

            result = reader.read_technologies_config()

            assert result.success is False
            assert result.file_path is None
            assert len(result.errors) == 1
            assert result.errors[0].error_type == "MISSING_SHEET"
            assert "Techno-economic details" in result.errors[0].message

    def test_read_technologies_config_missing_columns(self):
        """Test extraction when required columns are missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Excel file with wrong columns
            test_data = {"WrongColumn": ["data"]}
            df = pd.DataFrame(test_data)

            excel_path = Path(tmpdir) / "test.xlsx"
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Techno-economic details", index=False)

            output_dir = Path(tmpdir) / "output"
            reader = MasterExcelReader(excel_path, output_dir)

            result = reader.read_technologies_config()

            assert result.success is False
            assert result.file_path is None
            assert len(result.errors) == 1
            assert result.errors[0].error_type == "MISSING_COLUMNS"
            assert "Technology" in result.errors[0].message

    def test_read_technologies_config_with_duplicates(self):
        """Test extraction handles duplicate technology codes correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test Excel file with duplicate codes
            test_data = {
                "Technology": ["BF", "B.F.", "bf", "EAF"],  # Three variations of BF
                "Name in the Dashboard": ["Blast 1", "Blast 2", "Blast 3", "Electric Arc"],
                "Product": ["iron", "iron", "iron", "steel"],
            }
            df = pd.DataFrame(test_data)

            excel_path = Path(tmpdir) / "test.xlsx"
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Techno-economic details", index=False)

            output_dir = Path(tmpdir) / "output"
            reader = MasterExcelReader(excel_path, output_dir)

            result = reader.read_technologies_config()

            assert result.success is True
            assert result.file_path is not None

            # Verify deduplication worked
            with open(result.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Should only have 2 technologies (one BF, one EAF)
                assert len(data["technologies"]) == 2
                assert "bf" in data["technologies"]
                assert "eaf" in data["technologies"]
                # Check duplicates are tracked
                assert data["source"]["duplicates"] is not None
                assert len(data["source"]["duplicates"]) == 2

    def test_read_technologies_config_extraction_error(self):
        """Test handling of extraction errors."""
        with tempfile.TemporaryDirectory() as tmpdir:
            excel_path = Path(tmpdir) / "test.xlsx"
            output_dir = Path(tmpdir) / "output"

            reader = MasterExcelReader(excel_path, output_dir)

            # Mock the excel file to raise an exception
            with patch.object(reader, "_excel_file", None):
                with patch("pandas.ExcelFile", side_effect=Exception("Test error")):
                    result = reader.read_technologies_config()

            assert result.success is False
            assert result.file_path is None
            assert len(result.errors) == 1
            assert result.errors[0].error_type == "EXTRACTION_ERROR"
            assert "Test error" in result.errors[0].message

    def test_read_technologies_config_context_manager(self):
        """Test that read_technologies_config works with context manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test Excel file
            test_data = {"Technology": ["BF"], "Name in the Dashboard": ["Blast Furnace"], "Product": ["iron"]}
            df = pd.DataFrame(test_data)

            excel_path = Path(tmpdir) / "test.xlsx"
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Techno-economic details", index=False)

            output_dir = Path(tmpdir) / "output"

            # Use context manager
            with MasterExcelReader(excel_path, output_dir) as reader:
                result = reader.read_technologies_config()

            assert result.success is True
            assert result.file_path is not None
            assert result.file_path.exists()

    def test_read_technologies_config_no_display_name_column(self):
        """Test extraction when display name column is missing (uses fallback)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test Excel file without display name column
            test_data = {"Technology": ["BF", "EAF"], "Product": ["iron", "steel"]}
            df = pd.DataFrame(test_data)

            excel_path = Path(tmpdir) / "test.xlsx"
            with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="Techno-economic details", index=False)

            output_dir = Path(tmpdir) / "output"
            reader = MasterExcelReader(excel_path, output_dir)

            result = reader.read_technologies_config()

            assert result.success is True
            assert result.file_path is not None

            # Verify fallback to technology codes as display names
            with open(result.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                assert data["technologies"]["bf"]["display_name"] == "BF"
                assert data["technologies"]["eaf"]["display_name"] == "EAF"
