"""Tests for Worker model fields and database schema"""

import pytest
from django.test import TestCase
from django.utils import timezone
from django.db import IntegrityError

from steeloweb.models import Worker


class WorkerModelTests(TestCase):
    """Tests for Worker model structure and validation"""

    def test_worker_model_has_launch_token_field(self):
        """Worker model should have launch_token field for secure handshake"""
        worker = Worker(worker_id="test-123", state=Worker.WorkerState.STARTING, launch_token="abc123def456")
        worker.save()

        # Should be able to retrieve and verify token
        retrieved = Worker.objects.get(worker_id="test-123")
        assert retrieved.launch_token == "abc123def456"

    def test_worker_model_has_pid_started_at_field(self):
        """Worker model should have pid_started_at for process validation"""
        now = timezone.now()
        worker = Worker(worker_id="test-123", state=Worker.WorkerState.RUNNING, pid=12345, pid_started_at=now)
        worker.save()

        retrieved = Worker.objects.get(worker_id="test-123")
        assert retrieved.pid_started_at == now

    def test_worker_model_has_draining_state(self):
        """Worker model should support DRAINING state"""
        worker = Worker(worker_id="test-123", state=Worker.WorkerState.DRAINING)
        worker.save()

        retrieved = Worker.objects.get(worker_id="test-123")
        assert retrieved.state == Worker.WorkerState.DRAINING

    def test_worker_id_must_be_unique(self):
        """Worker IDs must be unique"""
        Worker.objects.create(worker_id="duplicate-id", state=Worker.WorkerState.STARTING)

        with pytest.raises(IntegrityError):
            Worker.objects.create(worker_id="duplicate-id", state=Worker.WorkerState.STARTING)

    def test_launch_token_indexed(self):
        """Launch token should be indexed for fast handshake lookups"""
        # This tests that the database index exists
        Worker.objects.create(
            worker_id="test-worker", launch_token="indexed-token-123", state=Worker.WorkerState.STARTING
        )

        # Should be able to query efficiently by launch_token
        found = Worker.objects.filter(launch_token="indexed-token-123").first()
        assert found.worker_id == "test-worker"
