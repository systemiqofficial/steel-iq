import pytest

from django.urls import reverse

from steeloweb.models import ModelRun


@pytest.mark.django_db
def test_modelrun_progres(client):
    # given a running simulation with no progress yet
    modelrun = ModelRun.objects.create(state=ModelRun.RunState.RUNNING)
    url = reverse("modelrun-progress", kwargs={"pk": modelrun.pk})

    # when we get the url
    r = client.get(url)
    assert r.status_code == 200

    # then we we see no progress bar yet
    content = r.content.decode("utf-8")
    assert "Simulation is starting up" in content

    # when we update the progress to just have completed 1 of 10 years and refetch the url
    modelrun.progress = {"years": [{"start_year": 2025, "end_year": 2035, "current_year": 2026}]}
    modelrun.save()
    r = client.get(url)
    assert r.status_code == 200

    # then we see the progress bar 10% completed
    content = r.content.decode("utf-8")
    assert "progressbar" in content
    assert "width: 10%;" in content  # 10% progress

    # when the simulation has finished and we refetch the url
    modelrun.state = modelrun.RunState.FINISHED
    modelrun.save()
    r = client.get(url)
    assert r.status_code == 200

    # then the hx-refresh header should have been set to trigger a full page refresh and stop polling
    assert r.headers["HX-Refresh"] == "true"
