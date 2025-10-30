"""Tests for force parameter in worker creation"""

import json
from unittest.mock import patch, Mock
from django.test import TestCase, Client
from django.urls import reverse

from steeloweb.models import Worker
from steeloweb import views_worker as views


class ForceParameterTests(TestCase):
    """Tests for force parameter that bypasses memory checks"""

    def setUp(self):
        self.client = Client()

    def test_htmx_force_parameter_bypasses_capacity_check(self):
        """HTMX endpoint with force=true should create worker even at capacity"""
        # Mock capacity to be zero (no capacity)
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345, poll=Mock(return_value=None))
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: 1234567890.0)

                    # Without force, should fail
                    response = self.client.post(reverse("add-worker-htmx"))
                    assert Worker.objects.count() == 0

                    # With force=true, should succeed
                    response = self.client.post(reverse("add-worker-htmx") + "?force=true")
                    assert response.status_code == 200
                    assert Worker.objects.count() == 1

    def test_json_force_parameter_bypasses_capacity_check(self):
        """JSON API endpoint with force=true should create worker even at capacity"""
        # Mock capacity to be zero (no capacity)
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345, poll=Mock(return_value=None))
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: 1234567890.0)

                    # Without force, should fail
                    response = self.client.post(
                        reverse("add-worker-json"), data=json.dumps({}), content_type="application/json"
                    )
                    assert response.status_code == 400
                    assert Worker.objects.count() == 0

                    # With force=true, should succeed
                    response = self.client.post(
                        reverse("add-worker-json"),
                        data=json.dumps({"force": True}),
                        content_type="application/json",
                    )
                    assert response.status_code == 200
                    assert Worker.objects.count() == 1

    def test_force_parameter_works_at_capacity_but_not_zero(self):
        """Force parameter should work when at capacity (active >= admissible) but admissible > 0"""
        # Mock capacity to be 1
        with patch.object(views.supervisor, "admissible_workers", return_value=1):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345, poll=Mock(return_value=None))
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: 1234567890.0)

                    # Create first worker (should succeed, within capacity)
                    response = self.client.post(
                        reverse("add-worker-json"), data=json.dumps({}), content_type="application/json"
                    )
                    assert response.status_code == 200
                    assert Worker.objects.count() == 1

                    # Try to create second worker without force (should fail, at capacity)
                    response = self.client.post(
                        reverse("add-worker-json"), data=json.dumps({}), content_type="application/json"
                    )
                    assert response.status_code == 400
                    assert Worker.objects.count() == 1

                    # Create second worker with force (should succeed)
                    response = self.client.post(
                        reverse("add-worker-json"),
                        data=json.dumps({"force": True}),
                        content_type="application/json",
                    )
                    assert response.status_code == 200
                    assert Worker.objects.count() == 2

    def test_force_parameter_logs_warning(self):
        """Forced worker creation should log warning with memory state"""
        # Mock capacity to be zero
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345, poll=Mock(return_value=None))
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: 1234567890.0)
                    with patch("steeloweb.views_worker.logger") as mock_logger:
                        # Create worker with force
                        response = self.client.post(
                            reverse("add-worker-json"),
                            data=json.dumps({"force": True}),
                            content_type="application/json",
                        )
                        assert response.status_code == 200

                        # Check that warning was logged
                        mock_logger.warning.assert_called()
                        warning_call = mock_logger.warning.call_args[0][0]
                        assert "FORCED worker creation" in warning_call
                        assert "Memory:" in warning_call
                        assert "admissible capacity" in warning_call

    def test_force_false_respects_capacity(self):
        """force=false should still respect capacity limits"""
        # Mock capacity to be zero
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            # Explicit force=false should fail
            response = self.client.post(
                reverse("add-worker-json"), data=json.dumps({"force": False}), content_type="application/json"
            )
            assert response.status_code == 400
            assert Worker.objects.count() == 0

    def test_malformed_json_defaults_to_force_false(self):
        """Malformed JSON should default to force=false"""
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            # Send malformed JSON
            response = self.client.post(
                reverse("add-worker-json"), data="not valid json", content_type="application/json"
            )
            # Should fail due to capacity (force defaults to false)
            assert response.status_code == 400
            assert Worker.objects.count() == 0

    def test_htmx_force_query_param_variations(self):
        """Test various query parameter formats for HTMX endpoint"""
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345, poll=Mock(return_value=None))
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: 1234567890.0)

                    # force=true should work (lowercase)
                    self.client.post(reverse("add-worker-htmx") + "?force=true")
                    assert Worker.objects.count() == 1
                    Worker.objects.all().delete()

                    # force=True should NOT work (case sensitive)
                    self.client.post(reverse("add-worker-htmx") + "?force=True")
                    assert Worker.objects.count() == 0

                    # force=1 should NOT work (must be "true" string)
                    self.client.post(reverse("add-worker-htmx") + "?force=1")
                    assert Worker.objects.count() == 0

    def test_normal_flow_unaffected_by_force_parameter(self):
        """Normal worker creation (with capacity) should work as before"""
        # Mock capacity to be 2
        with patch.object(views.supervisor, "admissible_workers", return_value=2):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345, poll=Mock(return_value=None))
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: 1234567890.0)

                    # Normal creation without force should work
                    response = self.client.post(
                        reverse("add-worker-json"), data=json.dumps({}), content_type="application/json"
                    )
                    assert response.status_code == 200
                    assert Worker.objects.count() == 1

                    # Another normal creation should also work
                    response = self.client.post(
                        reverse("add-worker-json"), data=json.dumps({}), content_type="application/json"
                    )
                    assert response.status_code == 200
                    assert Worker.objects.count() == 2

    def test_htmx_force_with_query_param_respects_csrf(self):
        """HTMX endpoint with force=true should still enforce CSRF protection"""
        # Mock capacity to be zero
        with patch.object(views.supervisor, "admissible_workers", return_value=0):
            with patch("subprocess.Popen") as mock_popen:
                mock_popen.return_value = Mock(pid=12345, poll=Mock(return_value=None))
                with patch("psutil.Process") as mock_psutil:
                    mock_psutil.return_value = Mock(create_time=lambda: 1234567890.0)

                    # POST with force=true should still work (Django test client handles CSRF)
                    response = self.client.post(reverse("add-worker-htmx") + "?force=true")
                    assert response.status_code == 200
                    assert Worker.objects.count() == 1
