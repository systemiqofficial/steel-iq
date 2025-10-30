"""
Comprehensive unit tests for AllocationModel functionality.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile

from steelo.economic_models.plant_agent import AllocationModel
from steelo.domain import Year
from steelo.domain.events import SteelAllocationsCalculated


class TestAllocationModelBusinessLogic:
    """Test core business logic of AllocationModel."""

    def test_tariff_handling_with_tariffs_enabled(self):
        """Test that tariff handling works when include_tariffs is True."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = True
        mock_bus.env.get_eu_countries.return_value = ["DEU", "FRA"]
        mock_bus.env.trade_tariffs = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.uow.repository = MagicMock()

        # Mock the trade tariff calculation
        mock_bus.env.get_active_trade_tariffs = MagicMock()
        mock_bus.env.get_active_trade_tariffs.return_value = [MagicMock(name="test_tariff")]

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    # Act
                    AllocationModel.run(mock_bus)

        # Assert
        mock_bus.env.get_active_trade_tariffs.assert_called_once()
        mock_setup.assert_called_once()
        call_kwargs = mock_setup.call_args.kwargs
        assert "active_trade_tariffs" in call_kwargs
        assert len(call_kwargs["active_trade_tariffs"]) == 1

    def test_tariff_handling_with_tariffs_disabled(self):
        """Test that tariff handling is skipped when include_tariffs is False."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = ["DEU", "FRA"]
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.uow.repository = MagicMock()

        mock_bus.env.get_active_trade_tariffs = MagicMock()
        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    # Act
                    AllocationModel.run(mock_bus)

        # Assert - tariffs should not be fetched
        mock_bus.env.get_active_trade_tariffs.assert_not_called()
        mock_setup.assert_called_once()
        call_kwargs = mock_setup.call_args.kwargs
        assert "active_trade_tariffs" not in call_kwargs

    def test_transport_emissions_processing(self):
        """Test that transport emissions are correctly updated when available."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = [{"route": "A-B", "emissions": 100}]
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.env.plot_paths = MagicMock()
        mock_bus.env.country_mappings = {}
        mock_bus.uow.repository = MagicMock()

        # Create mock allocations
        mock_steel_allocation = MagicMock()
        mock_steel_allocation.allocations = [MagicMock()]
        mock_steel_allocation.update_transport_emissions = MagicMock()

        mock_iron_allocation = MagicMock()
        mock_iron_allocation.allocations = []
        mock_iron_allocation.update_transport_emissions = MagicMock()

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {"steel": mock_steel_allocation, "iron": mock_iron_allocation}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    with patch("steelo.economic_models.plant_agent.plot_detailed_trade_map"):
                        with patch("steelo.economic_models.plant_agent.plot_trade_allocation_visualization"):
                            with patch("builtins.open", create=True):
                                with patch("steelo.economic_models.plant_agent.pickle.dump"):
                                    # Act
                                    AllocationModel.run(mock_bus)

        # Assert
        mock_steel_allocation.update_transport_emissions.assert_called_once_with(
            transport_emissions=mock_bus.env.transport_kpis
        )
        mock_iron_allocation.update_transport_emissions.assert_called_once_with(
            transport_emissions=mock_bus.env.transport_kpis
        )

    def test_non_empty_allocations_filtering(self):
        """Test that only commodities with allocations are processed for visualization."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.env.plot_paths = MagicMock()
        mock_bus.env.country_mappings = {}
        mock_bus.uow.repository = MagicMock()

        # Create mock allocations - steel has allocations, iron doesn't
        mock_steel_allocation = MagicMock()
        mock_steel_allocation.allocations = [MagicMock()]

        mock_iron_allocation = MagicMock()
        mock_iron_allocation.allocations = []

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {"steel": mock_steel_allocation, "iron": mock_iron_allocation}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    with patch("steelo.economic_models.plant_agent.plot_detailed_trade_map") as mock_plot_map:
                        with patch(
                            "steelo.economic_models.plant_agent.plot_trade_allocation_visualization"
                        ) as mock_plot_viz:
                            with patch("builtins.open", create=True):
                                with patch("steelo.economic_models.plant_agent.pickle.dump"):
                                    # Act
                                    AllocationModel.run(mock_bus)

        # Assert - only steel should be included in visualizations
        mock_plot_map.assert_called_once()
        plot_map_allocations = mock_plot_map.call_args.kwargs["allocations_by_commodity"]
        assert "steel" in plot_map_allocations
        assert "iron" not in plot_map_allocations

        mock_plot_viz.assert_called_once()
        plot_viz_allocations = mock_plot_viz.call_args.kwargs["allocations_by_commodity"]
        assert "steel" in plot_viz_allocations
        assert "iron" not in plot_viz_allocations


class TestAllocationModelVisualization:
    """Test visualization and output generation functionality."""

    def test_folium_trade_map_generation(self):
        """Test that folium trade map is generated with correct parameters."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.env.plot_paths = MagicMock(plots_dir=Path("/test/plots"), geo_plots_dir=Path("/test/plots/geo"))
        mock_bus.uow.repository = MagicMock()

        # Create mock allocations
        mock_allocation = MagicMock()
        mock_allocation.allocations = [MagicMock()]

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {"steel": mock_allocation}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    with patch("steelo.economic_models.plant_agent.plot_detailed_trade_map") as mock_plot_map:
                        with patch("steelo.economic_models.plant_agent.plot_trade_allocation_visualization"):
                            with patch("builtins.open", create=True):
                                with patch("steelo.economic_models.plant_agent.pickle.dump"):
                                    # Act
                                    AllocationModel.run(mock_bus)

        # Assert
        mock_plot_map.assert_called_once_with(
            allocations_by_commodity={"steel": mock_allocation},
            chosen_year=Year(2025),
            plot_paths=mock_bus.env.plot_paths,
        )

    def test_trade_allocation_visualization(self):
        """Test that trade allocation visualization is generated with correct parameters."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.env.plot_paths = MagicMock()
        mock_bus.env.country_mappings = {"DEU": "Germany", "FRA": "France"}
        mock_bus.uow.repository = MagicMock()

        # Create mock allocations
        mock_allocation = MagicMock()
        mock_allocation.allocations = [MagicMock()]

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {"steel": mock_allocation}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    with patch("steelo.economic_models.plant_agent.plot_detailed_trade_map"):
                        with patch(
                            "steelo.economic_models.plant_agent.plot_trade_allocation_visualization"
                        ) as mock_plot_viz:
                            with patch("builtins.open", create=True):
                                with patch("steelo.economic_models.plant_agent.pickle.dump"):
                                    # Act
                                    AllocationModel.run(mock_bus)

        # Assert
        mock_plot_viz.assert_called_once_with(
            allocations_by_commodity={"steel": mock_allocation},
            chosen_year=Year(2025),
            plot_paths=mock_bus.env.plot_paths,
            country_mappings=mock_bus.env.country_mappings,
            top_n=20,
        )

    def test_pickle_debugging_output(self):
        """Test that pickle files are saved for debugging when allocations exist."""
        # Setup
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)

            mock_bus = MagicMock()
            mock_bus.env.year = Year(2025)
            mock_bus.env.legal_process_connectors = []
            mock_bus.env.config = MagicMock()
            mock_bus.env.config.include_tariffs = False
            mock_bus.env.get_eu_countries.return_value = []
            mock_bus.env.secondary_feedstock_constraints = []
            mock_bus.env.aggregated_metallic_charge_constraints = []
            mock_bus.env.transport_kpis = []
            mock_bus.env.output_dir = output_dir
            mock_bus.env.plot_paths = MagicMock()
            mock_bus.env.country_mappings = {}
            mock_bus.uow.repository = MagicMock()

            # Create mock allocations
            mock_allocation = MagicMock()
            mock_allocation.allocations = [MagicMock()]

            with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
                mock_lp = MagicMock()
                mock_lp.allocations = {"test": "allocation_data"}
                mock_setup.return_value = mock_lp

                with patch(
                    "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
                ) as mock_solve:
                    mock_solve.return_value = {"steel": mock_allocation}

                    with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                        with patch("steelo.economic_models.plant_agent.plot_detailed_trade_map"):
                            with patch("steelo.economic_models.plant_agent.plot_trade_allocation_visualization"):
                                with patch("steelo.economic_models.plant_agent.pickle.dump") as mock_pickle:
                                    # Act
                                    AllocationModel.run(mock_bus)

            # Assert - pickle should be called once (first call is commented out in production code)
            assert mock_pickle.call_count == 1

            # Only call is for trade_lp.allocations
            first_call = mock_pickle.call_args_list[0]
            assert first_call.args[0] == {"test": "allocation_data"}


class TestAllocationModelEventHandling:
    """Test event handling and state management."""

    def test_steel_allocations_calculated_event_published(self):
        """Test that SteelAllocationsCalculated event is published when allocations exist."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.uow.repository = MagicMock()

        mock_allocations = {"test": "allocation_data"}

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = mock_allocations
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    with patch("builtins.open", create=True):
                        with patch("steelo.economic_models.plant_agent.pickle.dump"):
                            # Act
                            AllocationModel.run(mock_bus)

        # Assert
        mock_bus.handle.assert_called()
        event = mock_bus.handle.call_args.args[0]
        assert isinstance(event, SteelAllocationsCalculated)
        assert event.trade_allocations == mock_allocations

    def test_steel_allocations_calculated_event_not_published_when_no_allocations(self):
        """Test that event is not published when allocations don't exist."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.uow.repository = MagicMock()

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    # Act
                    AllocationModel.run(mock_bus)

        # Assert
        mock_bus.handle.assert_not_called()


class TestAllocationModelErrorHandling:
    """Test error handling and validation."""

    def test_legal_process_connectors_assertion(self):
        """Test that assertion error is raised when legal_process_connectors is None."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.legal_process_connectors = None

        # Act & Assert
        with pytest.raises(AssertionError) as exc_info:
            AllocationModel.run(mock_bus)

        assert "Legal process connectors must be set in the environment" in str(exc_info.value)

    def test_simulation_config_assertion(self):
        """Test that assertion error is raised when config is None."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = None

        # Act & Assert
        with pytest.raises(AssertionError) as exc_info:
            AllocationModel.run(mock_bus)

        assert "config is required for trade LP" in str(exc_info.value)

    def test_output_directory_validation(self):
        """Test that ValueError is raised when output_dir is None."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = None
        mock_bus.uow.repository = MagicMock()

        # Create mock allocations
        mock_allocation = MagicMock()
        mock_allocation.allocations = [MagicMock()]

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {"steel": mock_allocation}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    # Act & Assert
                    with pytest.raises(ValueError) as exc_info:
                        AllocationModel.run(mock_bus)

                    assert "output_dir must be set on bus.env" in str(exc_info.value)


