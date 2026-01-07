import hashlib
import datetime
import logging
import os
from pathlib import Path
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView
from django.contrib import messages
from django.urls import reverse_lazy, reverse
from django.http import Http404, HttpResponse, FileResponse, HttpResponseNotModified
from django.core.files.base import ContentFile
from django.views.decorators.http import require_http_methods, require_POST
from django.utils import timezone

from django_htmx.http import HttpResponseClientRefresh

from .tasks import run_simulation_task
from .models import ModelRun, ResultImages, MasterExcelFile, DataPackage, DataPreparation, SimulationPlot, Worker
from .forms import ModelRunCreateForm, CircularityDataForm, MasterExcelFileForm
from steelo.core.parse import parse_bool_strict, parse_int_strict
from steelo.validation import SimulationConfigError, validate_technology_settings
from steelo.simulation_types import TechnologySettings
from steelo.data.manager import DataManager


class ModelRunListView(ListView):
    """
    Display a list of all model runs
    """

    model = ModelRun
    template_name = "steeloweb/modelrun_list.html"
    context_object_name = "modelruns"


@require_http_methods(["GET"])
def modelrun_list_fragment(request):
    """
    HTMX endpoint that returns just the model run table rows (not the tbody wrapper).

    IMPORTANT: This must use the same queryset as ModelRunListView to maintain
    consistency in filtering, ordering, and select_related optimizations.
    """
    # Reuse the same queryset as the main list view
    modelruns = ModelRun.objects.all().order_by("-started_at")

    # Future: If ModelRunListView adds select_related() or prefetch_related(),
    # apply the same optimizations here to prevent N+1 queries
    # Example: modelruns = modelruns.select_related('data_preparation')

    return render(request, "steeloweb/partials/_modelrun_table_rows.html", {"modelruns": modelruns})


class ModelRunDetailView(DetailView):
    """
    Display details of a specific model run
    """

    model = ModelRun
    template_name = "steeloweb/modelrun_detail.html"
    context_object_name = "modelrun"

    def get_context_data(self, **kwargs):
        from steeloweb.utils import get_log_file_path

        context = super().get_context_data(**kwargs)

        # Check if there are result images for this model run
        try:
            context["result_images"] = ResultImages.objects.get(modelrun=self.object)
        except ResultImages.DoesNotExist:
            context["result_images"] = None

        # Get technology switches data
        try:
            context["technology_switches"] = self.object.get_technology_switches()
        except FileNotFoundError:
            # Technology switches not available - template will handle this gracefully
            context["technology_switches"] = None

        # Get log file path if available
        context["log_file_path"] = get_log_file_path(self.object.id)

        return context


def run_simulation(request, pk):
    """
    Run a simulation based on a model run configuration
    """
    modelrun = get_object_or_404(ModelRun, pk=pk)

    # Only allow running if the model is in created state
    if modelrun.state != ModelRun.RunState.CREATED:
        messages.error(request, f"Cannot run simulation in {modelrun.state} state")
        return redirect("modelrun-detail", pk=pk)

    # NEW: Check worker availability
    from steeloweb.views_worker import check_worker_availability_for_simulation

    availability = check_worker_availability_for_simulation()

    # Handle critical blocking scenarios
    if availability["status"] == "no_capacity":
        # Clear any stale warning before showing error
        request.session.pop("simulation_warning", None)
        messages.error(request, availability["message"])
        return redirect("modelrun-detail", pk=pk)

    if availability["status"] == "no_workers":
        # Clear any stale warning before showing error
        request.session.pop("simulation_warning", None)
        messages.error(request, availability["message"])
        return redirect("modelrun-detail", pk=pk)

    # Handle "all busy" warning - requires explicit confirmation
    if availability["status"] == "all_busy":
        # Check if user confirmed via POST parameter
        if not request.POST.get("confirm_busy"):
            # Store warning in session for display
            request.session["simulation_warning"] = {
                "message": availability["message"],
                "data": availability["data"],
            }
            # Redirect to show warning (will be displayed on detail page)
            return redirect("modelrun-detail", pk=pk)
        # User confirmed - log that they proceeded despite warning
        import logging

        logger = logging.getLogger(__name__)
        logger.info(
            f"Simulation {pk} started with user confirmation (all workers busy): "
            f"{availability['data']['active_workers']} active, "
            f"{availability['data']['pending_tasks']} pending"
        )

    # Clear any warning from session before proceeding
    # (Handles both confirmed warnings and cases where status changed to 'ok')
    request.session.pop("simulation_warning", None)

    # Update state to running
    modelrun.state = ModelRun.RunState.RUNNING

    # Start the task and store its ID for tracking
    task_result = run_simulation_task.enqueue(modelrun.pk)
    modelrun.task_id = str(task_result.id)
    modelrun.save()

    # Success message to confirm the simulation was queued
    messages.success(request, "Simulation started successfully and added to the queue.")

    return redirect("modelrun-detail", pk=pk)


@require_POST
def rerun_modelrun(request, pk):
    """Reset a failed run that crashed and enqueue it again."""
    modelrun = get_object_or_404(ModelRun, pk=pk)

    if not modelrun.can_rerun:
        messages.error(request, "Only cancelled runs or runs that failed due to an exception can be rerun.")
        return redirect("modelrun-detail", pk=pk)

    modelrun.reset_for_rerun()
    return run_simulation(request, pk)


@require_POST
def dismiss_simulation_warning(request, pk):
    """Dismiss the simulation warning without starting the simulation"""
    # Clear the warning from session
    request.session.pop("simulation_warning", None)
    messages.info(request, "Warning dismissed.")
    return redirect("modelrun-detail", pk=pk)


@require_POST
def dismiss_data_prep_warning(request, pk):
    """Dismiss the data preparation warning without starting preparation"""
    # Clear the warning from session
    request.session.pop(f"data_prep_warning_{pk}", None)
    messages.info(request, "Warning dismissed.")
    return redirect("master-excel-detail", pk=pk)


def cancel_modelrun(request, pk):
    """
    Cancel a running simulation - immediately marks as cancelled since iterations take 15+ minutes
    """
    if request.method != "POST":
        return redirect("modelrun-detail", pk=pk)

    modelrun = get_object_or_404(ModelRun, pk=pk)
    original_state = modelrun.state

    # Only attempt task/worker cleanup for actively running runs
    worker_ids_to_abort: list[str] = []

    if original_state in [ModelRun.RunState.RUNNING, ModelRun.RunState.CANCELLING] and modelrun.task_id:
        try:
            from django_tasks.backends.database.models import DBTaskResult

            task_record = DBTaskResult.objects.filter(id=modelrun.task_id).first()
            if task_record:
                worker_ids_to_abort = list(task_record.worker_ids or [])
                task_record.status = "FAILED"
                task_record.finished_at = timezone.now()
                task_record.exception_class_path = "django_tasks.exceptions.TaskCancelled"
                task_record.traceback = "Task cancelled by user; worker terminated."
                task_record.worker_ids = []
                task_record.save(
                    update_fields=["status", "finished_at", "exception_class_path", "traceback", "worker_ids"]
                )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to mark task %s as cancelled during model run cancel: %s", modelrun.task_id, exc
            )

    # Only allow cancelling if the model is in running state
    if original_state == ModelRun.RunState.RUNNING:
        # Immediately mark as cancelled - don't wait for the worker
        modelrun.state = ModelRun.RunState.CANCELLED
        modelrun.finished_at = timezone.now()
        modelrun.error_message = "Simulation was canceled by user. The background worker was terminated."
        modelrun.task_id = None
        modelrun.save()

        messages.warning(request, "Simulation has been cancelled and the worker was terminated.")
    elif original_state == ModelRun.RunState.CANCELLING:
        # Also handle stuck cancelling state
        modelrun.state = ModelRun.RunState.CANCELLED
        modelrun.finished_at = timezone.now()
        modelrun.error_message = "Simulation was canceled by user. The background worker was terminated."
        modelrun.task_id = None
        modelrun.save()

        messages.warning(request, "Simulation has been cancelled and the worker was terminated.")
    else:
        messages.error(request, f"Cannot cancel simulation in {modelrun.get_state_display()} state")
        return redirect("modelrun-detail", pk=pk)

    # Terminate any workers still associated with this task
    if worker_ids_to_abort:
        from steeloweb.views_worker import _abort_worker

        logger = logging.getLogger(__name__)
        for worker_id in worker_ids_to_abort:
            try:
                _abort_worker(worker_id)
            except Worker.DoesNotExist:
                logger.info("Worker %s already removed when cancelling model run %s", worker_id, modelrun.pk)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to abort worker %s for model run %s: %s", worker_id, modelrun.pk, exc)

    return redirect("modelrun-detail", pk=pk)


def force_stop_modelrun(request, pk):
    """
    Force stop a stuck simulation (when worker has died)
    """
    if request.method != "POST":
        return redirect("modelrun-detail", pk=pk)

    modelrun = get_object_or_404(ModelRun, pk=pk)

    # Allow force stopping for running or cancelling states
    if modelrun.state in [ModelRun.RunState.RUNNING, ModelRun.RunState.CANCELLING]:
        # Always allow force stop, but show a warning if recently updated
        from django.utils import timezone
        from datetime import timedelta

        last_update = modelrun.updated_at
        time_since_update = timezone.now() - last_update

        # Force stop the simulation
        was_cancelling = modelrun.state == ModelRun.RunState.CANCELLING

        modelrun.state = ModelRun.RunState.FAILED
        modelrun.finished_at = timezone.now()

        if was_cancelling:
            modelrun.error_message = (
                "Simulation was force stopped while cancelling. The worker process may have been terminated."
            )
        else:
            modelrun.error_message = (
                "Simulation was force stopped. The worker process may have crashed or been terminated."
            )

        modelrun.save()

        if time_since_update < timedelta(seconds=30):
            messages.warning(
                request,
                f"Simulation has been force stopped. Note: It was last updated {time_since_update.seconds} seconds ago, "
                "so it may have still been running.",
            )
        else:
            messages.warning(request, "Simulation has been force stopped.")
    else:
        messages.error(request, f"Cannot force stop simulation in {modelrun.get_state_display()} state")

    return redirect("modelrun-detail", pk=pk)


