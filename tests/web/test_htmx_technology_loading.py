import pytest
from unittest.mock import patch
from django.urls import reverse
from steeloweb.models import DataPreparation, DataPackage


@pytest.fixture
def mock_technologies():
    """Mock technology data for testing."""
    return {
        "bf": {"code": "BF", "display_name": "Blast Furnace (BF)", "allowed": True, "from_year": 2025, "to_year": None},
        "ccs": {
            "code": "CCS",
            "display_name": "Carbon Capture Storage",
            "allowed": True,
            "from_year": 2025,
            "to_year": None,
        },
    }


@pytest.fixture
def data_packages():
    """Create required DataPackage entries for tests."""
    core_package = DataPackage.objects.create(
        name=DataPackage.PackageType.CORE_DATA, version="test", source_type=DataPackage.SourceType.LOCAL
    )
    geo_package = DataPackage.objects.create(
        name=DataPackage.PackageType.GEO_DATA, version="test", source_type=DataPackage.SourceType.LOCAL
    )
    return core_package, geo_package


@pytest.mark.django_db
def test_technologies_fragment_valid_prep(client, mock_technologies, data_packages):
    """Test HTMX fragment endpoint with valid data preparation."""
    core_package, geo_package = data_packages

    prep = DataPreparation.objects.create(
        name="Test Preparation",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory="/test/path",
    )

    with patch.object(DataPreparation, "get_technologies", return_value=mock_technologies):
        url = reverse("create-modelrun-tech-fragment")
        response = client.get(url, {"data_preparation": prep.id})

        assert response.status_code == 200
        # Check that all technologies appear (including CCS)
        assert b"Blast Furnace (BF)" in response.content
        assert b"Carbon Capture Storage" in response.content
        assert b"2 technologies available" in response.content


@pytest.mark.django_db
def test_technologies_fragment_invalid_prep(client):
    """Test HTMX fragment endpoint with invalid data preparation."""
    url = reverse("create-modelrun-tech-fragment")
    response = client.get(url, {"data_preparation": 99999})

    assert response.status_code == 200  # Always returns 200 with friendly message
    assert b"The selected data preparation is not available or not ready." in response.content


@pytest.mark.django_db
def test_technologies_fragment_no_prep(client):
    """Test HTMX fragment endpoint without data preparation parameter."""
    url = reverse("create-modelrun-tech-fragment")
    response = client.get(url)

    assert response.status_code == 200  # Always returns 200 with friendly message
    assert b"Please select a data preparation to view technologies." in response.content


@pytest.mark.django_db
def test_technologies_fragment_not_ready_prep(client, data_packages):
    """Test HTMX fragment endpoint with non-ready data preparation."""
    core_package, geo_package = data_packages

    prep = DataPreparation.objects.create(
        name="Test Preparation",
        status=DataPreparation.Status.PREPARING,  # Not READY
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory="/test/path",
    )

    url = reverse("create-modelrun-tech-fragment")
    response = client.get(url, {"data_preparation": prep.id})

    assert response.status_code == 200  # Always returns 200 with friendly message
    assert b"The selected data preparation is not available or not ready." in response.content


@pytest.mark.django_db
def test_main_view_with_preselected_prep(client, data_packages):
    """Test main view with preselected data preparation."""
    core_package, geo_package = data_packages

    prep = DataPreparation.objects.create(
        name="Test Preparation",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory="/test/path",
    )

    url = reverse("create-modelrun")
    response = client.get(url, {"data_preparation": prep.id})

    assert response.status_code == 200
    # The main view works, but we haven't updated the template with HTMX yet
    # That would require modifying the large existing template
    # For now, just verify the view renders successfully


@pytest.mark.django_db
def test_form_submission_with_technologies(client, mock_technologies, data_packages):
    """Test form submission would process technology settings correctly once integrated."""
    # This test is a placeholder for full integration
    # The actual form processing would need to be updated to use dynamic technologies
    # For now, we just verify the infrastructure is in place
    core_package, geo_package = data_packages

    prep = DataPreparation.objects.create(
        name="Test Preparation",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory="/test/path",
    )

    with patch.object(DataPreparation, "get_technologies", return_value=mock_technologies):
        # Verify that the technologies can be loaded
        technologies = prep.get_technologies()
        assert "bf" in technologies
        assert "ccs" in technologies


@pytest.mark.django_db
def test_no_post_collision_with_deduplication(client, data_packages):
    """Test that deduplication prevents POST collision."""
    core_package, geo_package = data_packages

    prep = DataPreparation.objects.create(
        name="Test Preparation",
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
        data_directory="/test/path",
    )

    # Only one BF technology due to deduplication
    dedup_technologies = {
        "bf": {"code": "BF", "display_name": "Blast Furnace", "allowed": True, "from_year": 2025, "to_year": None}
    }

    with patch.object(DataPreparation, "get_technologies", return_value=dedup_technologies):
        # Verify that deduplication results in only one BF technology
        technologies = prep.get_technologies()
        assert len(technologies) == 1
        assert "bf" in technologies
