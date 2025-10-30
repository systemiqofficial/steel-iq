"""Tests for worker force parameter UI elements"""

import pytest
from unittest.mock import patch, Mock
from django.urls import reverse

from steeloweb.models import Worker
from steeloweb import views_worker as views


@pytest.mark.django_db
class TestWorkerForceUIElements:
    """Tests for UI elements related to force parameter"""

    def test_worker_status_fragment_has_data_attributes(self, client):
        """Worker status fragment should include data-can-add and data-no-capacity attributes"""
        # Mock capacity to be zero
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            response = client.get(reverse("worker-status-htmx"))
            content = response.content.decode("utf-8")

            # Check for data attributes
            assert 'data-can-add="False"' in content
            assert 'data-no-capacity="True"' in content

            # Check for add-worker-btn class
            assert 'class="btn btn-sm btn-success add-worker-btn"' in content

    def test_worker_status_fragment_button_always_enabled(self, client):
        """Add Worker button should never have disabled attribute"""
        # Test with zero capacity
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            response = client.get(reverse("worker-status-htmx"))
            content = response.content.decode("utf-8")

            # Button should not be disabled
            # Check that 'disabled' doesn't appear in the Add Worker button context
            # Find the button and check it's not disabled
            assert "add-worker-btn" in content
            # The button HTML should not contain 'disabled'
            button_start = content.find('class="btn btn-sm btn-success add-worker-btn"')
            button_end = content.find("</button>", button_start)
            button_html = content[button_start:button_end]
            assert "disabled" not in button_html.lower()

    def test_worker_status_fragment_data_attributes_with_capacity(self, client):
        """Data attributes should reflect available capacity"""
        # Mock capacity to be 2 with no active workers
        with patch.object(views.supervisor, "admissible_workers", return_value=2):
            response = client.get(reverse("worker-status-htmx"))
            content = response.content.decode("utf-8")

            # Should show can add
            assert 'data-can-add="True"' in content
            assert 'data-no-capacity="False"' in content

    def test_worker_status_fragment_data_attributes_at_capacity(self, client):
        """Data attributes should show cannot add when at capacity but not zero"""
        # Create one worker
        Worker.objects.create(worker_id="test-1", state="RUNNING")

        # Mock capacity to be 1 (at capacity)
        with patch.object(views.supervisor, "admissible_workers", return_value=1):
            response = client.get(reverse("worker-status-htmx"))
            content = response.content.decode("utf-8")

            # Should show cannot add but capacity is not zero
            assert 'data-can-add="False"' in content
            assert 'data-no-capacity="False"' in content

    def test_base_template_has_memory_warning_modal(self, client):
        """Base template should include memory warning modal"""
        # Get any page that uses base template
        response = client.get(reverse("modelrun-list"))
        content = response.content.decode("utf-8")

        # Check for modal HTML
        assert 'id="memoryWarningModal"' in content
        assert "Insufficient Memory Warning" in content
        assert "Your system may not have sufficient memory" in content
        assert 'id="confirmForceWorker"' in content

    def test_base_template_has_worker_management_javascript(self, client):
        """Base template should include worker management JavaScript"""
        response = client.get(reverse("modelrun-list"))
        content = response.content.decode("utf-8")

        # Check for key JavaScript functions
        assert "getCsrfToken" in content
        assert "add-worker-btn" in content
        assert "showStartupWorkerPrompt" in content
        assert "?force=true" in content

    def test_htmx_headers_include_csrf_token(self, client):
        """Base template should set CSRF token in HTMX headers"""
        response = client.get(reverse("modelrun-list"))
        content = response.content.decode("utf-8")

        # Check for HTMX CSRF headers
        assert 'hx-headers=\'{"X-CSRFToken":' in content or 'hx-headers="{"X-CSRFToken":' in content


@pytest.mark.django_db
class TestWorkerForceEndpointIntegration:
    """Integration tests for force parameter with UI rendering"""

    def test_forced_worker_creation_updates_ui(self, client):
        """Creating a worker with force should return updated HTML"""
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345, poll=Mock(return_value=None))
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: 1234567890.0)

                    # Create worker with force
                    response = client.post(reverse("add-worker-htmx") + "?force=true")

                    # Should return HTML fragment
                    assert response.status_code == 200
                    assert "text/html" in response["Content-Type"]

                    content = response.content.decode("utf-8")

                    # Should show the new worker
                    assert "Worker" in content
                    assert "STARTING" in content or "RUNNING" in content

    def test_ui_flow_no_capacity_to_capacity(self, client):
        """Test UI transitions from no capacity to having capacity"""
        # Start with no capacity
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            response = client.get(reverse("worker-status-htmx"))
            content = response.content.decode("utf-8")
            assert 'data-can-add="False"' in content

        # Now with capacity
        with patch.object(views.supervisor, "admissible_workers", return_value=2):
            response = client.get(reverse("worker-status-htmx"))
            content = response.content.decode("utf-8")
            assert 'data-can-add="True"' in content
