import pytest
from django.utils import timezone
from datetime import timedelta
from steeloweb.models import ModelRun, Progress


class TestModelRun:
    pytestmark = pytest.mark.django_db

    def test_create_modelrun(self):
        """Test creating a new ModelRun instance."""
        model_run = ModelRun.objects.create()
        assert model_run.id is not None
        assert model_run.state == ModelRun.RunState.CREATED
        assert model_run.started_at is not None
        assert model_run.finished_at is None
        assert model_run.config == {}

    def test_modelrun_with_config(self):
        """Test creating a ModelRun with configuration."""
        config = {
            "num_iterations": 1000,
            "countries": ["DE", "FR", "UK"],
            "options": {"include_trade": True, "detailed_output": False},
        }

        model_run = ModelRun.objects.create(config=config)
        assert model_run.config == config
        assert model_run.config["num_iterations"] == 1000
        assert "DE" in model_run.config["countries"]
        assert model_run.config["options"]["include_trade"] is True

    def test_modelrun_state_transition(self):
        """Test changing a ModelRun's state."""
        model_run = ModelRun.objects.create()
        assert model_run.state == ModelRun.RunState.CREATED

        # Change to running
        model_run.state = ModelRun.RunState.RUNNING
        model_run.save()

        # Retrieve from DB to verify
        updated_run = ModelRun.objects.get(id=model_run.id)
        assert updated_run.state == ModelRun.RunState.RUNNING

        # Set as finished
        finish_time = timezone.now()
        updated_run.state = ModelRun.RunState.FINISHED
        updated_run.finished_at = finish_time
        updated_run.save()

        # Verify final state
        final_run = ModelRun.objects.get(id=model_run.id)
        assert final_run.state == ModelRun.RunState.FINISHED
        assert final_run.finished_at is not None

    def test_modelrun_string_representation(self):
        """Test the string representation of ModelRun."""
        model_run = ModelRun.objects.create()
        expected_str = f"ModelRun {model_run.id} - {model_run.state} ({model_run.started_at})"
        assert str(model_run) == expected_str

    def test_modelrun_ordering(self):
        """Test that ModelRuns are ordered by started_at descending."""
        # Create runs with different times
        now = timezone.now()

        # Create test runs with specific pattern to identify them
        test_config = {"test_ordering": True}

        older_run = ModelRun.objects.create(config=test_config)
        older_run.started_at = now - timedelta(hours=2)
        older_run.save()

        middle_run = ModelRun.objects.create(config=test_config)
        middle_run.started_at = now - timedelta(hours=1)
        middle_run.save()

        recent_run = ModelRun.objects.create(config=test_config)
        # recent_run uses default current time

        # Query only our test runs and check their order
        test_runs = list(ModelRun.objects.filter(config=test_config).order_by("-started_at"))

        # Verify order is newest to oldest
        assert len(test_runs) == 3
        assert test_runs[0].id == recent_run.id
        assert test_runs[1].id == middle_run.id
        assert test_runs[2].id == older_run.id

        # Double-check the timestamps are in correct order
        assert test_runs[0].started_at > test_runs[1].started_at
        assert test_runs[1].started_at > test_runs[2].started_at

    def test_is_finished(self):
        modelrun = ModelRun(state=ModelRun.RunState.FINISHED)
        assert modelrun.is_finished

        modelrun.state = "asdf"
        assert not modelrun.is_finished

    def test_is_running(self):
        modelrun = ModelRun(state=ModelRun.RunState.RUNNING)
        assert modelrun.is_running

        modelrun.state = "asdf"
        assert not modelrun.is_running

    def test_has_progress(self):
        modelrun = ModelRun()
        assert not modelrun.has_progress

        modelrun.progress = {"years": []}
        assert not modelrun.has_progress

        modelrun.progress = {"years": [{"start_year": 2025, "end_year": 2032, "current_year": 2025}]}
        assert modelrun.has_progress

    def test_current_progress(self):
        modelrun = ModelRun()
        assert modelrun.current_progress is None

        modelrun.progress = {"years": [{"start_year": 2025, "end_year": 2035, "current_year": 2026}]}
        progress = modelrun.current_progress
        assert progress.percentage_completed == 10


class TestProgress:
    def test_years(self):
        progress = Progress(start_year=2025, end_year=2035, current_year=2026)
        assert progress.years == 10

    def test_percentage_completed(self):
        progress = Progress(start_year=2025, end_year=2035, current_year=2026)
        assert progress.percentage_completed == 10

    def test_percentage_completed_single_year(self):
        progress = Progress(start_year=2025, end_year=2025, current_year=2025)
        assert progress.percentage_completed == 0

        progress = Progress(start_year=2025, end_year=2025, current_year=2026)
        assert progress.percentage_completed == 100