def _enrich_technology_defaults(technologies: dict, set_from_year: bool = True) -> None:
    """Enrich technologies dict with default_from_year for UI reset functionality.

    Modifies the technologies dict in place, adding 'default_from_year' field
    to each technology based on whether it's advanced or mature.

    Advanced technologies (H2, CCS, CCU, ESF, MOE, EWIN, SR) get 2030,
    mature technologies keep their existing from_year value.

    Args:
        technologies: Dict of technology configurations to enrich in place
        set_from_year: If True, set from_year to default (for initial load).
                       If False, preserve existing from_year (for validation error path).
    """
    import logging

    logger = logging.getLogger("steeloweb.tech.ui")

    # Define patterns that indicate advanced/emerging technologies
    ADVANCED_TECH_PATTERNS = {"H2", "CCS", "CCU", "ESF", "MOE", "EWIN", "SR"}

    for slug, tech in technologies.items():
        # DEFENSIVE: Get normalized code with fallback for legacy schemas (v1/v2)
        # Use 'or' chain to ensure we always have a non-empty string before .upper()
        normalized_code = tech.get("normalized_code")
        fallback_code = tech.get("code")

        # Pick first non-empty value, guaranteed to have a string
        code = (normalized_code or fallback_code or slug).upper()

        # Log warning only when normalized_code is missing (primary field)
        if not normalized_code:
            logger.warning(
                f"Technology '{slug}' missing normalized_code field (schema v1/v2?), using fallback: '{code}'"
            )

        # Check if this is an advanced technology by pattern matching
        is_advanced = any(pattern in code for pattern in ADVANCED_TECH_PATTERNS)

        # Get existing from_year from data prep
        existing_from_year = tech.get("from_year")

        if is_advanced:
            # Advanced technologies: default to 2030
            # Examples: DRIH2, BFCCS, MOE, EWIN, SR, ESF variants
            default_year = 2030
        else:
            # Mature technologies: preserve existing value from data prep
            # Examples: BF, BOF, DRI, EAF, BFBOF, DRING, DRINGEAF
            if existing_from_year is not None:
                default_year = existing_from_year
            else:
                # Fallback to 2025 if no existing value (shouldn't happen with proper data prep)
                default_year = 2025

        # Always store default for reset functionality
        tech["default_from_year"] = default_year

        # Optionally set from_year to default (for initial load)
        # When False, preserves user-edited values in validation error path
        if set_from_year:
            tech["from_year"] = default_year


def _get_tech_to_product_mapping(prep: DataPreparation) -> dict[str, str]:
    """Get technology-to-product mapping from data preparation.

    Reads product types from technologies.json, which extracts them from
    the Master Excel "Techno-economic details" sheet's Product column.

    Requires schema_version >= 3 with product_type field.
    """
    technologies = prep.get_technologies()

    # Check if technologies is empty (likely due to validation failure)
    if not technologies:
        raise ValueError(
            "Unable to load technology data. This likely means your data preparation is using an outdated format. "
            "Please refresh the data preparation to update to schema version 3, which includes the required 'product_type' field. "
            "If you continue to see this error after refreshing, ensure the Master Excel file has a 'Product' column "
            "in the 'Techno-economic details' sheet with values 'iron' or 'steel' for each technology."
        )

    tech_to_product = {}

    for slug, tech_info in technologies.items():
        normalized_code = tech_info["normalized_code"]

        # Product type is required in schema v3+
        if "product_type" not in tech_info:
            raise ValueError(
                f"Technology {normalized_code} missing required product_type field. "
                "Please refresh data preparation with latest Master Excel. "
                "The Product column in 'Techno-economic details' sheet must be filled for all technologies."
            )

        product_type = tech_info["product_type"]
        if product_type not in ["iron", "steel"]:
            raise ValueError(
                f"Invalid product_type '{product_type}' for technology {normalized_code}. "
                "Must be 'iron' or 'steel' in the Excel sheet."
            )

        tech_to_product[normalized_code] = product_type

    return tech_to_product


def _merge_posted_technology_values(request, technologies: dict) -> None:
    """Merge posted form values into technologies dict to preserve user input on validation errors.

    Modifies technologies dict in place, updating 'allowed', 'from_year', and 'to_year' fields
    based on values from request.POST.

    Args:
        request: Django request containing POST data
        technologies: Dict of technology configurations to update in place
    """
    # Build set of allowed checkboxes from POST data
    # HTML checkboxes are only present in POST data when checked
    posted_allowed_techs = set()
    for key in request.POST.keys():
        if key.startswith("tech_") and key.endswith("_allowed"):
            slug = key[5:-8]  # Extract slug from "tech_{slug}_allowed"
            posted_allowed_techs.add(slug)

    # Merge posted values for each technology
    for slug, tech_info in technologies.items():
        # Checkbox: preserve exact user selection
        # If checkbox was posted, it was checked; otherwise it was unchecked
        tech_info["allowed"] = slug in posted_allowed_techs

        # Year values: preserve if posted
        posted_from_year = request.POST.get(f"tech_{slug}_from_year")
        if posted_from_year:
            try:
                tech_info["from_year"] = int(posted_from_year)
            except (ValueError, TypeError):
                pass  # Keep default if invalid

        posted_to_year = request.POST.get(f"tech_{slug}_to_year")
        if posted_to_year:
            try:
                tech_info["to_year"] = int(posted_to_year) if posted_to_year else None
            except (ValueError, TypeError):
                tech_info["to_year"] = None
        elif posted_to_year == "":
            tech_info["to_year"] = None


def _build_validation_error_context(request, form, data_preparation: DataPreparation) -> dict:
    """Build context for validation error re-render with preserved user input.

    Creates context dict containing form, technologies with merged user input,
    and any structured validation errors.

    Args:
        request: Django request containing POST data
        form: Form instance (may have technology_validation_errors attribute)
        data_preparation: DataPreparation instance to load technologies from

    Returns:
        Dict with 'form', 'technologies', 'selected_preparation', and optionally
        'technology_validation_errors' keys
    """
    import copy

    # Load and deep copy technology data to avoid mutating cached data
    technologies_orig = data_preparation.get_technologies()
    technologies = copy.deepcopy(technologies_orig)

    # Merge ALL posted values (checkboxes, years) to preserve user input
    _merge_posted_technology_values(request, technologies)

    # Apply UI metadata without overriding user values
    # set_from_year=False preserves user-edited from_year values
    _enrich_technology_defaults(technologies, set_from_year=False)

    # Build base context
    context = {
        "form": form,
        "technologies": technologies,
        "selected_preparation": data_preparation,
    }

    # Add structured errors if they exist (from coverage validation)
    if hasattr(form, "technology_validation_errors"):
        context["technology_validation_errors"] = [
            {
                "title": error.title,
                "description": error.description,
                "suggestions": error.suggestions,
                "product_type": error.product_type,
            }
            for error in form.technology_validation_errors
        ]

    return context


def _extract_technology_settings(request, prep: DataPreparation, form) -> dict:
    """Build technology_settings from POST data."""
    from steelo.validation import ValidationError

    # Initialize structured errors list
    if not hasattr(form, "technology_validation_errors"):
        form.technology_validation_errors = []

    # Build technology_settings from POST data
    technologies = prep.get_technologies()
    settings = {}

    for slug, tech_info in technologies.items():
        # Use normalized_code as the key!
        normalized_code = tech_info["normalized_code"]
        name = tech_info["display_name"]

        # Parse with strict validation
        try:
            # Checkbox: absent = None, which we default to False
            allowed = parse_bool_strict(
                request.POST.get(f"tech_{slug}_allowed"),
                default=False,  # unchecked = False
            )
            from_year = parse_int_strict(request.POST.get(f"tech_{slug}_from_year"), required=True, lo=2020, hi=2100)
            to_year_str = request.POST.get(f"tech_{slug}_to_year")
            to_year = None
            if to_year_str and to_year_str != "":
                to_year = parse_int_strict(to_year_str, required=False, lo=2020, hi=2100)

            if to_year and to_year < from_year:
                raise ValueError("End year before start year")

            # Key by normalized_code!
            settings[normalized_code] = {"allowed": allowed, "from_year": from_year, "to_year": to_year}
        except ValueError as e:
            # Convert parsing error to structured format
            form.technology_validation_errors.append(
                ValidationError(
                    title=f"{name} Configuration Error",
                    description=str(e),
                    suggestions=[],
                    product_type=None,
                )
            )

    # Check for parsing errors before proceeding
    if form.technology_validation_errors:
        return {}

    # Validate against repository and scenario horizon
    try:
        # Get available codes from preparation's technologies
        available_codes = {t["normalized_code"] for t in technologies.values()}

        # Check for collisions before proceeding
        codes = [t["normalized_code"] for t in technologies.values()]
        dupes = {c for c in codes if codes.count(c) > 1}
        if dupes:
            # Convert duplicate codes error to structured format
            form.technology_validation_errors.append(
                ValidationError(
                    title="Duplicate Technology Codes",
                    description=f"Duplicate technology codes found: {', '.join(sorted(dupes))}",
                    suggestions=[],
                    product_type=None,
                )
            )
            return {}

        # Convert to TechnologySettings for validation
        tech_map = {k: TechnologySettings(**v) for k, v in settings.items()}

        # Validate with scenario years
        validate_technology_settings(
            tech_map, available_codes, year_min=form.cleaned_data["start_year"], year_max=form.cleaned_data["end_year"]
        )
    except SimulationConfigError as e:
        # Convert horizon validation error to structured format
        form.technology_validation_errors.append(
            ValidationError(
                title="Technology Availability Issue",
                description=str(e),
                suggestions=[],
                product_type=None,
            )
        )
        return {}

    # NEW: Enhanced product coverage validation
    from steelo.validation import check_product_coverage_enhanced

    # Get simulation timeframe from validated form data
    start_year = form.cleaned_data.get("start_year", 2025)
    end_year = form.cleaned_data.get("end_year", 2050)

    # Get tech-to-product mapping and technology data
    try:
        tech_to_product = _get_tech_to_product_mapping(prep)
    except ValueError as e:
        # Convert schema error to structured format
        form.technology_validation_errors.append(
            ValidationError(
                title="Data Schema Issue",
                description=str(e),
                suggestions=[],
                product_type=None,
            )
        )
        return {}

    technologies_data = prep.get_technologies()

    # Enhanced validation with user-friendly error data
    validation_result = check_product_coverage_enhanced(
        {k: TechnologySettings(**v) for k, v in settings.items()},
        tech_to_product,
        technologies_data,
        start_year,
        end_year,
    )

    if not validation_result.is_valid:
        # Extend structured errors for contextual display (append coverage errors to any existing errors)
        form.technology_validation_errors.extend(validation_result.errors)
        return {}

    return settings


