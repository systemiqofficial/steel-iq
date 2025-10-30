import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from steeloweb.models import ModelRun, ResultImages


class TestResultImagesModel:
    pytestmark = pytest.mark.django_db

    def test_create_result_images(self, temp_media_root):
        """Test creating a new ResultImages instance."""
        # Create a model run first
        model_run = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create a test image file
        test_image = SimpleUploadedFile(
            name="test_image.png",
            content=b"PNG\x89\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            content_type="image/png",
        )

        # Create result images
        result_images = ResultImages.objects.create(
            modelrun=model_run,
            lcoe_map=test_image,
            lcoh_map=test_image,
            priority_locations_iron=test_image,
            priority_locations_steel=test_image,
        )

        # Check the instance was created
        assert result_images.id is not None
        assert result_images.modelrun == model_run

        # Check that the files were stored in the correct locations
        assert f"results/{model_run.id}/" in result_images.lcoe_map.name
        assert f"results/{model_run.id}/" in result_images.lcoh_map.name
        assert f"results/{model_run.id}/" in result_images.priority_locations_iron.name
        assert f"results/{model_run.id}/" in result_images.priority_locations_steel.name

        # Check the string representation
        assert str(result_images) == f"Result Images for Model Run #{model_run.id}"

    def test_result_images_with_model_run_relation(self, temp_media_root):
        """Test the related name from ModelRun to ResultImages."""
        # Create a model run first
        model_run = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create a test image file
        test_image = SimpleUploadedFile(
            name="test_image.png",
            content=b"PNG\x89\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            content_type="image/png",
        )

        # Create result images
        result_images = ResultImages.objects.create(
            modelrun=model_run,
            lcoe_map=test_image,
            lcoh_map=test_image,
            priority_locations_iron=test_image,
            priority_locations_steel=test_image,
        )

        # Check we can access the result_images through the related name
        assert hasattr(model_run, "result_images")

        # Since result_images is a related_name to a ForeignKey, it returns a manager
        result_images_from_model_run = model_run.result_images.first()
        assert result_images_from_model_run == result_images


class TestResultViews:
    pytestmark = pytest.mark.django_db

    def test_cost_map_view_lcoe(self, client, temp_media_root):
        """Test the cost map view for LCOE."""
        # Create a model run
        model_run = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create a test image file
        test_image = SimpleUploadedFile(
            name="test_image.png",
            content=b"PNG\x89\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            content_type="image/png",
        )

        # Create result images
        ResultImages.objects.create(
            modelrun=model_run,
            lcoe_map=test_image,
            lcoh_map=test_image,
            priority_locations_iron=test_image,
            priority_locations_steel=test_image,
        )

        # Test the LCOE view
        url = reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoe"})
        response = client.get(url)

        # Check response
        assert response.status_code == 200
        assert "Levelized Cost of Electricity (LCOE)" in response.content.decode("utf-8")

    def test_cost_map_view_lcoh(self, client, temp_media_root):
        """Test the cost map view for LCOH."""
        # Create a model run
        model_run = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create a test image file
        test_image = SimpleUploadedFile(
            name="test_image.png",
            content=b"PNG\x89\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            content_type="image/png",
        )

        # Create result images
        ResultImages.objects.create(
            modelrun=model_run,
            lcoe_map=test_image,
            lcoh_map=test_image,
            priority_locations_iron=test_image,
            priority_locations_steel=test_image,
        )

        # Test the LCOH view
        url = reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoh"})
        response = client.get(url)

        # Check response
        assert response.status_code == 200
        assert "Levelized Cost of Hydrogen (LCOH)" in response.content.decode("utf-8")

    def test_priority_map_view_iron(self, client, temp_media_root):
        """Test the priority map view for iron production."""
        # Create a model run
        model_run = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create a test image file
        test_image = SimpleUploadedFile(
            name="test_image.png",
            content=b"PNG\x89\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            content_type="image/png",
        )

        # Create result images
        ResultImages.objects.create(
            modelrun=model_run,
            lcoe_map=test_image,
            lcoh_map=test_image,
            priority_locations_iron=test_image,
            priority_locations_steel=test_image,
        )

        # Test the iron priority view
        url = reverse("view-priority-map", kwargs={"pk": model_run.id, "map_type": "iron"})
        response = client.get(url)

        # Check response
        assert response.status_code == 200
        assert "Top 5% Priority Locations for Iron Production" in response.content.decode("utf-8")

    def test_priority_map_view_steel(self, client, temp_media_root):
        """Test the priority map view for steel production."""
        # Create a model run
        model_run = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create a test image file
        test_image = SimpleUploadedFile(
            name="test_image.png",
            content=b"PNG\x89\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            content_type="image/png",
        )

        # Create result images
        ResultImages.objects.create(
            modelrun=model_run,
            lcoe_map=test_image,
            lcoh_map=test_image,
            priority_locations_iron=test_image,
            priority_locations_steel=test_image,
        )

        # Test the steel priority view
        url = reverse("view-priority-map", kwargs={"pk": model_run.id, "map_type": "steel"})
        response = client.get(url)

        # Check response
        assert response.status_code == 200
        assert "Top 5% Priority Locations for Steel Production" in response.content.decode("utf-8")

    def test_invalid_map_type(self, client, temp_media_root):
        """Test providing an invalid map type."""
        # Create a model run
        model_run = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Create a test image file
        test_image = SimpleUploadedFile(
            name="test_image.png",
            content=b"PNG\x89\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            content_type="image/png",
        )

        # Create result images
        ResultImages.objects.create(
            modelrun=model_run,
            lcoe_map=test_image,
            lcoh_map=test_image,
            priority_locations_iron=test_image,
            priority_locations_steel=test_image,
        )

        # Test an invalid map type
        url = reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "invalid"})
        response = client.get(url)

        # Should return 404
        assert response.status_code == 404

    def test_no_result_images(self, client):
        """Test accessing views when no result images exist."""
        # Create a model run with no images
        model_run = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)

        # Test the LCOE view
        url = reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoe"})
        response = client.get(url)

        # Should redirect to model run detail with an error message
        assert response.status_code == 302
        assert response.url == reverse("modelrun-detail", kwargs={"pk": model_run.id})


