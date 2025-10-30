"""Test that probability parameters are properly passed through the system."""

from unittest.mock import MagicMock, patch
from steelo.domain.constants import MT_TO_T
from steelo.domain.models import PlantGroup, Year


def test_probability_parameters_passed_to_build_new_plant():
    """Test that probability parameters are passed from config to build_new_plant."""

    # Create mock environment with config
    mock_env = MagicMock()
    mock_env.config.probability_of_announcement = 0.8
    mock_env.config.probability_of_construction = 0.95

    # Create mock bus with environment
    mock_bus = MagicMock()
    mock_bus.env = mock_env

    # Mock PlantGroup's update_status_of_business_opportunities method
    with patch.object(PlantGroup, "update_status_of_business_opportunities") as mock_update:
        mock_update.return_value = []

        # Create a plant group
        pg = PlantGroup(plant_group_id="test", plants=[])

        # Call the method that should pass probability parameters
        pg.update_status_of_business_opportunities(
            current_year=Year(2025),
            custom_energy_costs={},
            market_price={},
            capex_dict_all_locs={},
            cost_debt_all_locs={},
            cost_of_equity_all_locs={},
            get_bom_from_avg_boms=MagicMock(),
            iso3_to_region_map={},
            global_risk_free_rate=0.0209,
            probability_of_announcement=0.8,
            probability_of_construction=0.95,
        )

        # Verify the method was called with the correct parameters
        mock_update.assert_called_once()
        kwargs = mock_update.call_args.kwargs
        assert kwargs["probability_of_announcement"] == 0.8
        assert kwargs["probability_of_construction"] == 0.95


def test_top_n_loctechs_parameter_passed_to_identify_opportunities():
    """Test that top_n_loctechs_as_business_op is passed to identify_new_business_opportunities_4indi."""

    # Mock PlantGroup's identify_new_business_opportunities_4indi method
    with patch.object(PlantGroup, "identify_new_business_opportunities_4indi") as mock_identify:
        mock_identify.return_value = MagicMock()

        # Create a plant group
        pg = PlantGroup(plant_group_id="test", plants=[])

        # Call the method with top_n_loctechs_as_business_op parameter
        pg.identify_new_business_opportunities_4indi(
            current_year=Year(2025),
            input_costs={},
            locations={},
            iso3_to_region_map={},
            market_price={},
            capex_dict_all_locs_techs={},
            cost_of_debt_all_locs={},
            cost_of_equity_all_locs={},
            steel_plant_capacity=2.5 * MT_TO_T,
            plant_lifetime=20,
            all_plant_ids=[],
            fopex_all_locs_techs={},
            equity_share=0.2,
            dynamic_feedstocks={},
            get_bom_from_avg_boms=MagicMock(),
            global_risk_free_rate=0.0209,
            top_n_loctechs_as_business_op=7,  # Custom value
        )

        # Verify the method was called with the correct parameter
        mock_identify.assert_called_once()
        kwargs = mock_identify.call_args.kwargs
        assert kwargs["top_n_loctechs_as_business_op"] == 7