def create_modelrun(request):
    """
    Create a new model run with custom configuration
    """
    if request.method == "POST":
        form = ModelRunCreateForm(request.POST)
        if form.is_valid():
            # Create config dictionary from form data
            output_file = str(settings.BASE_DIR / "django_pam_simulation_run.json")
            config = {
                "start_year": form.cleaned_data["start_year"],
                "end_year": form.cleaned_data["end_year"],
                "plant_lifetime": form.cleaned_data.get("plant_lifetime", 20),
                "global_risk_free_rate": float(form.cleaned_data.get("global_risk_free_rate") or 0.0209),
                "steel_price_buffer": float(
                    form.cleaned_data.get("steel_price_buffer")
                    if form.cleaned_data.get("steel_price_buffer") is not None
                    else 200.0
                ),
                "iron_price_buffer": float(
                    form.cleaned_data.get("iron_price_buffer")
                    if form.cleaned_data.get("iron_price_buffer") is not None
                    else 200.0
                ),
                "construction_time": form.cleaned_data.get("construction_time", 4),
                "probabilistic_agents": form.cleaned_data.get("probabilistic_agents", True),
                "probability_of_announcement": float(form.cleaned_data.get("probability_of_announcement") or 0.7),
                "probability_of_construction": float(form.cleaned_data.get("probability_of_construction") or 0.9),
                "top_n_loctechs_as_business_op": form.cleaned_data.get("top_n_loctechs_as_business_op", 15),
                "priority_pct": int(form.cleaned_data.get("priority_pct") or 5),
                # Plant capacity parameters (convert Mt to t)
                "expanded_capacity": float(form.cleaned_data.get("expanded_capacity") or 2.5) * 1000000,
                "capacity_limit_iron": float(form.cleaned_data.get("capacity_limit_iron") or 100) * 1000000,
                "capacity_limit_steel": float(form.cleaned_data.get("capacity_limit_steel") or 100) * 1000000,
                "new_capacity_share_from_new_plants": float(
                    form.cleaned_data.get("new_capacity_share_from_new_plants")
                    if form.cleaned_data.get("new_capacity_share_from_new_plants") is not None
                    else 0.4
                ),
                "hydrogen_ceiling_percentile": float(form.cleaned_data.get("hydrogen_ceiling_percentile") or 20.0),
                "intraregional_trade_allowed": form.cleaned_data.get("intraregional_trade_allowed", True),
                "long_dist_pipeline_transport_cost": float(
                    form.cleaned_data.get("long_dist_pipeline_transport_cost") or 1.0
                ),
                # Policy settings
                "use_iron_ore_premiums": form.cleaned_data.get("use_iron_ore_premiums", True),
                "green_steel_emissions_limit": 0.4,  # Hardcoded - no longer user-configurable
                "include_tariffs": form.cleaned_data.get("include_tariffs", True),
                "output_file": output_file,
                # Add new demand and circularity fields
                "total_steel_demand_scenario": form.cleaned_data.get(
                    "total_steel_demand_scenario", "business_as_usual"
                ),
                "green_steel_demand_scenario": form.cleaned_data.get(
                    "green_steel_demand_scenario", "business_as_usual"
                ),
                "scrap_generation_scenario": form.cleaned_data.get("scrap_generation_scenario", "business_as_usual"),
                "circularity_file": form.cleaned_data.get("circularity_file", ""),
                # Technology fields will be added after extraction from POST data
                "hydrogen_subsidies": form.cleaned_data.get("hydrogen_subsidies", False),
                "esf_cost_scenario": form.cleaned_data.get("esf_cost_scenario", ""),
                "moe_cost_scenario": form.cleaned_data.get("moe_cost_scenario", ""),
                "electrowinning_cost_scenario": form.cleaned_data.get("electrowinning_cost_scenario", ""),
                "ccs_cost_scenario": form.cleaned_data.get("ccs_cost_scenario", ""),
                # Geospatial fields
                "included_power_mix": form.cleaned_data.get("included_power_mix", "85% baseload + 15% grid"),
                "max_slope": float(form.cleaned_data.get("max_slope") or 2.0),
                "max_altitude": form.cleaned_data.get("max_altitude", 1500),
                "max_latitude": float(
                    form.cleaned_data.get("max_latitude") if form.cleaned_data.get("max_latitude") is not None else 70.0
                ),
                "include_infrastructure_cost": form.cleaned_data.get("include_infrastructure_cost", True),
                "include_transport_cost": form.cleaned_data.get("include_transport_cost", True),
                "include_lulc_cost": form.cleaned_data.get("include_lulc_cost", True),
                # Transportation cost fields - assembled into the expected dictionary structure
                "transportation_cost_per_km_per_ton": {
                    "iron_mine_to_plant": float(
                        form.cleaned_data.get("iron_mine_to_plant")
                        if form.cleaned_data.get("iron_mine_to_plant") is not None
                        else 0.013
                    ),
                    "iron_to_steel_plant": float(
                        form.cleaned_data.get("iron_to_steel_plant")
                        if form.cleaned_data.get("iron_to_steel_plant") is not None
                        else 0.015
                    ),
                    "steel_to_demand": float(
                        form.cleaned_data.get("steel_to_demand")
                        if form.cleaned_data.get("steel_to_demand") is not None
                        else 0.019
                    ),
                },
            }

            # Extract technology settings from POST data if data preparation is available
            data_preparation = form.cleaned_data.get("data_preparation")
            if data_preparation:
                technology_settings = _extract_technology_settings(request, data_preparation, form)
                if not technology_settings:  # Validation failed
                    # ALWAYS build context with preserved user input, regardless of error type
                    # This ensures state preservation for ALL validation errors:
                    # - Horizon violations, parse errors, etc. (from validate_technology_settings)
                    # - Coverage errors (from check_product_coverage_enhanced)
                    context = _build_validation_error_context(request, form, data_preparation)
                    return render(request, "steeloweb/create_modelrun.html", context)
                config["technology_settings"] = technology_settings
            else:
                messages.error(request, "Data preparation is required to configure technologies")
                return render(request, "steeloweb/create_modelrun.html", {"form": form})

            # Remove old technology fields that might have been added
            LEGACY_PREFIXES = ("bf_", "bof_", "dri_", "eaf_", "esf_", "moe_", "electrowinning_", "bf_ccs_")
            for key in list(config.keys()):
                if any(key.startswith(p) for p in LEGACY_PREFIXES):
                    del config[key]

            # Create the model run
            modelrun = ModelRun.objects.create(
                name=form.cleaned_data.get("name", ""),
                config=config,
                data_preparation=data_preparation,
            )

            messages.success(request, "Model run created successfully")
            return redirect("modelrun-detail", pk=modelrun.pk)
        else:
            # Form is invalid (general validation: missing name, invalid years, etc.)
            messages.error(request, "Please correct the errors below")

            # Check if user had selected a data preparation with technology configuration
            # If so, preserve their technology selections on re-render
            data_preparation_id = request.POST.get("data_preparation")
            if data_preparation_id:
                try:
                    data_preparation = DataPreparation.objects.get(
                        pk=data_preparation_id, status=DataPreparation.Status.READY
                    )
                    # Build context with preserved technology selections
                    context = _build_validation_error_context(request, form, data_preparation)
                    return render(request, "steeloweb/create_modelrun.html", context)
                except (ValueError, DataPreparation.DoesNotExist):
                    # Invalid ID format or data preparation not found/not ready
                    # Render without technology data
                    pass

            # No data preparation selected or not found, render form without technology data
            return render(request, "steeloweb/create_modelrun.html", {"form": form})
    else:
        # GET request shows the form
        form = ModelRunCreateForm()

    return render(request, "steeloweb/create_modelrun.html", {"form": form})


