import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from steeloweb.models import ModelRun, SimulationPlot


@pytest.mark.django_db
def test_capture_takes_capacity_pool_charts_as_capacity_policy_plots(tmp_path):
    """The ``plots/capacity_pool/`` charts become capacity policy plots, titled by file name, with their product."""
    modelrun = ModelRun.objects.create(state=ModelRun.RunState.FINISHED)
    pam_plots_dir = tmp_path / "plots" / "PAM"
    pam_plots_dir.mkdir(parents=True)
    pool_dir = tmp_path / "plots" / "capacity_pool"
    pool_dir.mkdir()
    for name in (
        "steel_pool_nested.png",
        "steel_capacity_refused.png",
        "iron_pool_area.png",
        "iron_capacity_flows.png",
        "technology_mix.png",
    ):
        (pool_dir / name).write_bytes(b"fake png content")
    (pool_dir / "technology_mix.csv").write_text("year\n2025\n")

    SimulationPlot.capture_simulation_plots(modelrun, pam_plots_dir=pam_plots_dir)

    assert modelrun.simulation_plots.count() == 5
    plots = modelrun.simulation_plots.filter(plot_type=SimulationPlot.PlotType.CAPACITY_POLICY)
    assert sorted(plots.values_list("title", "product_type")) == [
        ("Iron Capacity Flows", "iron"),
        ("Iron Pool Area", "iron"),
        ("Steel Capacity Refused", "steel"),
        ("Steel Pool Nested", "steel"),
        ("Technology Mix", ""),
    ]


@pytest.mark.django_db
def test_run_page_shows_the_capacity_policy_accordion_only_with_its_plots(client):
    """A run with capacity policy plots gets the accordion, linking each chart; a run without gets none.

    Neither page has the removed "Result Maps & Visualizations" section.
    """

    def finished_run_with_plot(plot_type: str) -> tuple[ModelRun, SimulationPlot]:
        modelrun = ModelRun.objects.create(
            state=ModelRun.RunState.FINISHED,
            results={"price": {"2025": {"Steel": 500, "Iron": 300}}},
        )
        plot = SimulationPlot.objects.create(
            modelrun=modelrun,
            plot_type=plot_type,
            title="Chart",
            image=ContentFile(b"png", name="chart.png"),
        )
        return modelrun, plot

    policy_run, policy_plot = finished_run_with_plot(SimulationPlot.PlotType.CAPACITY_POLICY)
    other_run, _ = finished_run_with_plot(SimulationPlot.PlotType.MARKET_PRICES)

    policy_page = client.get(reverse("modelrun-detail", kwargs={"pk": policy_run.pk})).content.decode()
    other_page = client.get(reverse("modelrun-detail", kwargs={"pk": other_run.pk})).content.decode()

    plot_url = reverse("view-simulation-plot", kwargs={"pk": policy_run.pk, "plot_id": policy_plot.pk})
    assert 'id="capacityPolicyAccordion"' in policy_page
    assert "Capacity Policy (1)" in policy_page
    assert f'href="{plot_url}?popup=1"' in policy_page
    assert 'id="capacityPolicyAccordion"' not in other_page
    assert "Result Maps" not in policy_page
    assert "Result Maps" not in other_page
