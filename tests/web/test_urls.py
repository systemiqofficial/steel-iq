import pytest
from django.urls import reverse, resolve


@pytest.mark.parametrize(
    "view_name,kwargs,expected_url",
    [
        ("view-cost-map", {"pk": 1, "map_type": "lcoe"}, "/modelrun/1/cost/lcoe/"),
        ("view-cost-map", {"pk": 1, "map_type": "lcoh"}, "/modelrun/1/cost/lcoh/"),
        ("view-priority-map", {"pk": 1, "map_type": "iron"}, "/modelrun/1/priority/iron/"),
        ("view-priority-map", {"pk": 1, "map_type": "steel"}, "/modelrun/1/priority/steel/"),
    ],
)
def test_result_image_urls(view_name, kwargs, expected_url):
    """Test that URL names resolve to the correct URL patterns"""
    url = reverse(view_name, kwargs=kwargs)
    assert url == expected_url


@pytest.mark.parametrize(
    "url,expected_view_name",
    [
        ("/modelrun/1/cost/lcoe/", "view-cost-map"),
        ("/modelrun/1/cost/lcoh/", "view-cost-map"),
        ("/modelrun/1/priority/iron/", "view-priority-map"),
        ("/modelrun/1/priority/steel/", "view-priority-map"),
    ],
)
def test_result_image_url_resolution(url, expected_view_name):
    """Test that URLs resolve to the correct view names"""
    resolved = resolve(url)
    assert resolved.view_name == expected_view_name


@pytest.mark.parametrize(
    "url,expected_kwargs",
    [
        ("/modelrun/1/cost/lcoe/", {"pk": 1, "map_type": "lcoe"}),
        ("/modelrun/1/cost/lcoh/", {"pk": 1, "map_type": "lcoh"}),
        ("/modelrun/1/priority/iron/", {"pk": 1, "map_type": "iron"}),
        ("/modelrun/1/priority/steel/", {"pk": 1, "map_type": "steel"}),
    ],
)
def test_result_image_url_kwargs(url, expected_kwargs):
    """Test that URLs pass the correct kwargs to the views"""
    resolved = resolve(url)
    assert resolved.kwargs == expected_kwargs