@require_http_methods(["GET"])
def technologies_fragment(request):
    """
    HTMX endpoint: returns just the technology table for a given data preparation.

    Always returns 200 with valid HTML to avoid HTMX error states.
    Shows friendly messages for missing/invalid preparations.
    Supports HTTP caching with ETag/304 responses.
    """
    from django.template.loader import render_to_string
    from django.http import HttpResponse

    prep_id = request.GET.get("data_preparation")

    if not prep_id:
        # Return empty state with friendly message (200, not 400)
        html = render_to_string(
            "steeloweb/partials/_technology_table.html",
            {
                "technologies": {},
                "selected_preparation": None,
                "error_message": "Please select a data preparation to view technologies.",
            },
            request=request,
        )
        return HttpResponse(html, status=200)

    try:
        prep = DataPreparation.objects.get(pk=prep_id, status=DataPreparation.Status.READY)
    except DataPreparation.DoesNotExist:
        # Return friendly error state (200, not 400)
        html = render_to_string(
            "steeloweb/partials/_technology_table.html",
            {
                "technologies": {},
                "selected_preparation": None,
                "error_message": "The selected data preparation is not available or not ready.",
            },
            request=request,
        )
        return HttpResponse(html, status=200)

    # Check for HTTP caching with ETag (post-auth)
    from pathlib import Path

    tech_path = Path(prep.data_directory) / "data" / "fixtures" / "technologies.json"

    if tech_path.exists():
        st = tech_path.stat()
        etag_val = hashlib.md5(f"{st.st_mtime_ns}:{st.st_size}:{prep.id}".encode()).hexdigest()

        # Normalize client ETag (strip quotes) for comparison
        client_tag = (request.headers.get("If-None-Match", "") or "").strip('"')

        # Return 304 if ETag matches
        if client_tag == etag_val:
            resp = HttpResponseNotModified()
            resp["ETag"] = f'"{etag_val}"'
            resp["Cache-Control"] = "private, no-cache"
            resp["Vary"] = "HX-Request, Cookie"
            return resp

    technologies = prep.get_technologies()  # Uses validated Pydantic models

    # IMPORTANT: Deep copy to avoid mutating the cached data
    # The view needs to apply UI-specific defaults without corrupting the canonical cached data
    import copy

    technologies = copy.deepcopy(technologies)

    # Sort technologies alphabetically by display name for better UX
    technologies = dict(sorted(technologies.items(), key=lambda x: x[1].get("display_name", "").lower()))

    # Log if no technologies found
    import logging

    logger = logging.getLogger("steeloweb.tech.ui")
    if not technologies:
        logger.warning("No technologies found for data preparation %s (%s)", prep.id, prep.name)

    # Set default availability years and reset metadata
    # set_from_year=True for initial load (sets both from_year and default_from_year)
    _enrich_technology_defaults(technologies, set_from_year=True)

    context = {
        "technologies": technologies,
        "selected_preparation": prep,
    }

    if tech_path.exists():
        # Normal render with cache headers
        response = render(request, "steeloweb/partials/_technology_table.html", context)
        response["ETag"] = f'"{etag_val}"'  # Always set with quotes
        response["Last-Modified"] = datetime.datetime.utcfromtimestamp(st.st_mtime).strftime(
            "%a, %d %b %Y %H:%M:%S GMT"
        )
        response["Cache-Control"] = "private, no-cache"
        response["Vary"] = "HX-Request, Cookie"
        response["HX-Push-Url"] = f"{reverse('create-modelrun')}?data_preparation={prep.id}"
    else:
        # No file - render without cache headers
        response = render(request, "steeloweb/partials/_technology_table.html", context)
        # No cache headers when file doesn't exist

    return response


@require_http_methods(["GET"])
def dataset_metadata_fragment(request):
    """
    HTMX endpoint: returns dataset metadata information for a given data preparation.

    Displays metadata from plants_metadata.json including plant_lifetime_used,
    data_reference_year, and schema version.
    """
    from django.template.loader import render_to_string
    from django.http import HttpResponse
    from pathlib import Path
    import json

    prep_id = request.GET.get("data_preparation")

    if not prep_id:
        html = render_to_string(
            "steeloweb/partials/_dataset_metadata.html",
            {
                "has_metadata": False,
                "selected_preparation": None,
                "message": "Please select a data preparation to view dataset information.",
            },
            request=request,
        )
        return HttpResponse(html, status=200)

    try:
        prep = DataPreparation.objects.get(pk=prep_id, status=DataPreparation.Status.READY)
    except DataPreparation.DoesNotExist:
        html = render_to_string(
            "steeloweb/partials/_dataset_metadata.html",
            {
                "has_metadata": False,
                "selected_preparation": None,
                "message": "The selected data preparation is not available or not ready.",
            },
            request=request,
        )
        return HttpResponse(html, status=200)

    # Load metadata from plants_metadata.json
    metadata_path = Path(prep.data_directory) / "data" / "fixtures" / "plants_metadata.json"

    context = {
        "selected_preparation": prep,
        "has_metadata": False,
    }

    if metadata_path.exists():
        try:
            with open(metadata_path, "r") as f:
                metadata_data = json.load(f)

            context.update(
                {
                    "has_metadata": True,
                    "schema_version": metadata_data.get("schema_version"),
                    "plant_lifetime_used": metadata_data.get("metadata", {}).get("plant_lifetime_used"),
                    "data_reference_year": metadata_data.get("metadata", {}).get("data_reference_year"),
                    "generated_at": metadata_data.get("metadata", {}).get("generated_at"),
                    "furnace_group_count": len(metadata_data.get("furnace_groups", {})),
                }
            )
        except (json.JSONDecodeError, KeyError) as e:
            context["message"] = f"Error reading metadata file: {e}"
    else:
        context["message"] = "No metadata file found. This dataset uses legacy mode (plant_lifetime=20 only)."

    return render(request, "steeloweb/partials/_dataset_metadata.html", context)


def get_modelrun_progress(request, pk):
    """
    HTMX endpoint to get the current progress of a model run
    """
    modelrun = get_object_or_404(ModelRun, pk=pk)

    # Check if task crashed and mark as failed if needed
    modelrun.mark_as_failed_if_stuck()

    # If simulation just finished, reload page to show results
    if modelrun.is_finished:
        return HttpResponseClientRefresh()

    context = {"modelrun": modelrun}
    return render(request, "steeloweb/includes/progress_bar.html", context)


def view_cost_map(request, pk, map_type):
    """
    View for displaying cost maps (LCOE or LCOH)
    """
    modelrun = get_object_or_404(ModelRun, pk=pk)

    try:
        result_images = ResultImages.objects.get(modelrun=modelrun)

        if map_type == "lcoe":
            image = result_images.lcoe_map
            title = "Levelized Cost of Electricity (LCOE)"
        elif map_type == "lcoh":
            image = result_images.lcoh_map
            title = "Levelized Cost of Hydrogen (LCOH)"
        else:
            raise Http404("Invalid map type")

        if not image:
            messages.error(
                request,
                f"{title} not available for this model run. "
                f"This may occur if plot generation failed or certain configuration options were selected.",
            )
            return redirect("modelrun-detail", pk=pk)

        context = {"modelrun": modelrun, "image": image, "title": title, "map_type": map_type}
        return render(request, "steeloweb/result_map.html", context)
    except ResultImages.DoesNotExist:
        messages.error(request, "No result images available for this model run")
        return redirect("modelrun-detail", pk=pk)


def view_priority_map(request, pk, map_type):
    """
    View for displaying priority location maps (iron or steel)
    """
    modelrun = get_object_or_404(ModelRun, pk=pk)

    try:
        result_images = ResultImages.objects.get(modelrun=modelrun)

        if map_type == "iron":
            image = result_images.priority_locations_iron
            title = "Top 5% Priority Locations for Iron Production"
        elif map_type == "steel":
            image = result_images.priority_locations_steel
            title = "Top 5% Priority Locations for Steel Production"
        else:
            raise Http404("Invalid map type")

        if not image:
            messages.error(
                request,
                f"{title} not available for this model run. "
                f"This may occur if plot generation failed or certain configuration options were selected.",
            )
            return redirect("modelrun-detail", pk=pk)

        context = {"modelrun": modelrun, "image": image, "title": title, "map_type": map_type}
        return render(request, "steeloweb/result_map.html", context)
    except ResultImages.DoesNotExist:
        messages.error(request, "No result images available for this model run")
        return redirect("modelrun-detail", pk=pk)


def view_plant_visualization(request, pk, visualization_type):
    """
    View for displaying various plant visualizations (construction maps, status charts)
    """
    modelrun = get_object_or_404(ModelRun, pk=pk)

    try:
        result_images = ResultImages.objects.get(modelrun=modelrun)

        # Mapping of visualization types to model fields and titles
        visualization_mapping = {
            "iron-construction": {
                "field": "new_plants_iron_construction",
                "title": "Iron Plants Operating Map",
            },
            "steel-construction": {
                "field": "new_plants_steel_construction",
                "title": "Steel Plants Operating Map",
            },
            "iron-status": {"field": "new_plants_iron_status", "title": "New Iron Plants by Status"},
            "steel-status": {"field": "new_plants_steel_status", "title": "New Steel Plants by Status"},
        }

        if visualization_type not in visualization_mapping:
            raise Http404("Invalid visualization type")

        viz_info = visualization_mapping[visualization_type]
        image = getattr(result_images, viz_info["field"])

        if not image:
            messages.error(request, f"No {viz_info['title']} visualization available for this model run")
            return redirect("modelrun-detail", pk=pk)

        context = {
            "modelrun": modelrun,
            "image": image,
            "title": viz_info["title"],
            "visualization_type": visualization_type,
        }
        return render(request, "steeloweb/result_map.html", context)
    except ResultImages.DoesNotExist:
        messages.error(request, "No result images available for this model run")
        return redirect("modelrun-detail", pk=pk)


