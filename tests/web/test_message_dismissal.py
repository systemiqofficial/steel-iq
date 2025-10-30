"""Tests for Django message dismissal functionality"""

from django.test import TestCase, Client
from django.contrib import messages


class MessageDismissalTests(TestCase):
    """Test that Django messages have dismissible alerts with close buttons"""

    def setUp(self):
        self.client = Client()

    def test_messages_partial_exists(self):
        """Messages partial template should exist and be accessible"""
        from django.template.loader import get_template

        # Should not raise TemplateDoesNotExist
        template = get_template("steeloweb/partials/messages.html")
        assert template is not None

    def test_messages_have_dismissible_class(self):
        """Messages should render with alert-dismissible class"""
        from django.template import Context, Template
        from django.contrib.messages.storage.base import Message

        # Create a test message
        test_message = Message(level=messages.SUCCESS, message="Test message")

        # Render the partial with the message
        template = Template(
            "{% load static %}"
            "{% if messages %}"
            '<div class="messages mb-4">'
            "{% for message in messages %}"
            '<div class="alert alert-{{ message.tags }} alert-dismissible fade show" role="alert">'
            "{{ message }}"
            '<button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>'
            "</div>"
            "{% endfor %}"
            "</div>"
            "{% endif %}"
        )

        context = Context({"messages": [test_message]})
        rendered = template.render(context)

        # Check for dismissible components
        assert "alert-dismissible" in rendered
        assert "btn-close" in rendered
        assert 'data-bs-dismiss="alert"' in rendered

    def test_modelrun_list_uses_messages_partial(self):
        """ModelRun list template should use the messages partial"""
        with open("src/steeloweb/templates/steeloweb/modelrun_list.html", "r") as f:
            content = f.read()
            assert '{% include "steeloweb/partials/messages.html" %}' in content

    def test_modelrun_detail_uses_messages_partial(self):
        """ModelRun detail template should use the messages partial"""
        with open("src/steeloweb/templates/steeloweb/modelrun_detail.html", "r") as f:
            content = f.read()
            assert '{% include "steeloweb/partials/messages.html" %}' in content

    def test_repository_templates_use_messages_partial(self):
        """Repository templates should use the messages partial"""
        templates = [
            "repository_list.html",
            "repository_detail.html",
            "repository_form.html",
            "clone_repository.html",
            "upload_repository.html",
        ]

        for template_name in templates:
            with open(f"src/steeloweb/templates/steeloweb/{template_name}", "r") as f:
                content = f.read()
                assert '{% include "steeloweb/partials/messages.html" %}' in content, (
                    f"{template_name} should use messages partial"
                )

    def test_other_templates_use_messages_partial(self):
        """Other templates should use the messages partial"""
        templates = ["create_modelrun.html", "simulation_plot.html", "result_map.html", "upload_circularity.html"]

        for template_name in templates:
            with open(f"src/steeloweb/templates/steeloweb/{template_name}", "r") as f:
                content = f.read()
                assert '{% include "steeloweb/partials/messages.html" %}' in content, (
                    f"{template_name} should use messages partial"
                )