class TestAllocationModelIntegrationPoints:
    """Test integration with other components."""

    def test_secondary_feedstock_constraints_processing(self):
        """Test that secondary feedstock constraints are processed correctly."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = True
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.trade_tariffs = []
        mock_bus.env.secondary_feedstock_constraints = [
            MagicMock(year=Year(2024)),
            MagicMock(year=Year(2025)),
            MagicMock(year=Year(2026)),
        ]
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.uow.repository = MagicMock()

        mock_filtered_constraints = [mock_bus.env.secondary_feedstock_constraints[1]]

        mock_bus.env.get_active_trade_tariffs = MagicMock()
        mock_bus.env.get_active_trade_tariffs.return_value = []

        mock_bus.env.relevant_secondary_feedstock_constraints = MagicMock()
        mock_bus.env.relevant_secondary_feedstock_constraints.return_value = mock_filtered_constraints

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    # Act
                    AllocationModel.run(mock_bus)

        # Assert
        mock_bus.env.relevant_secondary_feedstock_constraints.assert_called_once()

        # Verify filtered constraints were passed to set_up_steel_trade_lp
        mock_setup.assert_called_once()
        call_kwargs = mock_setup.call_args.kwargs
        assert call_kwargs["secondary_feedstock_constraints"] == mock_filtered_constraints

    def test_aggregated_metallic_charge_constraints(self):
        """Test that aggregated metallic charge constraints are passed correctly."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = True
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.trade_tariffs = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = [
            MagicMock(name="constraint1"),
            MagicMock(name="constraint2"),
        ]
        mock_bus.env.transport_kpis = []
        mock_bus.env.output_dir = Path("/test/output")
        mock_bus.uow.repository = MagicMock()

        mock_bus.env.get_active_trade_tariffs = MagicMock()
        mock_bus.env.get_active_trade_tariffs.return_value = []
        mock_bus.env.relevant_secondary_feedstock_constraints = MagicMock()
        mock_bus.env.relevant_secondary_feedstock_constraints.return_value = []

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_lp = MagicMock()
            mock_lp.allocations = None
            mock_setup.return_value = mock_lp

            with patch(
                "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
            ) as mock_solve:
                mock_solve.return_value = {}

                with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv"):
                    # Act
                    AllocationModel.run(mock_bus)

        # Assert
        mock_setup.assert_called_once()
        call_kwargs = mock_setup.call_args.kwargs
        assert (
            call_kwargs["aggregated_metallic_charge_constraints"] == mock_bus.env.aggregated_metallic_charge_constraints
        )
        assert len(call_kwargs["aggregated_metallic_charge_constraints"]) == 2