def upload_circularity_data(request, modelrun_id=None):
    """
    Upload circularity data file
    """
    if request.method == "POST":
        form = CircularityDataForm(request.POST, request.FILES)
        if form.is_valid():
            # This is just a placeholder - in a real implementation, we would save the file
            # and create a database record for it
            messages.success(request, "Circularity data uploaded successfully")

            # Redirect to appropriate page
            if modelrun_id:
                return redirect("modelrun-detail", pk=modelrun_id)
            return redirect("modelrun-list")
        else:
            messages.error(request, "Please correct the errors below")
    else:
        form = CircularityDataForm()

    # Determine the context based on whether we're in a modelrun context
    context = {"form": form}
    if modelrun_id:
        context["modelrun"] = get_object_or_404(ModelRun, pk=modelrun_id)

    return render(request, "steeloweb/upload_circularity.html", context)


def view_simulation_plot(request, pk, plot_id):
    """
    View for displaying individual simulation plots in full size
    """
    modelrun = get_object_or_404(ModelRun, pk=pk)
    plot = get_object_or_404(modelrun.simulation_plots, pk=plot_id)

    context = {
        "modelrun": modelrun,
        "plot": plot,
        "image": plot.image,
        "title": plot.title,
    }
    return render(request, "steeloweb/simulation_plot.html", context)


