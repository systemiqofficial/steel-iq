import pytest
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from steeloweb.models import ModelRun, ResultImages


@pytest.mark.django_db
class TestResultMapTemplate:
    def test_result_map_template_rendering(self, client, temp_media_root):
        """Test that the result_map.html template renders correctly."""
        # Create a model run
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

        # Test each map type
        map_types = {
            "lcoe": "Levelized Cost of Electricity (LCOE)",
            "lcoh": "Levelized Cost of Hydrogen (LCOH)",
        }

        for map_type, title in map_types.items():
            # Get the cost map view
            url = reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": map_type})
            response = client.get(url)

            # Check response
            assert response.status_code == 200
            content = response.content.decode("utf-8")

            # Check for key elements in the template
            assert f"<title>{title} - Model Run #{model_run.id}</title>" in content
            assert f"<h1>{title}</h1>" in content
            assert "Back to Model Run" in content
            assert f'<img src="{getattr(result_images, f"{map_type}_map").url}"' in content
            assert f"Results from model run #{model_run.id}" in content

    def test_modelrun_detail_template_with_result_images(self, client, temp_media_root):
        """Test that the modelrun_detail.html template includes result image links when available."""
        # Create a model run with results
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

        # Check the template includes the result image links section
        content = response.content.decode("utf-8")

        # Check for key elements related to result images
        assert "Simulation Results" in content

        # Check for links to the image views
        assert f'href="{reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoe"})}"' in content
        assert f'href="{reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoh"})}"' in content
        assert f'href="{reverse("view-priority-map", kwargs={"pk": model_run.id, "map_type": "iron"})}"' in content
        assert f'href="{reverse("view-priority-map", kwargs={"pk": model_run.id, "map_type": "steel"})}"' in content

        # Check for the cost map links
        lcoe_url = reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoe"})
        lcoh_url = reverse("view-cost-map", kwargs={"pk": model_run.id, "map_type": "lcoh"})
        assert f'href="{lcoe_url}"' in content
        assert f'href="{lcoh_url}"' in content
        assert "Levelized Cost of Electricity (LCOE)" in content
        assert "Levelized Cost of Hydrogen (LCOH)" in content

        # Check for the priority map links
        iron_url = reverse("view-priority-map", kwargs={"pk": model_run.id, "map_type": "iron"})
        steel_url = reverse("view-priority-map", kwargs={"pk": model_run.id, "map_type": "steel"})
        assert f'href="{iron_url}"' in content
        assert f'href="{steel_url}"' in content
        assert "Top 5% Locations for Iron Production" in content
        assert "Top 5% Locations for Steel Production" in content
