"""Tests for the capacity policy settings on the run form."""

import pytest
from django.urls import reverse

from steeloweb.models import ModelRun

CAPACITY_POOL_FIXTURES = (
    "capacity_pool_provinces.json",
    "capacity_pool_technologies.json",
    "capacity_pool_opening_credits.json",
)


def add_capacity_pool_fixtures(data_preparation):
    """Write the three capacity pool fixtures into a preparation's ``data/fixtures``.

    Args:
        data_preparation: The ready ``DataPreparation``.
    """
    fixtures = data_preparation.get_data_path() / "data" / "fixtures"
    for name in CAPACITY_POOL_FIXTURES:
        (fixtures / name).write_text("[]")


def form_error_messages(response):
    """Every error message of the re-rendered run form, field and non-field alike.

    Args:
        response: The response to a refused post.

    Returns:
        The messages in one flat list.
    """
    return [message for messages in response.context["form"].errors.values() for message in messages]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("posted", "message"),
    [
        (
            {"capacity_policy_enabled": "on", "capacity_policy_replacement_ratio": "0.5"},
            "replacement_ratio must be at least 1, got 0.5",
        ),
        (
            {"capacity_policy_credit_validity_years": "5"},
            "Credit validity requires the capacity-replacement policy to be enabled.",
        ),
    ],
)
def test_create_form_refuses_capacity_policy_levers_the_policy_refuses(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
    ready_data_preparation,
    posted,
    message,
):
    """A lever set ``CapacityPolicyConfig`` refuses shows its message, and credit validity needs the switch on."""
    add_capacity_pool_fixtures(ready_data_preparation)

    response = client.post(reverse("create-modelrun"), data={**valid_modelrun_form_data, **posted})

    assert response.status_code == 200
    assert message in form_error_messages(response)
    assert not ModelRun.objects.exists()


@pytest.mark.django_db
def test_create_form_refuses_the_capacity_policy_on_a_preparation_without_its_fixtures(
    mock_technology_extraction,
    client,
    valid_modelrun_form_data,
):
    """The policy switch on a preparation lacking the capacity pool fixtures is a form error, not a failed run."""
    response = client.post(
        reverse("create-modelrun"),
        data={**valid_modelrun_form_data, "capacity_policy_enabled": "on"},
    )

    assert response.status_code == 200
    assert response.context["form"].has_error("capacity_policy_enabled")
    assert not ModelRun.objects.exists()