class TestAllocationModelIntegration:
    """End-to-end integration tests for AllocationModel."""

    def test_full_allocation_flow_with_results(self):
        """Test complete allocation flow when LP solving produces results."""
        # Setup
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "TM").mkdir(parents=True)

            mock_bus = MagicMock()
            mock_bus.env.year = Year(2025)
            mock_bus.env.legal_process_connectors = [MagicMock()]
            mock_bus.env.config = MagicMock()
            mock_bus.env.config.include_tariffs = True
            mock_bus.env.get_eu_countries.return_value = ["DEU", "FRA"]
            mock_bus.env.trade_tariffs = [MagicMock()]
            mock_bus.env.secondary_feedstock_constraints = []
            mock_bus.env.aggregated_metallic_charge_constraints = []
            mock_bus.env.transport_kpis = [{"route": "A-B", "emissions": 50}]
            mock_bus.env.output_dir = output_dir
            mock_bus.env.plot_paths = MagicMock()
            mock_bus.env.country_mappings = {"DEU": "Germany"}
            mock_bus.uow.repository = MagicMock()

            # Create mock allocations
            mock_allocation = MagicMock()
            mock_allocation.allocations = [MagicMock()]
            mock_allocation.update_transport_emissions = MagicMock()

            mock_allocations = {"steel": mock_allocation}
            mock_lp_allocations = {"lp": "allocations"}

            mock_bus.env.get_active_trade_tariffs = MagicMock()
            mock_bus.env.get_active_trade_tariffs.return_value = [MagicMock()]
            mock_bus.env.relevant_secondary_feedstock_constraints = MagicMock()
            mock_bus.env.relevant_secondary_feedstock_constraints.return_value = []

            with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
                mock_lp = MagicMock()
                mock_lp.allocations = mock_lp_allocations
                mock_setup.return_value = mock_lp

                with patch(
                    "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
                ) as mock_solve:
                    mock_solve.return_value = mock_allocations

                    with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv") as mock_export:
                        with patch("steelo.economic_models.plant_agent.plot_detailed_trade_map") as mock_plot_map:
                            with patch(
                                "steelo.economic_models.plant_agent.plot_trade_allocation_visualization"
                            ) as mock_plot_viz:
                                with patch("steelo.economic_models.plant_agent.pickle.dump") as mock_pickle:
                                    # Act
                                    AllocationModel.run(mock_bus)

            # Assert all components were called
            mock_bus.env.get_active_trade_tariffs.assert_called_once()
            mock_setup.assert_called_once()
            mock_solve.assert_called_once()
            mock_export.assert_called_once()
            mock_plot_map.assert_called_once()
            mock_plot_viz.assert_called_once()
            assert mock_pickle.call_count == 1
            mock_bus.handle.assert_called_once()

            # Verify transport emissions were updated
            mock_allocation.update_transport_emissions.assert_called_once()

    def test_allocation_flow_with_no_allocations(self):
        """Test allocation flow when no allocations are produced."""
        # Setup
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            (output_dir / "TM").mkdir(parents=True)

            mock_bus = MagicMock()
            mock_bus.env.year = Year(2025)
            mock_bus.env.legal_process_connectors = []
            mock_bus.env.config = MagicMock()
            mock_bus.env.config.include_tariffs = False
            mock_bus.env.get_eu_countries.return_value = []
            mock_bus.env.secondary_feedstock_constraints = []
            mock_bus.env.aggregated_metallic_charge_constraints = []
            mock_bus.env.transport_kpis = []
            mock_bus.env.output_dir = output_dir
            mock_bus.uow.repository = MagicMock()

            mock_bus.env.relevant_secondary_feedstock_constraints = MagicMock()
            mock_bus.env.relevant_secondary_feedstock_constraints.return_value = []

            with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
                mock_lp = MagicMock()
                mock_lp.allocations = None
                mock_setup.return_value = mock_lp

                with patch(
                    "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
                ) as mock_solve:
                    mock_solve.return_value = {}

                    with patch("steelo.economic_models.plant_agent.export_commodity_allocations_to_csv") as mock_export:
                        with patch("steelo.economic_models.plant_agent.plot_detailed_trade_map") as mock_plot_map:
                            with patch(
                                "steelo.economic_models.plant_agent.plot_trade_allocation_visualization"
                            ) as mock_plot_viz:
                                # Act
                                AllocationModel.run(mock_bus)

            # Assert
            mock_export.assert_called_once()  # CSV export still happens
            mock_plot_map.assert_not_called()  # No plotting for empty allocations
            mock_plot_viz.assert_not_called()
            mock_bus.handle.assert_not_called()  # No event published

    def test_allocation_flow_error_propagation(self):
        """Test that errors in LP solving are properly propagated."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2025)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.uow.repository = MagicMock()

        mock_bus.env.relevant_secondary_feedstock_constraints = MagicMock()
        mock_bus.env.relevant_secondary_feedstock_constraints.return_value = []

        with patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup:
            mock_setup.side_effect = RuntimeError("LP setup failed")

            # Act & Assert
            with pytest.raises(RuntimeError) as exc_info:
                AllocationModel.run(mock_bus)

            assert "LP setup failed" in str(exc_info.value)

    def test_carbon_cost_update_called_on_plants(self):
        """Test that AllocationModel.run() calls update_furnace_group_carbon_costs on all plants."""
        # Setup
        mock_bus = MagicMock()
        mock_bus.env.year = Year(2030)
        mock_bus.env.legal_process_connectors = []
        mock_bus.env.config = MagicMock()
        mock_bus.env.config.include_tariffs = False
        mock_bus.env.get_eu_countries.return_value = []
        mock_bus.env.secondary_feedstock_constraints = []
        mock_bus.env.aggregated_metallic_charge_constraints = []
        mock_bus.env.transport_kpis = []
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_bus.env.output_dir = Path(temp_dir)
            mock_bus.env.plot_paths = MagicMock()
            mock_bus.env.country_mappings = MagicMock()
            mock_bus.uow.repository = MagicMock()

            mock_bus.env.relevant_secondary_feedstock_constraints = MagicMock()
            mock_bus.env.relevant_secondary_feedstock_constraints.return_value = []

            # Create mock plants to verify the method is called
            mock_plant1 = MagicMock()
            mock_plant2 = MagicMock()
            mock_bus.uow.plants.list.return_value = [mock_plant1, mock_plant2]

            with (
                patch("steelo.economic_models.plant_agent.set_up_steel_trade_lp") as mock_setup,
                patch(
                    "steelo.economic_models.plant_agent.solve_steel_trade_lp_and_return_commodity_allocations"
                ) as mock_solve,
            ):
                mock_lp = MagicMock()
                mock_lp.allocations = None
                mock_setup.return_value = mock_lp
                mock_solve.return_value = {}

                # Act
                AllocationModel.run(mock_bus)

                # Assert
                mock_plant1.update_furnace_group_carbon_costs.assert_called_once_with(
                    Year(2030), mock_bus.env.config.chosen_emissions_boundary_for_carbon_costs
                )
                mock_plant2.update_furnace_group_carbon_costs.assert_called_once_with(
                    Year(2030), mock_bus.env.config.chosen_emissions_boundary_for_carbon_costs
                )