class TestModelRunDetailWithImages:
    pytestmark = pytest.mark.django_db

    def test_modelrun_detail_with_result_images(self, client, temp_media_root):
        """Test that the model run detail page shows result links when images exist."""
        # Create a model run with some results
        model_run = ModelRun.objects.create(
            state=ModelRun.RunState.FINISHED,
            results={"price": {"2025": {"Steel": 500, "Iron": 300}}, "capacity": {"2025": {"Steel": 100, "Iron": 200}}},
        )

        # Create a test image file
        test_image = SimpleUploadedFile(
            name="test_image.png",
            content=b"PNG\x89\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82",
            content_type="image/png",
        )

        # Create result images
        ResultImages.objects.create(
            modelrun=model_run,
            lcoe_map=test_image,
            lcoh_map=test_image,
            priority_locations_iron=test_image,
            priority_locations_steel=test_image,
        )

        # Get the model run detail page
        url = reverse("modelrun-detail", kwargs={"pk": model_run.id})
        response = client.get(url)

        # Check that the visualization section exists and contains the right links
        content = response.content.decode("utf-8")
        assert "Simulation Results" in content

        # Check for links to the image views
        assert f'href="{reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoe"})}"' in content
        assert f'href="{reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoh"})}"' in content
        assert f'href="{reverse("view-priority-map", kwargs={"pk": model_run.id, "map_type": "iron"})}"' in content
        assert f'href="{reverse("view-priority-map", kwargs={"pk": model_run.id, "map_type": "steel"})}"' in content

        # Check for text descriptions
        assert "Levelized Cost of Electricity (LCOE)" in content
        assert "Levelized Cost of Hydrogen (LCOH)" in content
        assert "Top 5% Locations for Iron Production" in content
        assert "Top 5% Locations for Steel Production" in content

    def test_modelrun_detail_without_result_images(self, client):
        """Test that the model run detail page doesn't show result links when no images exist."""
        # Create a model run with no images but with results
        model_run = ModelRun.objects.create(
            state=ModelRun.RunState.FINISHED,
            results={"price": {"2025": {"Steel": 500, "Iron": 300}}, "capacity": {"2025": {"Steel": 100, "Iron": 200}}},
        )

        # Get the model run detail page
        url = reverse("modelrun-detail", kwargs={"pk": model_run.id})
        response = client.get(url)

        # Check that the visualization section doesn't exist
        content = response.content.decode("utf-8")
        # The page should show the results section
        assert "Simulation Results" in content
        # But the section for visualization links shouldn't be there
        assert "Result Maps &amp; Visualizations" not in content
        assert "Levelized Cost of Electricity (LCOE)" not in content
