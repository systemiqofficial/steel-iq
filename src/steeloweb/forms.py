from django import forms
import tempfile
from pathlib import Path
from datetime import datetime
from decimal import Decimal

from steelo.capacity_policy.config import CapacityPolicyConfig
from steelo.capacity_policy.pool import BANKED_CREDIT_RULES
from steelo.domain.constants import END_YEAR_DEFAULT, MT_TO_T, START_YEAR_DEFAULT
from steelo.simulation import config_default

from .models import ModelRun, DataPreparation, MasterExcelFile


class CircularityDataForm(forms.Form):
    """Form for uploading custom circularity and scrap generation data."""

    name = forms.CharField(
        max_length=255,
        help_text="Name for the circularity data file",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    description = forms.CharField(
        required=False,
        help_text="Description of the circularity data",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    circularity_file = forms.FileField(
        help_text="Upload custom circularity data (JSON format)",
        widget=forms.FileInput(attrs={"class": "form-control"}),
    )

    def clean_circularity_file(self):
        """Validate the uploaded circularity file."""
        file = self.cleaned_data.get("circularity_file")
        if file:
            # Check file extension
            if not file.name.endswith(".json"):
                raise forms.ValidationError("Only JSON files are allowed.")

            # Check file size (limit to 10MB)
            if file.size > 10 * 1024 * 1024:  # 10MB in bytes
                raise forms.ValidationError("File size cannot exceed 10MB.")

        return file


class MasterExcelFileForm(forms.ModelForm):
    """Form for uploading and managing master Excel files."""

    class Meta:
        model = MasterExcelFile
        fields = ["name", "description", "file"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., 'Modified CAPEX values'"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "file": forms.FileInput(attrs={"class": "form-control", "accept": ".xlsx,.xls"}),
        }
        help_texts = {
            "name": "Give your master Excel file a descriptive name",
            "description": "Describe what modifications you made to the template",
            "file": "Upload your modified master Excel file (.xlsx format)",
        }

    def clean_file(self):
        """Validate the uploaded file."""
        file = self.cleaned_data.get("file")
        if file:
            # Check file extension
            if not file.name.endswith((".xlsx", ".xls")):
                raise forms.ValidationError("Only Excel files (.xlsx or .xls) are allowed.")

            # Check file size (limit to 200MB)
            if file.size > 200 * 1024 * 1024:  # 200MB in bytes
                raise forms.ValidationError("File size cannot exceed 200MB.")

        return file


class ModelRunCreateForm(forms.ModelForm):
    """Form for creating a new model run with custom configuration."""

    name = forms.CharField(
        max_length=255,
        required=False,
        help_text="Give your simulation a descriptive name to identify it later",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g., 'High renewable scenario 2030'"}),
    )

    randomise_seed = forms.BooleanField(
        label="Randomise seed",
        initial=False,
        required=False,
        help_text=(
            "Tick to draw a fresh random seed for this run. When unticked, the seed below is used "
            "so results are reproducible. When ticked, results will vary slightly between runs due "
            "to stochastic components in agent decisions and the trade LP."
        ),
        widget=forms.CheckboxInput(
            attrs={"class": "form-check-input field-connected", "id": "id_randomise_seed"},
        ),
    )

    random_seed = forms.IntegerField(
        label="Random seed",
        initial=config_default("random_seed"),
        min_value=0,
        required=False,
        help_text=(
            "Seed shared by the Plant Agent, Geospatial, and Trade LP modules. "
            "Auto-filled with a fresh value when 'Randomise seed' is ticked."
        ),
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "id": "id_random_seed"}),
    )

    # Helper method to create widget attrs for connected/unconnected fields
    @staticmethod
    def _connected_attrs(base_class="form-control"):
        """Returns widget attributes for connected (working) fields."""
        return {"class": f"{base_class} field-connected"}

    @staticmethod
    def _unconnected_attrs(base_class="form-control", disabled=True):
        """Returns widget attributes for unconnected (not yet implemented) fields."""
        attrs = {"class": f"{base_class} field-not-connected"}
        if disabled:
            attrs["disabled"] = True
        return attrs

    SCENARIO_CHOICES = [
        ("business_as_usual", "Business As Usual"),
        ("system_change", "System Change"),
        ("accelerated_transition", "Accelerated Transition"),
        ("climate_neutrality", "Climate Neutrality 2050"),
        ("high_efficiency", "High Efficiency"),
        ("circular_economy", "Circular Economy Focus"),
        ("technology_breakthrough", "Technology Breakthrough"),
    ]

    # Geospatial section choices
    INCLUDED_POWER_MIX_CHOICES = [
        ("85% baseload + 15% grid", "85% baseload + 15% grid"),
        ("95% baseload + 5% grid", "95% baseload + 5% grid"),
        ("Not included", "Not included"),
        ("Grid only", "Grid only"),
    ]

    POWER_PRICE_FILE_CHOICES = [
        ("", "None selected"),
        ("low_forecast", "Low Price Forecast"),
        ("medium_forecast", "Medium Price Forecast"),
        ("high_forecast", "High Price Forecast"),
        ("regional_variations", "Regional Variations"),
        ("custom", "Custom Power Prices"),
    ]

    INFRASTRUCTURE_CHOICES = [
        ("", "Default infrastructure"),
        ("current_infrastructure", "Current Infrastructure Only"),
        ("planned_expansion", "Planned Infrastructure Expansion"),
        ("optimistic_scenario", "Optimistic Infrastructure Scenario"),
        ("custom", "Custom Infrastructure Data"),
    ]

    TRANSPORT_COST_CHOICES = [
        ("", "Default transport costs"),
        ("low_cost", "Low Transport Costs"),
        ("medium_cost", "Medium Transport Costs"),
        ("high_cost", "High Transport Costs"),
        ("custom", "Custom Transport Costs"),
    ]

    LAND_USE_CHOICES = [
        ("", "Default land use/cover data"),
        ("restricted", "Highly Restricted Land Use"),
        ("moderate", "Moderately Restricted Land Use"),
        ("permissive", "Permissive Land Use Policy"),
        ("custom", "Custom Land Use Data"),
    ]

    start_year = forms.IntegerField(
        initial=START_YEAR_DEFAULT,
        min_value=2020,
        max_value=2060,
        help_text="Start year for the simulation",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    end_year = forms.IntegerField(
        initial=END_YEAR_DEFAULT,
        min_value=2020,
        max_value=2060,
        help_text="End year for the simulation",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    # Economic parameters
    plant_lifetime = forms.IntegerField(
        initial=config_default("plant_lifetime"),
        min_value=1,
        max_value=100,
        help_text="Lifetime of steel plants in years",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    global_risk_free_rate = forms.DecimalField(
        initial=config_default("global_risk_free_rate"),
        min_value=0.0,
        max_value=0.5,
        max_digits=5,
        decimal_places=4,
        required=False,
        help_text="Global risk-free rate (acts as floor for debt cost after subsidies)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.0001"}),
    )

    steel_price_buffer = forms.DecimalField(
        label="Steel premium",
        initial=config_default("steel_price_buffer"),
        min_value=0.0,
        max_value=2000.0,
        max_digits=6,
        decimal_places=1,
        required=False,
        help_text="Addition to the steel price when demand exceeds supply to incentivize more capacity buildout (USD/tonne)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.1"}),
    )

    iron_price_buffer = forms.DecimalField(
        label="Iron premium",
        initial=config_default("iron_price_buffer"),
        min_value=0.0,
        max_value=2000.0,
        max_digits=6,
        decimal_places=1,
        required=False,
        help_text="Addition to the iron price when demand exceeds supply to incentivize more capacity buildout (USD/tonne)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.1"}),
    )

    steel_market_clearing_share = forms.DecimalField(
        label="Steel market clearing share",
        initial=config_default("steel_market_clearing_share"),
        min_value=0.5,
        max_value=0.995,
        max_digits=4,
        decimal_places=3,
        required=False,
        help_text=(
            "Fraction of total steel cumulative capacity that participates in market clearing in the cost curve; "
            "demand above this share triggers the steel premium instead of pricing off the long tail "
            "(default 0.95, max 0.995)."
        ),
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    iron_market_clearing_share = forms.DecimalField(
        label="Iron market clearing share",
        initial=config_default("iron_market_clearing_share"),
        min_value=0.5,
        max_value=0.995,
        max_digits=4,
        decimal_places=3,
        required=False,
        help_text=(
            "Fraction of total iron cumulative capacity that participates in market clearing in the cost curve; "
            "demand above this share triggers the iron premium instead of pricing off the long tail "
            "(default 0.95, max 0.995)."
        ),
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    peg_iron_to_steel_price = forms.BooleanField(
        label="Peg iron price to steel price",
        initial=config_default("peg_iron_to_steel_price"),
        required=False,
        help_text="When enabled, iron price is floored at a percentage of the steel price instead of following cost curves alone",
        widget=forms.CheckboxInput(
            attrs={"class": "form-check-input field-connected", "id": "id_peg_iron_to_steel_price"}
        ),
    )

    iron_to_steel_price_ratio = forms.DecimalField(
        label="Iron-to-steel price ratio",
        initial=config_default("iron_to_steel_price_ratio"),
        min_value=0.0,
        max_value=1.0,
        max_digits=3,
        decimal_places=2,
        required=False,
        help_text="Ratio of steel price used as iron price floor when pegging is enabled (0.8 = 80% of steel price)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    opening_balance_multiplier = forms.DecimalField(
        label="Plant opening balance multiplier",
        initial=config_default("opening_balance_multiplier"),
        min_value=0.0,
        max_value=100.0,
        max_digits=5,
        decimal_places=2,
        required=False,
        help_text="Scales the opening cash balance for plants at simulation start: 1.0 gives each furnace group enough to fund one renovation, 0.0 starts with empty balances",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    construction_time = forms.IntegerField(
        initial=config_default("construction_time"),
        min_value=1,
        max_value=10,
        required=False,
        help_text="Years required to construct a plant after announcement",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    consideration_time = forms.IntegerField(
        initial=config_default("consideration_time"),
        min_value=1,
        max_value=10,
        required=False,
        help_text="Years to consider a business opportunity and track its financial viability before deciding on announcement (planning horizon)",
        widget=forms.NumberInput(
            attrs={"class": "form-control field-connected", "data-default": str(config_default("consideration_time"))}
        ),
    )

    probabilistic_agents = forms.BooleanField(
        label="Probabilistic agents",
        initial=config_default("probabilistic_agents"),
        required=False,
        help_text="Enable probabilistic decision-making (mimics human behavior). When disabled, uses deterministic approach.",
        widget=forms.CheckboxInput(
            attrs={"class": "form-check-input field-connected", "id": "id_probabilistic_agents"}
        ),
    )

    probability_of_announcement = forms.DecimalField(
        label="Probability of announcement",
        initial=config_default("probability_of_announcement"),
        min_value=0.0,
        max_value=1.0,
        max_digits=3,
        decimal_places=2,
        required=False,
        help_text="Probability that a considered plant with a history of positive NPVs will be announced (0.0-1.0)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    probability_of_construction = forms.DecimalField(
        label="Probability of construction",
        initial=config_default("probability_of_construction"),
        min_value=0.0,
        max_value=1.0,
        max_digits=3,
        decimal_places=2,
        required=False,
        help_text="Probability that an announced plant will begin construction (0.0-1.0)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    top_n_loctechs_as_business_op = forms.IntegerField(
        label="Top N location-technologies",
        initial=config_default("top_n_loctechs_as_business_op"),
        min_value=1,
        max_value=20,
        required=False,
        help_text="Number of top location-technology pairs to consider as business opportunities",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    opportunity_pool_depth = forms.IntegerField(
        label="Opportunity pool depth",
        initial=config_default("opportunity_pool_depth"),
        min_value=1,
        max_value=10,
        required=False,
        help_text=(
            "Draw eligibility: global top (depth × top N) by NPV plus each technology's best 'depth' sites, "
            "so every technology stays drawable"
        ),
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    pick_priority_sites_share = forms.DecimalField(
        label="Priority sites fraction",
        initial=config_default("geo_config.pick_priority_sites_share"),
        min_value=0.001,
        max_value=1.0,
        max_digits=4,
        decimal_places=3,
        required=False,
        help_text="Fraction (0.0-1.0) of global grid points selected as priority locations for business opportunities",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.001"}),
    )

    calculate_npv_sites_share = forms.DecimalField(
        label="NPV sample fraction",
        initial=config_default("calculate_npv_sites_share"),
        min_value=0.0,
        max_value=1.0,
        max_digits=3,
        decimal_places=2,
        required=False,
        help_text=(
            "Fraction (0.0-1.0) of priority locations sampled each year for full NPV evaluation; "
            "the 0.1 default only saves computational time"
        ),
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    # Plant capacity parameters
    expanded_capacity = forms.DecimalField(
        label="Expanded capacity",
        initial=config_default("expanded_capacity") / MT_TO_T,
        min_value=0.1,
        max_value=1000.0,
        max_digits=6,
        decimal_places=1,
        required=False,
        help_text="Expansion size for already existing furnace groups and initial size of new plants (Mt)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.1"}),
    )

    capacity_limit_iron = forms.DecimalField(
        label="Capacity limit - Iron",
        initial=config_default("capacity_limit_iron") / MT_TO_T,
        min_value=0.1,
        max_value=1000.0,
        max_digits=6,
        decimal_places=1,
        required=False,
        help_text="Yearly new capacity limit for switching and expanding existing, and opening new iron furnace groups (Mt)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.1"}),
    )

    capacity_limit_steel = forms.DecimalField(
        label="Capacity limit - Steel",
        initial=config_default("capacity_limit_steel") / MT_TO_T,
        min_value=0.1,
        max_value=1000.0,
        max_digits=6,
        decimal_places=1,
        required=False,
        help_text="Yearly new capacity limit for switching and expanding existing, and opening new steel furnace groups (Mt)",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.1"}),
    )

    new_capacity_share_from_new_plants = forms.DecimalField(
        label="Proportion of new capacity from new plants",
        initial=config_default("new_capacity_share_from_new_plants"),
        min_value=0.0,
        max_value=1.0,
        max_digits=3,
        decimal_places=2,
        required=False,
        help_text="Proportion of new capacity from new plants vs expansions of existing plants (0.0-1.0). For instance, 0.2 means that 20% of the new capacity limit (see above) will be used for new plants and 80% for expansions and switches of existing ones.",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    # Policy Settings fields
    use_iron_ore_premiums = forms.BooleanField(
        label="Use iron ore premiums",
        initial=config_default("use_iron_ore_premiums"),
        required=False,
        help_text="Include iron ore premiums in calculations",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    include_tariffs = forms.BooleanField(
        label="Include tariffs",
        initial=config_default("include_tariffs"),
        required=False,
        help_text="Include tariffs in trade calculations",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    enable_furnace_group_clustering = forms.BooleanField(
        label="Enable furnace group clustering",
        initial=config_default("enable_furnace_group_clustering"),
        required=False,
        help_text=(
            "Speed up trade calculations by grouping similar furnace groups (same technology, reductant, and country) "
            "into clusters. Switching this off radically increases the runtime."
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    geographical_clustering_scope = forms.ChoiceField(
        label="Geographical clustering scope",
        choices=[
            ("iso3", "Country (ISO3)"),
            ("plant_group", "Plant group (corporate)"),
            ("plant", "Individual plant"),
        ],
        initial=config_default("geographical_clustering_scope"),
        required=False,
        help_text=(
            "Geographical scope for clustering furnace groups consuming/producing closely-allocated commodities. "
            "Determines how fine-grained the clustering is while keeping cold/hot commodity substitution local. "
            "(requires clustering on)"
        ),
        widget=forms.Select(attrs={"class": "form-select field-connected"}),
    )

    chosen_emissions_boundary_for_carbon_costs = forms.ChoiceField(
        label="Carbon cost emissions boundary",
        choices=[],
        initial=config_default("chosen_emissions_boundary_for_carbon_costs"),
        required=False,
        help_text="Emissions boundary whose emissions carbon prices are charged on; the choices come from the data preparation",
        widget=forms.Select(attrs={"class": "form-select field-connected"}),
    )

    carbon_border_export_rebates = forms.BooleanField(
        label="Carbon border export rebates",
        initial=config_default("carbon_border_export_rebates"),
        required=False,
        help_text="Rebate the carbon already paid on exports leaving a carbon border region",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    # Capacity policy: the clean() step gathers the capacity_policy_* fields into one stored dictionary
    capacity_policy_enabled = forms.BooleanField(
        label="Capacity-replacement policy",
        initial=config_default("capacity_policy.enabled"),
        required=False,
        help_text="Enable China's capacity-replacement policy",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    capacity_policy_replacement_ratio = forms.DecimalField(
        label="Replacement ratio",
        initial=config_default("capacity_policy.replacement_ratio"),
        required=False,
        help_text="Old-to-new capacity ratio for a penalised replacement; the furnace group shrinks to old / ratio",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    capacity_policy_emission_intense_penalty_divisor = forms.DecimalField(
        label="Emission-intense penalty divisor",
        initial=config_default("capacity_policy.emission_intense_penalty_divisor"),
        required=False,
        help_text="Divisor on an emission-intense new build's permitted capacity; a lever separate from the ratio",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    capacity_policy_min_utilization_for_renovation = forms.DecimalField(
        label="Minimum utilisation for renovation",
        initial=config_default("capacity_policy.min_utilization_for_renovation"),
        required=False,
        help_text="Utilisation floor of the renovation gate (0.0-1.0); applies to renovations and replacements alike",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.01"}),
    )

    capacity_policy_utilization_window_years = forms.IntegerField(
        label="Utilisation window (years)",
        initial=config_default("capacity_policy.utilization_window_years"),
        required=False,
        help_text="Consecutive recorded years at or below the floor that make a furnace group ineligible",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    capacity_policy_inter_company_swap_cutoff_year = forms.IntegerField(
        label="Inter-company swap cutoff year",
        initial=config_default("capacity_policy.inter_company_swap_cutoff_year"),
        required=False,
        help_text="First year a company may only spend its own credits; blank keeps the default year",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    capacity_policy_no_swap_cutoff = forms.BooleanField(
        label="No cutoff",
        initial=False,
        required=False,
        help_text="Companies keep swapping credits throughout the run",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    capacity_policy_banked_credit_rule = forms.ChoiceField(
        label="Banked credit rule",
        choices=[(rule, rule) for rule in BANKED_CREDIT_RULES],
        initial=config_default("capacity_policy.banked_credit_rule"),
        required=False,
        help_text=(
            "Treatment of pre-cutoff credits from the cutoff on: reassign (stay with their depositor, unowned ones "
            "swept), persist (freely spendable) or expire (unusable)"
        ),
        widget=forms.Select(attrs={"class": "form-select field-connected"}),
    )

    capacity_policy_credit_validity_years = forms.IntegerField(
        label="Credit validity (years)",
        initial=config_default("capacity_policy.credit_validity_years"),
        required=False,
        help_text="Years a banked credit stays usable (a credit of year V through V + N - 1); blank never expires",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    capacity_policy_capacity_pool_max_retry_years = forms.IntegerField(
        label="Maximum retry years",
        initial=config_default("capacity_policy.capacity_pool_max_retry_years"),
        required=False,
        help_text="Blocked years after which a considered greenfield site is discarded",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
    )

    capacity_policy_renovation_counts_as_replace = forms.BooleanField(
        label="Renovation counts as replacement",
        initial=config_default("capacity_policy.renovation_counts_as_replace"),
        required=False,
        help_text=(
            "A same-technology renovation is a full replacement (gate, ratio, shrink, deposit); unticked exempts it "
            "from the ratio alone"
        ),
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    # Demand and Circularity fields
    total_steel_demand_scenario = forms.ChoiceField(
        choices=SCENARIO_CHOICES,
        initial="business_as_usual",
        required=False,
        help_text="[Not yet implemented] Select scenario for total steel demand",
        widget=forms.Select(attrs={"class": "form-select field-not-connected", "disabled": True}),
    )

    green_steel_demand_scenario = forms.ChoiceField(
        choices=SCENARIO_CHOICES,
        initial="business_as_usual",
        required=False,
        help_text="[Not yet implemented] Select scenario for green steel demand",
        widget=forms.Select(attrs={"class": "form-select field-not-connected", "disabled": True}),
    )

    chosen_grid_emissions_scenario = forms.ChoiceField(
        choices=[],
        initial=config_default("chosen_grid_emissions_scenario"),
        required=False,
        help_text="Trajectory of grid electricity emissivity used to compute indirect emissions for each furnace group.",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    # Placeholder for circularity file selection
    CIRCULARITY_CHOICES = [
        ("", "Default circularity data"),
        ("standard_recycling", "Standard Recycling Rates (2023)"),
        ("enhanced_recycling", "Enhanced Recycling Program"),
        ("eu_circular_economy", "EU Circular Economy Package"),
        ("global_best_practice", "Global Best Practice Standards"),
        ("high_scrap_availability", "High Scrap Availability Scenario"),
        ("custom", "Custom Circularity Data"),
    ]

    circularity_file = forms.ChoiceField(
        choices=CIRCULARITY_CHOICES,
        required=False,
        widget=forms.Select(attrs={"class": "form-select field-not-connected", "disabled": True}),
        help_text="[Not yet implemented] Choose a custom circularity data file or upload a new one",
    )

    # Technology fields
    # Adding file selection dropdowns for tech section
    TECHNOLOGY_FILE_CHOICES = [
        ("", "Default settings"),
        ("low_capex", "Low CAPEX Scenario"),
        ("medium_capex", "Medium CAPEX Scenario"),
        ("high_capex", "High CAPEX Scenario"),
        ("custom", "Custom CAPEX Values"),
    ]

    RESOURCE_FILE_CHOICES = [
        ("", "Default resource settings"),
        ("limited_supply", "Limited Supply Scenario"),
        ("abundant_supply", "Abundant Supply Scenario"),
        ("balanced_supply", "Balanced Supply Scenario"),
        ("custom", "Custom Resource Values"),
    ]

    COST_SCENARIO_CHOICES = [
        ("", "Select cost scenario"),
        ("low_cost", "Low Cost"),
        ("medium_cost", "Medium Cost"),
        ("high_cost", "High Cost"),
        ("optimistic", "Optimistic"),
        ("pessimistic", "Pessimistic"),
    ]

    # Technology fields will be dynamically generated based on available technologies
    # from the data preparation instead of being hardcoded here

    hydrogen_subsidies = forms.BooleanField(
        initial=False,
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-not-connected", "disabled": True}),
        help_text="[Not yet implemented] Introduce regional OPEX subsidies for hydrogen use by country/region",
    )

    hydrogen_ceiling_percentile = forms.DecimalField(
        label="Hydrogen ceiling percentile",
        initial=config_default("geo_config.hydrogen_ceiling_percentile"),
        min_value=0.0,
        max_value=100.0,
        max_digits=5,
        decimal_places=1,
        required=False,
        help_text="Percentile of a region's hydrogen cost used as its price cap. 100 means no cap.",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.1"}),
    )

    intraregional_trade_allowed = forms.BooleanField(
        label="Intraregional trade allowed",
        initial=config_default("geo_config.intraregional_trade_allowed"),
        required=False,
        help_text="Allow hydrogen trade between linked regions (e.g., trade between Africa and Western Europe)",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    include_infrastructure_cost = forms.BooleanField(
        label="Include infrastructure cost",
        initial=config_default("geo_config.include_infrastructure_cost"),
        required=False,
        help_text="Include costs for building new rail infrastructure to connect plants",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    include_transport_cost = forms.BooleanField(
        label="Include transport cost",
        initial=config_default("geo_config.include_transport_cost"),
        required=False,
        help_text="Include costs for transporting iron ore and steel between locations",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    # Transportation cost fields (USD/tonne/km) - using GeoConfig dict key names
    iron_mine_to_plant = forms.DecimalField(
        label="Iron mine to iron plant",
        initial=config_default("geo_config.transportation_cost_per_km_per_ton")["iron_mine_to_plant"],
        min_value=0.0,
        max_value=10.0,
        max_digits=6,
        decimal_places=3,
        required=False,
        help_text="Transportation cost for iron ore and pellets",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.001"}),
    )

    iron_to_steel_plant = forms.DecimalField(
        label="Iron plant to steel plant",
        initial=config_default("geo_config.transportation_cost_per_km_per_ton")["iron_to_steel_plant"],
        min_value=0.0,
        max_value=10.0,
        max_digits=6,
        decimal_places=3,
        required=False,
        help_text="Transportation cost for hot metal, pig iron, DRI, HBI",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.001"}),
    )

    steel_to_demand = forms.DecimalField(
        label="Steel plant to demand center",
        initial=config_default("geo_config.transportation_cost_per_km_per_ton")["steel_to_demand"],
        min_value=0.0,
        max_value=10.0,
        max_digits=6,
        decimal_places=3,
        required=False,
        help_text="Transportation cost for liquid steel and steel products",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.001"}),
    )

    include_lulc_cost = forms.BooleanField(
        label="Include land use/land cover cost",
        initial=config_default("geo_config.include_lulc_cost"),
        required=False,
        help_text="Include land use/land cover factors in CAPEX calculations",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input field-connected"}),
    )

    long_dist_pipeline_transport_cost = forms.DecimalField(
        label="Long distance pipeline transport cost",
        initial=config_default("geo_config.long_dist_pipeline_transport_cost"),
        min_value=0.0,
        max_value=10.0,
        max_digits=3,
        decimal_places=1,
        required=False,
        help_text="Cost for long-distance hydrogen pipeline transport",
        widget=forms.NumberInput(attrs={"class": "form-control field-connected", "step": "0.1"}),
    )

    # Cost scenario fields
    esf_cost_scenario = forms.ChoiceField(
        choices=COST_SCENARIO_CHOICES,
        initial="",
        widget=forms.Select(attrs={"class": "form-select"}),
        required=False,
    )

    moe_cost_scenario = forms.ChoiceField(
        choices=COST_SCENARIO_CHOICES,
        initial="",
        widget=forms.Select(attrs={"class": "form-select"}),
        required=False,
    )

    electrowinning_cost_scenario = forms.ChoiceField(
        choices=COST_SCENARIO_CHOICES,
        initial="",
        widget=forms.Select(attrs={"class": "form-select"}),
        required=False,
    )

    ccs_cost_scenario = forms.ChoiceField(
        choices=COST_SCENARIO_CHOICES,
        initial="",
        widget=forms.Select(attrs={"class": "form-select"}),
        required=False,
    )

    # Geospatial section fields - Not yet implemented
    # Power and Hydrogen mix options
    included_power_mix = forms.ChoiceField(
        choices=INCLUDED_POWER_MIX_CHOICES,
        initial=config_default("geo_config.included_power_mix"),
        widget=forms.Select(attrs={"class": "form-select field-connected"}),
        required=False,
        help_text="Select the power mix configuration for the simulation. When combining baseload and grid power, the minimum between the mix (e.g., 85% baseload + 15% grid) and the grid price is selected at each location.",
    )

    # Maximum slope (degrees)
    max_slope = forms.DecimalField(
        initial=config_default("geo_config.max_slope"),
        min_value=0,
        max_value=90,
        decimal_places=1,
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
        help_text="Maximum slope for site selection. Set to 90 or higher to disable the slope filter.",
        required=False,
    )

    # Maximum altitude (m)
    max_altitude = forms.IntegerField(
        initial=int(config_default("geo_config.max_altitude")),
        min_value=0,
        max_value=10000,
        widget=forms.NumberInput(attrs={"class": "form-control field-connected"}),
        help_text="Maximum altitude for site selection. To ignore, set value to 10,000",
        required=False,
    )

    # Maximum absolute latitude (degrees)
    max_latitude = forms.FloatField(
        label="Maximum absolute latitude (°)",
        initial=config_default("geo_config.max_latitude"),
        min_value=0,
        max_value=90,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control field-connected",
                "step": "0.1",
                "data-default": str(config_default("geo_config.max_latitude")),
            }
        ),
        help_text="Maximum absolute latitude — sites limited to |latitude| ≤ this value. To ignore, set value to 90",
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit data_preparation choices to only ready preparations
        ready_preparations = DataPreparation.objects.filter(status=DataPreparation.Status.READY).order_by("-created_at")

        self.fields["data_preparation"].queryset = ready_preparations
        self.fields["data_preparation"].help_text = "Select prepared data to use for this simulation"
        self.fields["data_preparation"].required = True

        # Set default to the most recent ready preparation if creating new instance
        if not self.instance.pk and ready_preparations.exists():
            self.fields["data_preparation"].initial = ready_preparations.first().pk
            # Remove empty option if we have ready preparations
            self.fields["data_preparation"].empty_label = None
        else:
            self.fields["data_preparation"].empty_label = "No data preparations available"

        # Populate grid emissivity scenario choices from the resolved data preparation.
        active_prep = None
        prep_id = (self.data.get("data_preparation") if self.data else None) or self.initial.get("data_preparation")
        if prep_id:
            try:
                active_prep = DataPreparation.objects.get(pk=prep_id, status=DataPreparation.Status.READY)
            except (DataPreparation.DoesNotExist, ValueError, TypeError):
                active_prep = None
        if active_prep is None and ready_preparations.exists():
            active_prep = ready_preparations.first()

        scenarios = active_prep.get_region_emissivity_scenarios() if active_prep is not None else []
        self.fields["chosen_grid_emissions_scenario"].choices = [(s, s) for s in scenarios]
        # Override the field-level class default only when it is missing from the prep
        if scenarios and self.fields["chosen_grid_emissions_scenario"].initial not in scenarios:
            self.fields["chosen_grid_emissions_scenario"].initial = scenarios[0]

        boundaries = active_prep.get_emission_boundaries() if active_prep is not None else []
        boundary_field = self.fields["chosen_emissions_boundary_for_carbon_costs"]
        boundary_field.choices = [(boundary, boundary) for boundary in boundaries]
        if boundaries and boundary_field.initial not in boundaries:
            boundary_field.initial = boundaries[0]

    class Meta:
        model = ModelRun
        fields = [
            # General
            "name",
            # Simulation Parameters
            "randomise_seed",
            "random_seed",
            # Years
            "start_year",
            "end_year",
            # Economic parameters
            "plant_lifetime",
            "global_risk_free_rate",
            "steel_price_buffer",
            "iron_price_buffer",
            "steel_market_clearing_share",
            "iron_market_clearing_share",
            "peg_iron_to_steel_price",
            "iron_to_steel_price_ratio",
            "opening_balance_multiplier",
            "construction_time",
            "consideration_time",
            # Plant Construction Settings
            "probabilistic_agents",
            "probability_of_announcement",
            "probability_of_construction",
            "top_n_loctechs_as_business_op",
            "opportunity_pool_depth",
            "pick_priority_sites_share",
            "calculate_npv_sites_share",
            "expanded_capacity",
            "capacity_limit_iron",
            "capacity_limit_steel",
            "new_capacity_share_from_new_plants",
            # Data Preparation
            "data_preparation",
            # Policy Settings
            "use_iron_ore_premiums",
            "include_tariffs",
            "enable_furnace_group_clustering",
            "geographical_clustering_scope",
            "chosen_grid_emissions_scenario",
            "chosen_emissions_boundary_for_carbon_costs",
            "carbon_border_export_rebates",
            "capacity_policy_enabled",
            "capacity_policy_replacement_ratio",
            "capacity_policy_emission_intense_penalty_divisor",
            "capacity_policy_min_utilization_for_renovation",
            "capacity_policy_utilization_window_years",
            "capacity_policy_inter_company_swap_cutoff_year",
            "capacity_policy_no_swap_cutoff",
            "capacity_policy_banked_credit_rule",
            "capacity_policy_credit_validity_years",
            "capacity_policy_capacity_pool_max_retry_years",
            "capacity_policy_renovation_counts_as_replace",
            # Demand and Circularity
            "total_steel_demand_scenario",
            "green_steel_demand_scenario",
            "circularity_file",
            # Technology fields are now dynamically generated from POST data
            # Technology options
            "hydrogen_subsidies",
            "hydrogen_ceiling_percentile",
            "intraregional_trade_allowed",
            "long_dist_pipeline_transport_cost",
            # Cost scenarios
            "esf_cost_scenario",
            "moe_cost_scenario",
            "electrowinning_cost_scenario",
            "ccs_cost_scenario",
            # Geospatial fields
            "included_power_mix",
            "max_slope",
            "max_altitude",
            "max_latitude",
            "include_infrastructure_cost",
            "include_transport_cost",
            "iron_mine_to_plant",
            "iron_to_steel_plant",
            "steel_to_demand",
            "include_lulc_cost",
        ]
        widgets = {
            "data_preparation": forms.Select(attrs={"class": "form-select field-connected"}),
        }

    @property
    def capacity_policy_levers(self) -> list[forms.BoundField]:
        """The capacity policy's lever fields, shown under its switch.

        Returns:
            The bound ``capacity_policy_*`` fields other than the switch, in declaration order.
        """
        return [
            self[name]
            for name in self.fields
            if name.startswith("capacity_policy_") and name != "capacity_policy_enabled"
        ]

    def clean(self):
        """Validate the years, settle the seed and the clustering scope, and gather the capacity policy.

        Returns:
            The cleaned data; with the policy switch on, the ``capacity_policy_*`` fields replaced by one
            ``capacity_policy`` dictionary of every lever, a blank lever taking its class default.

        Notes:
            A lever set that ``CapacityPolicyConfig`` refuses, the switch on a preparation without the capacity pool
            fixtures, and a credit validity without the switch are form errors.
        """
        cleaned_data = super().clean()
        start_year = cleaned_data.get("start_year")
        end_year = cleaned_data.get("end_year")

        if start_year and end_year:
            if end_year < start_year:
                raise forms.ValidationError("End year must be after start year.")

        # Randomise seed handling: client-side JS draws the seed and writes it into
        # `random_seed` when the box is ticked, so the user sees the value being used.
        # We trust whatever value arrives, falling back to a fresh seed only as a
        # defence-in-depth for clients without JS. `randomise_seed` is a UI-only flag
        # and is not part of SimulationConfig, so it is removed here.
        import secrets

        randomise = cleaned_data.pop("randomise_seed", False)
        if cleaned_data.get("random_seed") is None:
            cleaned_data["random_seed"] = secrets.randbelow(2**31) if randomise else config_default("random_seed")

        # With clustering off the model uses "plant" for hot metal access, so the stored config records that
        if not cleaned_data.get("enable_furnace_group_clustering"):
            cleaned_data["geographical_clustering_scope"] = "plant"

        lever_names = [name for name in self.fields if name.startswith("capacity_policy_")]
        if not any(self.has_error(name) for name in lever_names):
            policy = {name.removeprefix("capacity_policy_"): cleaned_data.pop(name) for name in lever_names}
            no_cutoff = policy.pop("no_swap_cutoff")
            if policy["enabled"]:
                for lever, value in policy.items():
                    if value is None or value == "":
                        policy[lever] = config_default(f"capacity_policy.{lever}")
                    elif isinstance(value, Decimal):
                        policy[lever] = float(value)
                if no_cutoff:
                    policy["inter_company_swap_cutoff_year"] = None
                try:
                    CapacityPolicyConfig(**policy)
                except ValueError as error:
                    self.add_error(None, str(error))
                # Bootstrap refuses an enabled policy without these, so the form says so before the run starts
                if (
                    "data_preparation" in cleaned_data
                    and not cleaned_data["data_preparation"].has_capacity_pool_fixtures()
                ):
                    self.add_error(
                        "capacity_policy_enabled",
                        "The selected data preparation has no capacity pool data; prepare a master holding the "
                        "capacity pool sheets to enable the policy.",
                    )
                cleaned_data["capacity_policy"] = policy
            elif policy["credit_validity_years"] is not None:
                self.add_error(
                    "capacity_policy_credit_validity_years",
                    "Credit validity requires the capacity-replacement policy to be enabled.",
                )

        # Set default values for fields that are not required but need values
        if not cleaned_data.get("total_steel_demand_scenario"):
            cleaned_data["total_steel_demand_scenario"] = "business_as_usual"
        if not cleaned_data.get("green_steel_demand_scenario"):
            cleaned_data["green_steel_demand_scenario"] = "business_as_usual"

        return cleaned_data


class DataPreparationForm(forms.ModelForm):
    """Form for creating data preparations with optional master Excel file validation."""

    # Add a choice field for selecting between upload and existing MasterExcelFile
    master_excel_choice = forms.ChoiceField(
        choices=[
            ("default", "Use default from S3"),
            ("select", "Select existing master Excel file"),
            ("upload", "Upload new master Excel file"),
        ],
        initial="default",
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        help_text="Choose how to provide the master Excel file",
    )

    class Meta:
        model = DataPreparation
        fields = ["name", "core_data_package", "geo_data_package", "master_excel", "master_excel_file"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "core_data_package": forms.Select(attrs={"class": "form-select"}),
            "geo_data_package": forms.Select(attrs={"class": "form-select"}),
            "master_excel": forms.Select(attrs={"class": "form-select"}),
            "master_excel_file": forms.FileInput(attrs={"class": "form-control"}),
        }
        help_texts = {
            "master_excel": "Select an existing master Excel file",
            "master_excel_file": "Or upload a new master Excel file to override default data files. The file will be validated before saving.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Limit master_excel choices to valid files only
        self.fields["master_excel"].queryset = MasterExcelFile.objects.filter(
            validation_status__in=["valid", "warnings"]
        ).order_by("-created_at")
        self.fields["master_excel"].required = False
        self.fields["master_excel_file"].required = False

    def clean_master_excel_file(self):
        """Validate the master Excel file if provided."""
        file = self.cleaned_data.get("master_excel_file")
        if file:
            # Check file extension
            if not file.name.endswith((".xlsx", ".xls")):
                raise forms.ValidationError("Only Excel files (.xlsx or .xls) are allowed.")

            # Check file size (limit to 200MB)
            if file.size > 200 * 1024 * 1024:  # 200MB in bytes
                raise forms.ValidationError("File size cannot exceed 200MB.")

            # Save to temporary location for validation
            with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
                for chunk in file.chunks():
                    tmp.write(chunk)
                tmp_path = Path(tmp.name)

            try:
                # Import here to avoid circular imports
                from steelo.adapters.dataprocessing.master_excel_validator import MasterExcelValidator

                # Validate using MasterExcelValidator
                validator = MasterExcelValidator()
                report = validator.validate_file(tmp_path)

                # Check for critical errors
                if report.has_errors():
                    error_messages = []
                    for error in report.errors[:5]:  # Show first 5 errors
                        error_messages.append(str(error))
                    if len(report.errors) > 5:
                        error_messages.append(f"... and {len(report.errors) - 5} more errors")

                    raise forms.ValidationError(
                        f"Master Excel validation failed with {len(report.errors)} errors:\\n"
                        + "\\n".join(error_messages)
                    )

                # Store validation report for later use
                self.instance.master_excel_validation_report = {
                    "errors": [str(e) for e in report.errors],
                    "warnings": [str(w) for w in report.warnings],
                    "info": [str(i) for i in report.info],
                    "validated_at": datetime.now().isoformat(),
                    "summary": {
                        "error_count": len(report.errors),
                        "warning_count": len(report.warnings),
                        "info_count": len(report.info),
                    },
                }

            except ImportError as e:
                raise forms.ValidationError(f"Could not import validator: {e}")
            except Exception as e:
                raise forms.ValidationError(f"Error validating Excel file: {e}")
            finally:
                # Clean up temporary file
                if tmp_path.exists():
                    tmp_path.unlink()

        return file

    def clean(self):
        """Validate the form based on master_excel_choice."""
        cleaned_data = super().clean()
        choice = cleaned_data.get("master_excel_choice")

        if choice == "select":
            # User wants to select existing file
            if not cleaned_data.get("master_excel"):
                raise forms.ValidationError("Please select an existing master Excel file")
            # Clear the upload field
            cleaned_data["master_excel_file"] = None
        elif choice == "upload":
            # User wants to upload new file
            if not cleaned_data.get("master_excel_file"):
                raise forms.ValidationError("Please upload a master Excel file")
            # Clear the selection field
            cleaned_data["master_excel"] = None
        else:
            # Default - use S3, clear both fields
            cleaned_data["master_excel"] = None
            cleaned_data["master_excel_file"] = None

        return cleaned_data
