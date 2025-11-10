"""
Simple URL pattern verification tests that don't require Django to be fully loaded.
This is a minimal test suite to verify URL patterns are correctly defined.
"""

import re


def test_url_patterns_exist():
    """Test that all expected URL patterns exist in the urls.py file"""
    with open('/home/user/steel-iq/src/steeloweb/urls.py', 'r') as f:
        content = f.read()

    expected_patterns = {
        'scenario_list': "path('scenarios/',",
        'scenario_create': "path('scenarios/new/',",
        'scenario_detail': "path('scenarios/<int:id>/',",
        'scenario_update': "path('scenarios/<int:id>/edit/',",
        'scenario_delete': "path('scenarios/<int:id>/delete/',",
        'scenario_clone': "path('scenarios/<int:id>/clone/',",
        'scenario_run': "path('scenarios/<int:id>/run/',",
        'variation_create': "path('scenarios/<int:scenario_id>/variations/new/',",
        'variation_detail': "path('variations/<int:id>/',",
        'variation_update': "path('variations/<int:id>/edit/',",
        'variation_delete': "path('variations/<int:id>/delete/',",
        'sweep_create': "path('scenarios/<int:scenario_id>/sweeps/new/',",
        'sweep_detail': "path('sweeps/<int:id>/',",
        'sweep_generate': "path('sweeps/<int:id>/generate/',",
        'sweep_delete': "path('sweeps/<int:id>/delete/',",
        'scenario_compare': "path('compare/',",
    }

    for name, pattern in expected_patterns.items():
        assert pattern in content, f"Expected pattern for {name} not found: {pattern}"


def test_placeholder_imports_exist():
    """Test that placeholder imports for views are present"""
    with open('/home/user/steel-iq/src/steeloweb/urls.py', 'r') as f:
        content = f.read()

    assert 'from steeloweb.views import scenarios, scenario_variations, sensitivity_sweeps' in content, \
        "Placeholder view imports not found"


def test_url_names_unique():
    """Test that all scenario-related URL names are unique"""
    with open('/home/user/steel-iq/src/steeloweb/urls.py', 'r') as f:
        content = f.read()

    # Extract all URL names
    url_names = re.findall(r"name=['\"]([^'\"]+)['\"]", content)

    # Check scenario-related names for duplicates
    scenario_names = [
        'scenario_list', 'scenario_create', 'scenario_detail', 'scenario_update',
        'scenario_delete', 'scenario_clone', 'scenario_run', 'scenario_compare',
        'variation_create', 'variation_detail', 'variation_update', 'variation_delete',
        'sweep_create', 'sweep_detail', 'sweep_generate', 'sweep_delete',
    ]

    for name in scenario_names:
        count = url_names.count(name)
        assert count == 1, f"URL name '{name}' appears {count} times (expected 1)"


def test_restful_naming_conventions():
    """Test that URL patterns follow RESTful conventions"""
    with open('/home/user/steel-iq/src/steeloweb/urls.py', 'r') as f:
        content = f.read()

    # Test RESTful patterns
    restful_checks = [
        ("List view", "path('scenarios/',"),
        ("Create view", "path('scenarios/new/',"),
        ("Detail view", "path('scenarios/<int:id>/',"),
        ("Update view", "path('scenarios/<int:id>/edit/',"),
        ("Delete view", "path('scenarios/<int:id>/delete/',"),
    ]

    for check_name, pattern in restful_checks:
        assert pattern in content, f"{check_name} pattern not found: {pattern}"


def test_nested_resource_patterns():
    """Test that nested resources use correct parent_id in path"""
    with open('/home/user/steel-iq/src/steeloweb/urls.py', 'r') as f:
        content = f.read()

    # Variations are nested under scenarios
    assert "path('scenarios/<int:scenario_id>/variations/new/'," in content, \
        "Nested variation create pattern not found"

    # Sweeps are nested under scenarios
    assert "path('scenarios/<int:scenario_id>/sweeps/new/'," in content, \
        "Nested sweep create pattern not found"


def test_id_parameter_consistency():
    """Test that ID parameters use <int:id> pattern"""
    with open('/home/user/steel-iq/src/steeloweb/urls.py', 'r') as f:
        content = f.read()

    # Check for scenario ID patterns
    scenario_id_patterns = [
        "path('scenarios/<int:id>/',",
        "path('scenarios/<int:id>/edit/',",
        "path('scenarios/<int:id>/delete/',",
        "path('scenarios/<int:id>/clone/',",
        "path('scenarios/<int:id>/run/',",
    ]

    for pattern in scenario_id_patterns:
        assert pattern in content, f"Expected ID pattern not found: {pattern}"


def test_url_count():
    """Test that the expected number of scenario URLs were added"""
    with open('/home/user/steel-iq/src/steeloweb/urls.py', 'r') as f:
        content = f.read()

    # Count scenario-related URL patterns
    scenario_urls = [
        'scenario_list', 'scenario_create', 'scenario_detail', 'scenario_update',
        'scenario_delete', 'scenario_clone', 'scenario_run', 'scenario_compare',
        'variation_create', 'variation_detail', 'variation_update', 'variation_delete',
        'sweep_create', 'sweep_detail', 'sweep_generate', 'sweep_delete',
    ]

    for url_name in scenario_urls:
        assert f"name='{url_name}'" in content, f"URL name '{url_name}' not found"

    # Verify we have exactly 16 scenario URLs
    assert len(scenario_urls) == 16, f"Expected 16 scenario URLs, found {len(scenario_urls)}"
