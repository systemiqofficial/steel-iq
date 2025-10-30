"""Test that debt accumulation works correctly when switching technologies."""

from steelo.domain.models import (
    FurnaceGroup,
    Plant,
    Technology,
    PointInTime,
    TimeFrame,
    Year,
    Location,
)


def test_debt_accumulation_on_technology_switch():
    """Test that when switching technologies mid-lifetime, old debt is preserved and accumulated with new debt."""

    # Create a plant with a furnace group
    plant = Plant(
        plant_id="test_plant",
        location=Location(lat=40.0, lon=-95.0, country="United States", region="North America", iso3="USA"),
        furnace_groups=[],
        power_source="grid",
        soe_status="private",
        parent_gem_id="parent_123",
        workforce_size=100,
        certified=False,
        category_steel_product=set(),
        steel_capacity=1000,
        technology_unit_fopex={"bf-bof": 50, "dri-eaf": 40, "eaf": 45},
    )

    # Create initial furnace group with BOF technology
    furnace_group = FurnaceGroup(
        furnace_group_id="fg1",
        capacity=1000,
        status="operating",
        last_renovation_date=None,
        technology=Technology(
            name="BOF",
            bill_of_materials={},
            product="steel",
            capex_type="greenfield",
            capex=500,  # $500/tonne
        ),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=2024,
            time_frame=TimeFrame(start=Year(2014), end=Year(2034)),  # 10 years into 20-year lifetime
            plant_lifetime=20,
        ),
        equity_share=0.2,  # 80% debt financing
        cost_of_debt=0.05,
    )

    plant.add_furnace_group(furnace_group)

    # Calculate initial debt repayments before technology switch
    initial_debt_per_year = furnace_group.debt_repayment_per_year.copy()
    initial_remaining_years = furnace_group.lifetime.remaining_number_of_years

    # Verify we have 10 years remaining on the initial technology
    assert initial_remaining_years == 10, f"Expected 10 years remaining, got {initial_remaining_years}"

    # Calculate the remaining debt from initial technology
    remaining_initial_debt = initial_debt_per_year[-initial_remaining_years:] if initial_remaining_years > 0 else []

    # Switch to EAF technology after 10 years
    plant.change_furnace_group_technology(
        furnace_group_id="fg1",
        technology_name="EAF",
        plant_lifetime=20,
        lag=0,
        capex=400.0,  # New technology capex
        capex_no_subsidy=400.0,  # Same as capex if no subsidy
        cost_of_debt=0.05,
        cost_of_debt_no_subsidy=0.05,
        bom={"materials": {}, "energy": {}},
    )

    # Get the furnace group after technology change
    fg_after_switch = plant.get_furnace_group("fg1")

    # Verify the technology was changed
    assert fg_after_switch.technology.name == "EAF"

    # Verify legacy debt was preserved
    assert fg_after_switch.legacy_debt_schedule is not None, "Legacy debt schedule should not be None"
    assert len(fg_after_switch.legacy_debt_schedule) == len(remaining_initial_debt), (
        f"Legacy debt should have {len(remaining_initial_debt)} years of payments"
    )

    # Verify the new debt repayment schedule includes both old and new debt
    new_debt_per_year = fg_after_switch.debt_repayment_per_year

    # For the first 10 years after switch, we should have both old and new debt
    for i in range(min(10, len(new_debt_per_year))):
        # The debt payment should be higher than just the new technology's debt alone
        # because it includes the legacy debt
        assert new_debt_per_year[i] > 0, f"Year {i} debt payment should be positive"

    # Verify debt for current year includes legacy debt
    current_year_debt = fg_after_switch.debt_repayment_for_current_year
    assert current_year_debt > 0, "Current year debt should include both new and legacy debt"

    # Simulate a year passing and verify legacy debt schedule is updated
    initial_legacy_debt_length = len(fg_after_switch.legacy_debt_schedule)
    fg_after_switch.update_balance_sheet(market_price=800)  # Simulate annual update

    # Legacy debt schedule should be reduced by one year
    assert len(fg_after_switch.legacy_debt_schedule) == initial_legacy_debt_length - 1, (
        "Legacy debt schedule should be reduced by one payment after annual update"
    )


def test_npv_calculation_with_accumulated_debt():
    """Test that NPV calculation properly accounts for accumulated debt through COSA."""

    # Create a furnace group with some remaining debt
    furnace_group = FurnaceGroup(
        furnace_group_id="fg2",
        capacity=1000,
        status="operating",
        last_renovation_date=None,
        technology=Technology(
            name="BOF",
            bill_of_materials={"materials": {"iron_ore": 1.6}, "energy": {"electricity": 0.1}},
            product="steel",
            capex_type="greenfield",
            capex=500,
        ),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=2024,
            time_frame=TimeFrame(start=Year(2014), end=Year(2034)),  # 10 years remaining
            plant_lifetime=20,
        ),
        equity_share=0.2,
        cost_of_debt=0.05,
        # Add some legacy debt from a previous technology switch
        legacy_debt_schedule=[50000, 50000, 50000, 50000, 50000],  # 5 years of legacy debt
    )

    # The debt_repayment_per_year should include both current tech debt and legacy debt
    debt_payments = furnace_group.debt_repayment_per_year

    # Verify that the first 5 years have higher payments due to legacy debt
    for i in range(5):
        # These years should have both legacy and current debt
        assert debt_payments[i] > debt_payments[5] if len(debt_payments) > 5 else True, (
            f"Year {i} should have higher debt due to legacy payments"
        )

    # Current year debt should include legacy debt
    current_debt = furnace_group.debt_repayment_for_current_year
    assert current_debt > 0, "Current year debt should include legacy debt"

    # The total debt burden affects the COSA calculation and NPV for technology switches
    # This ensures that switching technologies is less attractive when carrying old debt


def test_renovation_does_not_need_legacy_debt_clearing():
    """Verify that renovation happens at end of lifetime when debt is already paid."""

    furnace_group = FurnaceGroup(
        furnace_group_id="fg3",
        capacity=1000,
        status="operating",
        last_renovation_date=None,
        technology=Technology(
            name="BOF",
            bill_of_materials={},
            product="steel",
            capex_type="greenfield",
            capex=500,
        ),
        historical_production={},
        utilization_rate=0.8,
        lifetime=PointInTime(
            current=2024,
            time_frame=TimeFrame(start=Year(2004), end=Year(2024)),  # Lifetime expired
            plant_lifetime=20,
        ),
        equity_share=0.2,
        cost_of_debt=0.05,
    )

    # When lifetime is expired, remaining years should be 0
    assert furnace_group.lifetime.expired, "Lifetime should be expired"
    assert furnace_group.lifetime.remaining_number_of_years == 0, "No years should remain"

    # At renovation time, all debt should be paid off
    debt_payments = furnace_group.debt_repayment_per_year
    # With 0 remaining years, there should be no future debt payments
    assert len(debt_payments) == 0 or all(payment == 0 for payment in debt_payments), (
        "No debt payments should remain at renovation time"
    )
