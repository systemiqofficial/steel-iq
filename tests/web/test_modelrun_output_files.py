from pathlib import Path

import pytest
from django.urls import reverse

from steeloweb.models import ModelRun


@pytest.mark.django_db
def test_output_files_page_lists_the_lower_case_data_folder(client, temp_media_root):
    """The run's ``data`` folder appears as the "Data" section, with links under ``data/`` as the simulation writes it."""
    output_dir = Path(temp_media_root) / "model_outputs" / "run_1"
    (output_dir / "data").mkdir(parents=True)
    (output_dir / "data" / "x.csv").write_text("a,b\n1,2\n")
    modelrun = ModelRun.objects.create(output_directory=str(output_dir))

    response = client.get(reverse("modelrun-output-files", args=[modelrun.pk]))

    assert response.status_code == 200
    sections = {section["name"]: section for section in response.context["sections"]}
    assert [file["relative_path"] for file in sections["Data"]["files"]] == ["data/x.csv"]
