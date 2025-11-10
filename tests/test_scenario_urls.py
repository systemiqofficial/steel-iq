"""
Tests for scenario management URL routing.

Following DHH principles: test URL resolution and naming conventions.
"""

import pytest
from django.urls import reverse, resolve


class TestScenarioURLsResolve:
    """Test that all scenario URLs resolve correctly to expected paths."""

    def test_scenario_list_url(self):
        """Test scenario list URL"""
        assert reverse('scenario_list') == '/scenarios/'

    def test_scenario_create_url(self):
        """Test scenario create URL"""
        assert reverse('scenario_create') == '/scenarios/new/'

    def test_scenario_detail_url(self):
        """Test scenario detail URL with ID"""
        assert reverse('scenario_detail', args=[1]) == '/scenarios/1/'
        assert reverse('scenario_detail', args=[42]) == '/scenarios/42/'

    def test_scenario_update_url(self):
        """Test scenario update URL"""
        assert reverse('scenario_update', args=[1]) == '/scenarios/1/edit/'

    def test_scenario_delete_url(self):
        """Test scenario delete URL"""
        assert reverse('scenario_delete', args=[1]) == '/scenarios/1/delete/'

    def test_scenario_clone_url(self):
        """Test scenario clone URL"""
        assert reverse('scenario_clone', args=[1]) == '/scenarios/1/clone/'

    def test_scenario_run_url(self):
        """Test scenario run URL"""
        assert reverse('scenario_run', args=[1]) == '/scenarios/1/run/'

    def test_scenario_compare_url(self):
        """Test scenario comparison URL"""
        assert reverse('scenario_compare') == '/compare/'


class TestVariationURLsResolve:
    """Test that all scenario variation URLs resolve correctly."""

    def test_variation_create_url(self):
        """Test variation create URL (nested under scenario)"""
        assert reverse('variation_create', args=[1]) == '/scenarios/1/variations/new/'

    def test_variation_detail_url(self):
        """Test variation detail URL"""
        assert reverse('variation_detail', args=[1]) == '/variations/1/'
        assert reverse('variation_detail', args=[99]) == '/variations/99/'

    def test_variation_update_url(self):
        """Test variation update URL"""
        assert reverse('variation_update', args=[1]) == '/variations/1/edit/'

    def test_variation_delete_url(self):
        """Test variation delete URL"""
        assert reverse('variation_delete', args=[1]) == '/variations/1/delete/'


class TestSensitivitySweepURLsResolve:
    """Test that all sensitivity sweep URLs resolve correctly."""

    def test_sweep_create_url(self):
        """Test sweep create URL (nested under scenario)"""
        assert reverse('sweep_create', args=[1]) == '/scenarios/1/sweeps/new/'

    def test_sweep_detail_url(self):
        """Test sweep detail URL"""
        assert reverse('sweep_detail', args=[1]) == '/sweeps/1/'

    def test_sweep_generate_url(self):
        """Test sweep generate runs URL"""
        assert reverse('sweep_generate', args=[1]) == '/sweeps/1/generate/'

    def test_sweep_delete_url(self):
        """Test sweep delete URL"""
        assert reverse('sweep_delete', args=[1]) == '/sweeps/1/delete/'


class TestURLNamingConventions:
    """Test URL naming conventions and uniqueness."""

    def test_all_scenario_urls_unique(self):
        """Ensure all URL names are unique and resolvable"""
        url_names = [
            'scenario_list',
            'scenario_create',
            'scenario_detail',
            'scenario_update',
            'scenario_delete',
            'scenario_clone',
            'scenario_run',
            'scenario_compare',
            'variation_create',
            'variation_detail',
            'variation_update',
            'variation_delete',
            'sweep_create',
            'sweep_detail',
            'sweep_generate',
            'sweep_delete',
        ]

        # All names should be unique
        assert len(url_names) == len(set(url_names)), "Duplicate URL names found"

        # All names should resolve without error
        for name in url_names:
            if name in ['scenario_list', 'scenario_create', 'scenario_compare']:
                # These don't need args
                reverse(name)
            elif name in ['variation_create', 'sweep_create']:
                # These need scenario_id
                reverse(name, args=[1])
            else:
                # These need id
                reverse(name, args=[1])

    def test_restful_naming_pattern(self):
        """Test that URL names follow RESTful conventions"""
        # List views should not have a suffix beyond the resource name
        assert reverse('scenario_list') == '/scenarios/'

        # Detail views use bare resource path
        assert reverse('scenario_detail', args=[1]) == '/scenarios/1/'

        # Create uses '/new/' convention
        assert reverse('scenario_create') == '/scenarios/new/'

        # Update uses '/edit/' convention
        assert reverse('scenario_update', args=[1]) == '/scenarios/1/edit/'

        # Delete uses '/delete/' convention
        assert reverse('scenario_delete', args=[1]) == '/scenarios/1/delete/'


class TestURLParameterPatterns:
    """Test that URL parameters use consistent patterns."""

    def test_id_parameter_pattern(self):
        """Test that ID parameters work with different values"""
        # Test various ID values
        for test_id in [1, 10, 100, 999, 12345]:
            assert reverse('scenario_detail', args=[test_id]) == f'/scenarios/{test_id}/'
            assert reverse('variation_detail', args=[test_id]) == f'/variations/{test_id}/'
            assert reverse('sweep_detail', args=[test_id]) == f'/sweeps/{test_id}/'

    def test_nested_resource_pattern(self):
        """Test that nested resources use parent_id in path"""
        # Variations are nested under scenarios
        assert reverse('variation_create', args=[5]) == '/scenarios/5/variations/new/'

        # Sweeps are nested under scenarios
        assert reverse('sweep_create', args=[10]) == '/scenarios/10/sweeps/new/'


class TestURLResolvers:
    """Test that URLs resolve to expected view names (even though views don't exist yet)."""

    def test_scenario_urls_resolve_to_scenarios_module(self):
        """Test that scenario URLs would resolve to scenarios views"""
        # Note: These will fail until views are implemented in Wave 3
        # but we can test that the URL pattern is correctly configured
        url = reverse('scenario_list')
        resolver = resolve(url)
        assert resolver.url_name == 'scenario_list'

        url = reverse('scenario_detail', args=[1])
        resolver = resolve(url)
        assert resolver.url_name == 'scenario_detail'
        assert resolver.kwargs == {'id': 1}

    def test_variation_urls_resolve_with_correct_kwargs(self):
        """Test that variation URLs resolve with correct parameter names"""
        url = reverse('variation_create', args=[5])
        resolver = resolve(url)
        assert resolver.url_name == 'variation_create'
        assert resolver.kwargs == {'scenario_id': 5}

        url = reverse('variation_detail', args=[10])
        resolver = resolve(url)
        assert resolver.url_name == 'variation_detail'
        assert resolver.kwargs == {'id': 10}

    def test_sweep_urls_resolve_with_correct_kwargs(self):
        """Test that sweep URLs resolve with correct parameter names"""
        url = reverse('sweep_create', args=[3])
        resolver = resolve(url)
        assert resolver.url_name == 'sweep_create'
        assert resolver.kwargs == {'scenario_id': 3}

        url = reverse('sweep_detail', args=[7])
        resolver = resolve(url)
        assert resolver.url_name == 'sweep_detail'
        assert resolver.kwargs == {'id': 7}