def download_modelrun_csv(request, pk):
    """
    Download the CSV results file for a model run
    """
    modelrun = get_object_or_404(ModelRun, pk=pk)

    if not modelrun.result_csv:
        raise Http404("No CSV results available for this model run")

    try:
        # Get the file content
        file_content = modelrun.result_csv.read()

        # Create response with proper headers
        response = HttpResponse(file_content, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="modelrun_{modelrun.id}_results.csv"'

        return response

    except Exception as e:
        messages.error(request, f"Error downloading CSV: {str(e)}")
        return redirect("modelrun-detail", pk=pk)


class ModelRunDeleteView(DeleteView):
    """Delete a model run and all its associated data"""

    model = ModelRun
    template_name = "steeloweb/modelrun_confirm_delete.html"
    success_url = reverse_lazy("modelrun-list")

    def dispatch(self, request, *args, **kwargs):
        """Check if model run can be deleted before processing request"""
        self.object = self.get_object()

        # Prevent deletion of running or cancelling simulations
        if self.object.state in [ModelRun.RunState.RUNNING, ModelRun.RunState.CANCELLING]:
            messages.error(
                request,
                f"Cannot delete a {self.object.get_state_display().lower()} simulation. Please wait for it to complete.",
            )
            return redirect("modelrun-detail", pk=self.object.pk)

        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """Add additional context about what will be deleted"""
        context = super().get_context_data(**kwargs)

        # Count related objects that will be deleted
        context["result_images_count"] = ResultImages.objects.filter(modelrun=self.object).count()
        context["simulation_plots_count"] = SimulationPlot.objects.filter(modelrun=self.object).count()
        context["has_results"] = bool(self.object.results) or bool(self.object.result_csv)

        return context

    def post(self, request, *args, **kwargs):
        """Handle the deletion with appropriate messaging"""
        self.object = self.get_object()

        # Store info for success message before deletion
        modelrun_name = self.object.name or f"Model Run #{self.object.id}"

        # Delete the object (this will cascade to related objects)
        response = self.delete(request, *args, **kwargs)

        messages.success(request, f'Model run "{modelrun_name}" and all associated data have been deleted.')

        return response


def download_master_excel_template(request):
    """
    Download the master Excel template from S3
    """
    try:
        # Import here to avoid circular imports
        from steelo.data.manager import DataManager

        # Use DataManager to get the master Excel from S3
        manager = DataManager()

        # Always download fresh from S3 to ensure latest version
        try:
            # Download the master-input package with force=True to bypass cache
            manager.download_package("master-input", force=True)
            cache_path = manager.get_package_path("master-input")

            if cache_path and cache_path.exists():
                # Look for the Excel file - according to manifest.json it's "master_input.xlsx"
                excel_source = cache_path / "master_input.xlsx"

                if excel_source.exists():
                    # Directly return the file from S3 without any caching
                    return FileResponse(
                        open(excel_source, "rb"), as_attachment=True, filename="master_input_template.xlsx"
                    )
                else:
                    messages.error(request, "Master Excel file not found in S3 package")
                    return redirect("master-excel-list")
            else:
                messages.error(request, "Failed to download master Excel package from S3")
                return redirect("master-excel-list")

        except Exception as e:
            messages.error(request, f"Error downloading from S3: {str(e)}")
            return redirect("master-excel-list")

    except Exception as e:
        messages.error(request, f"Error downloading template: {str(e)}")
        return redirect("master-excel-list")


class MasterExcelFileListView(ListView):
    """Display a list of all master Excel files"""

    model = MasterExcelFile
    template_name = "steeloweb/master_excel_list.html"
    context_object_name = "master_excel_files"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Separate example and user files (no templates since they're always fresh from S3)
        context["template_files"] = []  # No longer storing templates in database
        context["example_files"] = MasterExcelFile.objects.filter(is_example=True, is_template=False).order_by("name")
        context["user_files"] = MasterExcelFile.objects.filter(is_example=False, is_template=False).order_by(
            "-created_at"
        )

        # Get IDs of MasterExcelFiles that already have DataPreparations
        context["files_with_preparations"] = set(
            DataPreparation.objects.exclude(master_excel=None).values_list("master_excel_id", flat=True)
        )

        return context


class MasterExcelFileCreateView(CreateView):
    """Create a new master Excel file"""

    model = MasterExcelFile
    form_class = MasterExcelFileForm
    template_name = "steeloweb/master_excel_form.html"
    success_url = reverse_lazy("master-excel-list")

    def form_valid(self, form):
        response = super().form_valid(form)
        # Validate the file after saving
        self.object.validate()

        if self.object.validation_status == "invalid":
            messages.error(self.request, f"Master Excel file '{self.object.name}' has validation errors")
        elif self.object.validation_status == "warnings":
            messages.warning(self.request, f"Master Excel file '{self.object.name}' has validation warnings")
        else:
            messages.success(
                self.request, f"Master Excel file '{self.object.name}' uploaded and validated successfully"
            )

        return response


class MasterExcelFileDetailView(DetailView):
    """Display details of a specific master Excel file"""

    model = MasterExcelFile
    template_name = "steeloweb/master_excel_detail.html"
    context_object_name = "master_excel"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Check if there's an existing data preparation for this master Excel
        context["existing_preparation"] = DataPreparation.objects.filter(master_excel=self.object).first()

        # Add data preparation warning if it exists in session
        warning_key = f"data_prep_warning_{self.object.pk}"
        context["data_prep_warning"] = self.request.session.get(warning_key)

        return context


class MasterExcelFileUpdateView(UpdateView):
    """Update an existing master Excel file"""

    model = MasterExcelFile
    form_class = MasterExcelFileForm
    template_name = "steeloweb/master_excel_form.html"

    def get_success_url(self):
        return reverse_lazy("master-excel-detail", kwargs={"pk": self.object.pk})

    def dispatch(self, request, *args, **kwargs):
        """Check if file can be edited before processing request"""
        self.object = self.get_object()

        # Check if any DataPreparation using this file has active ModelRuns
        for prep in self.object.data_preparations.all():
            if prep.model_runs.exists():
                messages.error(
                    request,
                    f"Cannot edit: This file is used by {prep.model_runs.count()} model run(s) "
                    f"through data preparation '{prep.name}'.",
                )
                return redirect("master-excel-detail", pk=self.object.pk)

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        # Check if file was changed
        if "file" in form.changed_data:
            # Re-validate if file changed
            self.object.validation_status = "pending"
            self.object.validation_report = {}

        response = super().form_valid(form)

        # Validate if file was changed
        if "file" in form.changed_data:
            self.object.validate()

            if self.object.validation_status == "invalid":
                messages.error(self.request, f"Master Excel file '{self.object.name}' has validation errors")
            elif self.object.validation_status == "warnings":
                messages.warning(self.request, f"Master Excel file '{self.object.name}' has validation warnings")
            else:
                messages.success(
                    self.request, f"Master Excel file '{self.object.name}' updated and validated successfully"
                )
        else:
            messages.success(self.request, f"Master Excel file '{self.object.name}' updated successfully")

        return response


def download_master_excel_file(request, pk):
    """Download a master Excel file"""
    master_excel = get_object_or_404(MasterExcelFile, pk=pk)

    try:
        file_path = master_excel.get_file_path()
        if not file_path.exists():
            raise Http404("File not found")

        return FileResponse(open(file_path, "rb"), as_attachment=True, filename=f"{master_excel.name}.xlsx")
    except Exception as e:
        messages.error(request, f"Error downloading file: {str(e)}")
        return redirect("master-excel-detail", pk=pk)


def prepare_data_with_master_excel(request, pk):
    """Create a data preparation using a specific master Excel file"""
    master_excel = get_object_or_404(MasterExcelFile, pk=pk)

    # Check if file is valid
    if master_excel.validation_status not in ["valid", "warnings"]:
        messages.error(
            request, f"Cannot use '{master_excel.name}' - validation status is {master_excel.validation_status}"
        )
        return redirect("master-excel-detail", pk=pk)

    # Check worker availability before proceeding
    from steeloweb.views_worker import check_worker_availability
    import logging

    logger = logging.getLogger(__name__)
    availability = check_worker_availability(context="data_preparation")

    # Handle critical blocking scenarios
    if availability["status"] == "no_capacity":
        # Clear any stale warning before showing error
        request.session.pop(f"data_prep_warning_{pk}", None)
        messages.error(request, availability["message"])
        return redirect("master-excel-detail", pk=pk)

    if availability["status"] == "no_workers":
        # Clear any stale warning before showing error
        request.session.pop(f"data_prep_warning_{pk}", None)
        messages.error(request, availability["message"])
        return redirect("master-excel-detail", pk=pk)

    # Handle "all busy" warning - requires explicit confirmation
    if availability["status"] == "all_busy":
        # Check if user confirmed via POST parameter
        if not request.POST.get("confirm_busy"):
            # Store warning in session (with master_excel pk to avoid conflicts)
            request.session[f"data_prep_warning_{pk}"] = {
                "message": availability["message"],
                "data": availability["data"],
            }
            # Redirect to show warning (will be displayed on detail page)
            return redirect("master-excel-detail", pk=pk)
        # User confirmed - log that they proceeded despite warning
        logger.info(
            f"Data preparation for '{master_excel.name}' started with user confirmation (all workers busy): "
            f"{availability['data']['active_workers']} active, "
            f"{availability['data']['pending_tasks']} pending"
        )

    # Clear any warning from session before proceeding
    # (Handles both confirmed warnings and cases where status changed to 'ok')
    request.session.pop(f"data_prep_warning_{pk}", None)

    # Get or create data packages
    core_package, _ = DataPackage.objects.get_or_create(
        name="core-data",
        version="v1.0.3",
        defaults={
            "source_type": DataPackage.SourceType.S3,
            "source_url": "s3://steelo-data/core-data-v1.0.3.zip",
        },
    )

    geo_package, _ = DataPackage.objects.get_or_create(
        name="geo-data",
        version="v1.1.0",
        defaults={
            "source_type": DataPackage.SourceType.S3,
            "source_url": "s3://steelo-data/geo-data-v1.1.0.zip",
        },
    )

    # Check if we already have a ready preparation for this master Excel
    existing = DataPreparation.objects.filter(
        core_data_package=core_package,
        geo_data_package=geo_package,
        master_excel=master_excel,
        status=DataPreparation.Status.READY,
    ).first()

    if existing:
        messages.info(request, f"A data preparation already exists for '{master_excel.name}'")
        return redirect("data-preparation-detail", pk=existing.pk)

    # Create new preparation
    prep = DataPreparation.objects.create(
        name=f"Data with {master_excel.name}",
        core_data_package=core_package,
        geo_data_package=geo_package,
        master_excel=master_excel,
    )

    # Run preparation in background
    from .tasks import prepare_data_task

    prepare_data_task.enqueue(prep.pk)

    messages.success(request, f"Data preparation started for '{master_excel.name}'. You can track the progress below.")

    # Redirect to the data preparation detail page to track progress
    return redirect("data-preparation-detail", pk=prep.pk)


class MasterExcelFileDeleteView(DeleteView):
    """Delete a master Excel file"""

    model = MasterExcelFile
    template_name = "steeloweb/master_excel_confirm_delete.html"
    success_url = reverse_lazy("master-excel-list")

    def get_queryset(self):
        # Prevent deletion of template and example files
        return super().get_queryset().filter(is_template=False, is_example=False)

    def post(self, request, *args, **kwargs):
        """Override post to check if deletion is allowed"""
        self.object = self.get_object()

        # Check if any DataPreparation using this file has active ModelRuns
        data_preparations = self.object.data_preparations.all()
        if data_preparations.exists():
            # Check if any of these DataPreparations are used by ModelRuns
            total_model_runs = 0
            for prep in data_preparations:
                model_run_count = prep.model_runs.count()
                total_model_runs += model_run_count

            if total_model_runs > 0:
                messages.error(
                    request,
                    f"Cannot delete: This file is used by {total_model_runs} model run(s) through data preparations.",
                )
                return redirect("master-excel-detail", pk=self.object.pk)

            # No ModelRuns found, safe to delete DataPreparations
            prep_count = data_preparations.count()
            data_preparations.delete()
            messages.success(
                request,
                f'Master Excel file "{self.object.name}" and its {prep_count} data preparation(s) have been deleted.',
            )
        else:
            # No DataPreparations, just delete the file
            messages.success(request, f'Master Excel file "{self.object.name}" has been deleted.')

        return self.delete(request, *args, **kwargs)


class DataPreparationDetailView(DetailView):
    """Display details of a specific data preparation"""

    model = DataPreparation
    template_name = "steeloweb/data_preparation_detail.html"
    context_object_name = "preparation"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Add any simulations using this preparation
        context["simulations"] = ModelRun.objects.filter(data_preparation=self.object).order_by("-started_at")
        return context


def get_data_preparation_progress(request, pk):
    """
    HTMX endpoint to get the current progress of a data preparation
    """
    preparation = get_object_or_404(DataPreparation, pk=pk)

    # Always return the progress template, let the template handle terminal states
    context = {"preparation": preparation}
    return render(request, "steeloweb/includes/data_preparation_progress.html", context)


def delete_data_preparation(request, pk):
    """
    Delete a data preparation (only if it has no model runs)
    """
    preparation = get_object_or_404(DataPreparation, pk=pk)

    # Check if this preparation is used by any model runs
    if preparation.model_runs.exists():
        messages.error(
            request, f"Cannot delete: This data preparation is used by {preparation.model_runs.count()} model run(s)."
        )
        return redirect("data-preparation-detail", pk=pk)

    # Get the master excel reference before deletion for redirect
    master_excel = preparation.master_excel

    # Delete the preparation
    preparation.delete()
    messages.success(request, "Data preparation has been deleted successfully.")

    # Redirect to master excel detail if it exists, otherwise to list
    if master_excel:
        return redirect("master-excel-detail", pk=master_excel.pk)
    else:
        return redirect("master-excel-list")


def delete_prepared_file(request, pk, filename):
    """
    Delete a specific prepared file to trigger incremental regeneration
    """
    preparation = get_object_or_404(DataPreparation, pk=pk)

    if request.method == "DELETE":
        if not preparation.data_directory:
            messages.error(request, "No data directory found for this preparation.")
            return HttpResponse(status=400)  # noqa

        from pathlib import Path
        import os

        # Build file path
        data_path = Path(preparation.data_directory)
        fixtures_path = data_path / "data" / "fixtures"
        file_path = fixtures_path / filename

        # Security check - ensure filename is safe and within fixtures directory
        if not filename.replace(".", "").replace("_", "").replace("-", "").isalnum():
            messages.error(request, "Invalid filename.")
            return HttpResponse(status=400)

        # Check if file exists and is within the fixtures directory
        try:
            file_path.resolve().relative_to(fixtures_path.resolve())
        except ValueError:
            messages.error(request, "File not found or access denied.")
            return HttpResponse(status=403)

        if file_path.exists():
            try:
                os.remove(file_path)
                messages.success(
                    request, f"File '{filename}' has been deleted. Run 'Re-run Preparation' to regenerate it."
                )
            except OSError as e:
                messages.error(request, f"Failed to delete file: {e}")
                return HttpResponse(status=500)
        else:
            messages.warning(request, f"File '{filename}' was not found.")

        # Return empty response to remove the file row from the table
        from django.http import HttpResponse

        return HttpResponse("")

    # Only DELETE is allowed
    return HttpResponse(status=405)


def view_prepared_file(request, pk, filename):
    """
    View prepared data file inline (JSON) or download (other formats)
    """
    import json
    from pathlib import Path

    preparation = get_object_or_404(DataPreparation, pk=pk)

    # Ensure preparation is ready
    if preparation.status != DataPreparation.Status.READY:
        messages.error(request, "Data preparation is not ready yet.")
        return redirect("data-preparation-detail", pk=pk)

    # Try to get the path from timing_data first
    data_dir = Path(preparation.data_directory)
    file_path = None

    # Check if we have file paths in timing_data
    if preparation.timing_data and "file_paths" in preparation.timing_data:
        file_paths = preparation.timing_data["file_paths"]
        if filename in file_paths:
            # Path is stored relative to preparation directory
            relative_path = file_paths[filename]
            file_path = data_dir / relative_path
            if not file_path.exists():
                file_path = None

    # Fallback to checking multiple possible locations
    if not file_path:
        possible_paths = [
            data_dir / "data" / "fixtures" / filename,  # JSON files from fixtures
            data_dir / "data" / filename,  # Geo-data files in data directory
        ]

        # For subdirectory files (e.g., Infrastructure/rail_distance1.nc)
        if "/" in filename:
            parts = filename.split("/")
            possible_paths.append(data_dir / "data" / Path(*parts))

        for path in possible_paths:
            if path.exists():
                file_path = path
                break

    if not file_path:
        raise Http404("File not found")

    # Security check - ensure the file is within the data directory
    try:
        file_path = file_path.resolve()
        data_dir_resolved = data_dir.resolve()
        if not str(file_path).startswith(str(data_dir_resolved)):
            raise Http404("Invalid file path")
    except Exception:
        raise Http404("File not found")

    # Handle download parameter
    if request.GET.get("download") == "1":
        return FileResponse(open(file_path, "rb"), as_attachment=True, filename=filename)

    # For JSON files, show inline with syntax highlighting
    if filename.endswith(".json"):
        try:
            with open(file_path, "r") as f:
                json_data = json.load(f)

            # Get file size
            file_size = file_path.stat().st_size

            # For large files, show only a preview
            is_large = file_size > 100 * 1024  # 100KB
            preview_data = None

            if is_large:
                # For lists, show first 10 items
                if isinstance(json_data, list):
                    preview_data = json_data[:10]
                # For dicts with a main data key that's a list
                elif isinstance(json_data, dict):
                    # Find the main data key (usually the largest list)
                    main_key = None
                    for key, value in json_data.items():
                        if isinstance(value, list) and (
                            main_key is None or len(value) > len(json_data.get(main_key, []))
                        ):
                            main_key = key

                    if main_key:
                        preview_data = {
                            **{k: v for k, v in json_data.items() if k != main_key},
                            main_key: json_data[main_key][:10],
                            "preview_info": {"total_items": len(json_data[main_key]), "showing": 10, "key": main_key},
                        }
                    else:
                        # Just show the structure
                        preview_data = {
                            k: "..." if isinstance(v, (list, dict)) and len(str(v)) > 100 else v
                            for k, v in json_data.items()
                        }

            context = {
                "preparation": preparation,
                "filename": filename,
                "json_data": json_data if not is_large else preview_data,
                "is_large": is_large,
                "file_size": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
                "total_items": len(json_data) if isinstance(json_data, list) else None,
            }

            return render(request, "steeloweb/prepared_file_view.html", context)

        except json.JSONDecodeError as e:
            messages.error(request, f"Error reading JSON file: {str(e)}")
            return redirect("data-preparation-detail", pk=pk)
        except Exception as e:
            messages.error(request, f"Error reading file: {str(e)}")
            return redirect("data-preparation-detail", pk=pk)

    # For CSV files, also show inline
    elif filename.endswith(".csv"):
        try:
            import csv

            with open(file_path, "r") as f:
                reader = csv.reader(f)
                rows = list(reader)

            # Get file size
            file_size = file_path.stat().st_size

            context = {
                "preparation": preparation,
                "filename": filename,
                "csv_rows": rows[:100],  # Show first 100 rows
                "total_rows": len(rows),
                "is_large": len(rows) > 100,
                "file_size": file_size,
                "file_size_mb": round(file_size / (1024 * 1024), 2),
            }

            return render(request, "steeloweb/prepared_file_view_csv.html", context)

        except Exception as e:
            messages.error(request, f"Error reading CSV file: {str(e)}")
            return redirect("data-preparation-detail", pk=pk)

    # For other file types, download directly
    else:
        return FileResponse(open(file_path, "rb"), as_attachment=True, filename=filename)


class ModelRunOutputFilesView(DetailView):
    """
    Display output files for a specific model run, similar to DataPreparationDetailView
    """

    model = ModelRun
    template_name = "steeloweb/modelrun_output_files.html"
    context_object_name = "modelrun"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        modelrun = self.object

        # Check if output directory exists
        if not modelrun.output_directory:
            context["files_by_directory"] = {}
            return context

        from pathlib import Path

        output_path = Path(modelrun.output_directory)
        if not output_path.exists():
            context["files_by_directory"] = {}
            return context

        # Group files by subdirectory
        files_by_directory = {}

        # Define the directories we want to scan
        directories_to_scan = [
            ("TM", "Trade Module"),
            ("plots/GEO", "Geospatial Plots"),
            ("plots/PAM", "Simulation Plots"),
            (".", "Root Directory"),  # For files in the root output directory
        ]

        for dir_path, display_name in directories_to_scan:
            full_path = output_path / dir_path if dir_path != "." else output_path

            if full_path.exists() and full_path.is_dir():
                files = []

                # List all files in this directory (non-recursive)
                for item in full_path.iterdir():
                    if item.is_file() and not item.name.startswith("."):
                        # Get file info
                        file_info = {
                            "name": item.name,
                            "size": item.stat().st_size,
                            "size_display": self._format_file_size(item.stat().st_size),
                            "type": self._get_file_type(item.name),
                            "icon": self._get_file_icon(item.name),
                            "relative_path": str(item.relative_to(output_path)),
                            "is_viewable": self._is_viewable(item.name),
                            "is_image": self._is_image(item.name),
                        }
                        files.append(file_info)

                # Sort files by name
                if files:
                    files.sort(key=lambda x: x["name"].lower())
                    files_by_directory[display_name] = files

        context["files_by_directory"] = files_by_directory
        return context

    def _format_file_size(self, size_bytes):
        """Format file size in human readable format"""
        for unit in ["B", "KB", "MB", "GB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} TB"

    def _get_file_type(self, filename):
        """Get file type description based on extension"""
        ext = filename.lower().split(".")[-1]
        types = {
            "csv": "CSV File",
            "json": "JSON File",
            "txt": "Text File",
            "png": "PNG Image",
            "jpg": "JPEG Image",
            "jpeg": "JPEG Image",
            "pdf": "PDF Document",
            "xlsx": "Excel File",
            "xls": "Excel File",
            "log": "Log File",
            "pkl": "Pickle File",
            "parquet": "Parquet File",
        }
        return types.get(ext, f"{ext.upper()} File")

    def _get_file_icon(self, filename):
        """Get FontAwesome icon class for file type"""
        ext = filename.lower().split(".")[-1]
        icons = {
            "csv": "fa-file-csv",
            "json": "fa-file-code",
            "txt": "fa-file-alt",
            "png": "fa-file-image",
            "jpg": "fa-file-image",
            "jpeg": "fa-file-image",
            "pdf": "fa-file-pdf",
            "xlsx": "fa-file-excel",
            "xls": "fa-file-excel",
            "log": "fa-file-alt",
            "pkl": "fa-file-archive",
            "parquet": "fa-database",
        }
        return icons.get(ext, "fa-file")

    def _is_viewable(self, filename):
        """Check if file can be viewed inline"""
        viewable_extensions = (".csv", ".json", ".txt", ".log")
        return filename.lower().endswith(viewable_extensions)

    def _is_image(self, filename):
        """Check if file is an image"""
        image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".bmp")
        return filename.lower().endswith(image_extensions)


def view_modelrun_output_file(request, pk, filepath):
    """
    View or download a specific output file from a model run
    """
    modelrun = get_object_or_404(ModelRun, pk=pk)

    if not modelrun.output_directory:
        raise Http404("No output directory found for this model run")

    from pathlib import Path
    import json

    output_path = Path(modelrun.output_directory)
    if not output_path.exists():
        raise Http404("Output directory does not exist")

    # Security check - ensure the requested file is within the output directory
    try:
        requested_file = (output_path / filepath).resolve()
        # Check if requested_file is within output_path
        requested_file.relative_to(output_path.resolve())
    except (ValueError, Exception):
        raise Http404("Invalid file path")

    if not requested_file.exists() or not requested_file.is_file():
        raise Http404("File not found")

    # Handle download parameter
    if request.GET.get("download") == "1":
        return FileResponse(open(requested_file, "rb"), as_attachment=True, filename=requested_file.name)

    # For viewable text files, show inline
    viewable_extensions = (".csv", ".json", ".txt", ".log")
    if requested_file.suffix.lower() in viewable_extensions:
        if requested_file.suffix.lower() == ".json":
            try:
                with open(requested_file, "r") as f:
                    json_data = json.load(f)

                # Get file size
                file_size = requested_file.stat().st_size
                is_large = file_size > 100 * 1024  # 100KB

                context = {
                    "modelrun": modelrun,
                    "filename": requested_file.name,
                    "filepath": filepath,
                    "json_data": json_data if not is_large else {"preview": "File too large for inline viewing"},
                    "is_large": is_large,
                    "file_size": file_size,
                    "file_size_mb": round(file_size / (1024 * 1024), 2),
                }

                return render(request, "steeloweb/modelrun_output_file_view.html", context)

            except json.JSONDecodeError as e:
                messages.error(request, f"Error reading JSON file: {str(e)}")
                return redirect("modelrun-output-files", pk=pk)

        elif requested_file.suffix.lower() == ".csv":
            try:
                import csv

                with open(requested_file, "r") as f:
                    reader = csv.reader(f)
                    rows = list(reader)

                # Get file size
                file_size = requested_file.stat().st_size

                context = {
                    "modelrun": modelrun,
                    "filename": requested_file.name,
                    "filepath": filepath,
                    "csv_rows": rows[:100],  # Show first 100 rows
                    "total_rows": len(rows),
                    "is_large": len(rows) > 100,
                    "file_size": file_size,
                    "file_size_mb": round(file_size / (1024 * 1024), 2),
                }

                return render(request, "steeloweb/modelrun_output_file_view_csv.html", context)

            except Exception as e:
                messages.error(request, f"Error reading CSV file: {str(e)}")
                return redirect("modelrun-output-files", pk=pk)

        else:
            # For other text files, show as plain text
            try:
                with open(requested_file, "r") as f:
                    content = f.read(1024 * 1024)  # Read up to 1MB

                file_size = requested_file.stat().st_size
                is_truncated = file_size > 1024 * 1024

                context = {
                    "modelrun": modelrun,
                    "filename": requested_file.name,
                    "filepath": filepath,
                    "content": content,
                    "is_truncated": is_truncated,
                    "file_size": file_size,
                    "file_size_mb": round(file_size / (1024 * 1024), 2),
                }

                return render(request, "steeloweb/modelrun_output_file_view_text.html", context)

            except Exception as e:
                messages.error(request, f"Error reading file: {str(e)}")
                return redirect("modelrun-output-files", pk=pk)

    # For images, serve directly
    image_extensions = (".png", ".jpg", ".jpeg", ".gif", ".bmp")
    if requested_file.suffix.lower() in image_extensions:
        return FileResponse(open(requested_file, "rb"), content_type=f"image/{requested_file.suffix[1:]}")

    # For all other files, download
    return FileResponse(open(requested_file, "rb"), as_attachment=True, filename=requested_file.name)


def _get_or_create_template_master_excel():
    """
    Get or create the template master Excel file from S3.
    Returns the MasterExcelFile instance or None if creation fails.
    """
    import logging

    logger = logging.getLogger(__name__)

    # Check if template already exists
    template = MasterExcelFile.objects.filter(is_template=True).first()
    if template and template.file:
        file_path = template.get_file_path()
        if file_path.exists():
            logger.info(f"Using existing template MasterExcelFile: {template.name}")
            return template

    # Need to create/update template from S3
    try:
        from steelo.data.manager import DataManager
        import traceback

        manager = DataManager()

        # Download the master-input package
        logger.info("Downloading master-input package from S3...")
        try:
            manager.download_package("master-input", force=False)
        except Exception as download_error:
            logger.error(f"Failed to download master-input package: {download_error}")
            logger.error(f"Download error traceback: {traceback.format_exc()}")
            return None

        cache_path = manager.get_package_path("master-input")
        logger.info(f"Cache path for master-input: {cache_path}")

        if cache_path and cache_path.exists():
            # Look for the Excel file
            excel_source = cache_path / "master_input.xlsx"
            logger.info(f"Looking for Excel file at: {excel_source}")

            # List all files in the cache directory for debugging
            if cache_path.is_dir():
                files_in_cache = list(cache_path.iterdir())
                logger.info(f"Files in cache directory: {[f.name for f in files_in_cache]}")

            if excel_source.exists():
                # Create or update the template MasterExcelFile
                if not template:
                    template = MasterExcelFile(
                        name="Master Input Template",
                        description="Official master Excel template from S3",
                        is_template=True,
                    )

                # Copy to media directory
                logger.info("Copying master Excel template to Django media directory...")
                with open(excel_source, "rb") as f:
                    template.file.save("master_input_template.xlsx", ContentFile(f.read()))

                # Validate the template
                template.validate()

                logger.info(f"Successfully created template MasterExcelFile: {template.name}")
                return template
            else:
                logger.error(f"Master Excel file not found at {excel_source}")
                logger.error(f"Cache directory exists: {cache_path.exists()}")
                if cache_path.exists():
                    logger.error(f"Cache directory contents: {list(cache_path.iterdir())}")
                return None
        else:
            logger.error(f"Failed to get cache path or cache path doesn't exist: {cache_path}")
            return None

    except Exception as e:
        import traceback

        logger.error(f"Error creating template master Excel: {e}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return None


def first_time_setup(request):
    """
    First-time setup view for Electron app startup.
    Checks if data preparation is needed and handles the setup process.
    """
    # Handle POST request (Get Started button after seeing disclaimer)
    if request.method == "POST":
        # User has acknowledged the disclaimer and consented to error reporting
        # Store error reporting consent for Electron app
        import json

        if "ELECTRON_USER_DATA" in os.environ:
            user_data_dir = os.environ["ELECTRON_USER_DATA"]
            consent_file = os.path.join(user_data_dir, "error-reporting-consent.json")
            try:
                os.makedirs(user_data_dir, exist_ok=True)
                with open(consent_file, "w") as f:
                    json.dump(
                        {
                            "enabled": True,
                            "timestamp": timezone.now().isoformat(),
                            "version": "1.0",  # Version of the disclaimer they agreed to
                            "disclaimer_version": "F.3",  # Reference to the specific clause
                        },
                        f,
                    )
                pass  # Successfully saved consent
            except Exception:
                pass  # Failed to save consent, but don't block the user

        # Also store in session for web-based tracking
        request.session["error_reporting_consent"] = {
            "consented": True,
            "timestamp": timezone.now().isoformat(),
            "version": "1.0",
        }

        return redirect("modelrun-list")

    db_config = settings.DATABASES.get("default", {})
    db_path_value = db_config.get("NAME")
    db_path = Path(db_path_value) if db_path_value else None
    db_readonly_error = False

    if db_path:
        try:
            if db_path.exists():
                db_readonly_error = not os.access(db_path, os.W_OK)
            else:
                parent_dir = db_path.parent
                db_readonly_error = not os.access(parent_dir, os.W_OK | os.X_OK)
        except OSError:
            db_readonly_error = True

    if db_readonly_error:
        logger = logging.getLogger(__name__)
        logger.error(
            "SQLite database path is not writable: %s. First-time setup cannot continue.",
            db_path,
        )
        return render(
            request,
            "steeloweb/first_time_setup.html",
            {
                "preparation": None,
                "is_new": False,
                "db_readonly_error": True,
                "db_path": str(db_path) if db_path else "",
            },
        )

    # Determine the latest core-data and geo-data versions from the manifest
    manager = DataManager()
    core_manifest = manager.manifest.get_package("core-data")
    geo_manifest = manager.manifest.get_package("geo-data")

    if not core_manifest or not geo_manifest:
        logger = logging.getLogger(__name__)
        logger.error("Unable to locate core-data or geo-data package definitions in manifest.json")
        return render(
            request,
            "steeloweb/first_time_setup.html",
            {
                "preparation": None,
                "is_new": False,
                "data_package_error": True,
            },
        )

    def _normalize_version(version: str) -> str:
        return version if version.startswith("v") else f"v{version}"

    core_defaults = {
        "source_type": DataPackage.SourceType.S3,
        "source_url": core_manifest.url,
        "checksum": core_manifest.checksum or "",
        "size_mb": core_manifest.size_mb,
        "is_active": True,
    }
    geo_defaults = {
        "source_type": DataPackage.SourceType.S3,
        "source_url": geo_manifest.url,
        "checksum": geo_manifest.checksum or "",
        "size_mb": geo_manifest.size_mb,
        "is_active": True,
    }

    core_package, _ = DataPackage.objects.get_or_create(
        name="core-data",
        version=_normalize_version(core_manifest.version),
        defaults=core_defaults,
    )
    geo_package, _ = DataPackage.objects.get_or_create(
        name="geo-data",
        version=_normalize_version(geo_manifest.version),
        defaults=geo_defaults,
    )

    def _apply_defaults(package: DataPackage, defaults: dict[str, object]) -> None:
        updated = False
        for field, value in defaults.items():
            if getattr(package, field) != value:
                setattr(package, field, value)
                updated = True
        if updated:
            package.save(update_fields=list(defaults.keys()))

    _apply_defaults(core_package, core_defaults)
    _apply_defaults(geo_package, geo_defaults)

    # Deactivate older package entries to avoid accidental reuse
    DataPackage.objects.filter(name="core-data").exclude(pk=core_package.pk).update(is_active=False)
    DataPackage.objects.filter(name="geo-data").exclude(pk=geo_package.pk).update(is_active=False)

    # Restrict ready preparations to the active package versions
    ready_qs = DataPreparation.objects.filter(
        status=DataPreparation.Status.READY,
        core_data_package=core_package,
        geo_data_package=geo_package,
    )

    if ready_qs.exists():
        existing_prep = ready_qs.first()
        return render(request, "steeloweb/first_time_setup.html", {"preparation": existing_prep, "is_new": False})

    # Check if there's a preparation in progress or failed for the active packages
    existing_prep = DataPreparation.objects.filter(
        core_data_package=core_package,
        geo_data_package=geo_package,
        status__in=[
            DataPreparation.Status.PENDING,
            DataPreparation.Status.DOWNLOADING,
            DataPreparation.Status.EXTRACTING,
            DataPreparation.Status.PREPARING,
            DataPreparation.Status.FAILED,
        ],
    ).first()

    if existing_prep:
        return render(request, "steeloweb/first_time_setup.html", {"preparation": existing_prep, "is_new": False})

    # Get or create the template master Excel file (same as the working pattern)
    template_master_excel = _get_or_create_template_master_excel()

    if not template_master_excel:
        # Failed to create template, create a failed preparation to show the error
        preparation = DataPreparation.objects.create(
            name="Initial Setup Data",
            core_data_package=core_package,
            geo_data_package=geo_package,
            status=DataPreparation.Status.FAILED,
            error_message="Failed to download master Excel template from S3. Please check your internet connection.",
        )
        return render(request, "steeloweb/first_time_setup.html", {"preparation": preparation, "is_new": True})

    # Check if we already have a ready preparation for this template (same check as working pattern)
    existing = DataPreparation.objects.filter(
        core_data_package=core_package,
        geo_data_package=geo_package,
        master_excel=template_master_excel,
        status=DataPreparation.Status.READY,
    ).first()

    if existing:
        # Show the ready state with disclaimer
        return render(request, "steeloweb/first_time_setup.html", {"preparation": existing, "is_new": False})

    # Create new preparation with template master Excel (same as working pattern)
    preparation = DataPreparation.objects.create(
        name="Initial Setup Data",
        core_data_package=core_package,
        geo_data_package=geo_package,
        master_excel=template_master_excel,  # This is the key fix!
    )

    # Start preparation in background
    from .tasks import prepare_data_task

    logger = logging.getLogger(__name__)
    logger.info(
        f"Enqueueing data preparation task for preparation {preparation.pk} with master Excel {template_master_excel.name}"
    )

    try:
        task_result = prepare_data_task.enqueue(preparation.pk)
        logger.info(f"Task enqueued successfully with result: {task_result}")
    except Exception as e:
        logger.error(f"Failed to enqueue task: {e}")
        preparation.status = DataPreparation.Status.FAILED
        preparation.error_message = f"Failed to start preparation task: {e}"
        preparation.save()

    return render(request, "steeloweb/first_time_setup.html", {"preparation": preparation, "is_new": True})
