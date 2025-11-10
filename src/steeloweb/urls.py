"""
URL configuration for steeloweb project with production-grade worker management endpoints.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.urls import path
from . import views
from . import views_worker
# NOTE: These imports are for future scenario views (not yet implemented)
# from steeloweb.views import scenarios, scenario_variations, sensitivity_sweeps


urlpatterns = [
    # First-time setup
    path("first-time-setup/", views.first_time_setup, name="first-time-setup"),
    # Model Run URLs
    path("", views.ModelRunListView.as_view(), name="modelrun-list"),
    path("modelrun/list-fragment/", views.modelrun_list_fragment, name="modelrun-list-fragment"),
    path("modelrun/<int:pk>/", views.ModelRunDetailView.as_view(), name="modelrun-detail"),
    path("modelrun/<int:pk>/run/", views.run_simulation, name="run-simulation"),
    path("modelrun/<int:pk>/rerun/", views.rerun_modelrun, name="rerun-modelrun"),
    path("modelrun/<int:pk>/dismiss-warning/", views.dismiss_simulation_warning, name="dismiss-simulation-warning"),
    path("modelrun/<int:pk>/cancel/", views.cancel_modelrun, name="cancel-modelrun"),
    path("modelrun/<int:pk>/force-stop/", views.force_stop_modelrun, name="force-stop-modelrun"),
    path("modelrun/<int:pk>/progress/", views.get_modelrun_progress, name="modelrun-progress"),
    path("modelrun/<int:pk>/cost/<str:map_type>/", views.view_cost_map, name="view-cost-map"),
    path("modelrun/<int:pk>/priority/<str:map_type>/", views.view_priority_map, name="view-priority-map"),
    path(
        "modelrun/<int:pk>/plant-viz/<str:visualization_type>/",
        views.view_plant_visualization,
        name="view-plant-visualization",
    ),
    path("modelrun/<int:pk>/plot/<int:plot_id>/", views.view_simulation_plot, name="view-simulation-plot"),
    path("modelrun/<int:pk>/download-csv/", views.download_modelrun_csv, name="download-modelrun-csv"),
    path("modelrun/<int:pk>/delete/", views.ModelRunDeleteView.as_view(), name="modelrun-delete"),
    path("modelrun/<int:pk>/output-files/", views.ModelRunOutputFilesView.as_view(), name="modelrun-output-files"),
    path(
        "modelrun/<int:pk>/output-file/<path:filepath>/",
        views.view_modelrun_output_file,
        name="view-modelrun-output-file",
    ),
    path("modelrun/create/", views.create_modelrun, name="create-modelrun"),
    path("modelrun/technologies-fragment/", views.technologies_fragment, name="create-modelrun-tech-fragment"),
    path(
        "modelrun/dataset-metadata-fragment/", views.dataset_metadata_fragment, name="create-modelrun-metadata-fragment"
    ),
    # Master Excel URLs
    path("master-excel/", views.MasterExcelFileListView.as_view(), name="master-excel-list"),
    path("master-excel/create/", views.MasterExcelFileCreateView.as_view(), name="master-excel-create"),
    path("master-excel/<int:pk>/", views.MasterExcelFileDetailView.as_view(), name="master-excel-detail"),
    path("master-excel/<int:pk>/update/", views.MasterExcelFileUpdateView.as_view(), name="master-excel-update"),
    path("master-excel/<int:pk>/delete/", views.MasterExcelFileDeleteView.as_view(), name="master-excel-delete"),
    path("master-excel/<int:pk>/download/", views.download_master_excel_file, name="master-excel-download"),
    path(
        "master-excel/<int:pk>/prepare-data/",
        views.prepare_data_with_master_excel,
        name="prepare-data-with-master-excel",
    ),
    path(
        "master-excel/<int:pk>/dismiss-prep-warning/",
        views.dismiss_data_prep_warning,
        name="dismiss-data-prep-warning",
    ),
    path(
        "master-excel/download-template/", views.download_master_excel_template, name="download-master-excel-template"
    ),
    # Data Preparation URLs
    path("data-preparation/<int:pk>/", views.DataPreparationDetailView.as_view(), name="data-preparation-detail"),
    path("data-preparation/<int:pk>/progress/", views.get_data_preparation_progress, name="data-preparation-progress"),
    path("data-preparation/<int:pk>/delete/", views.delete_data_preparation, name="delete-data-preparation"),
    path("data-preparation/<int:pk>/file/<str:filename>/", views.view_prepared_file, name="view-prepared-file"),
    path(
        "data-preparation/<int:pk>/file/<str:filename>/delete/", views.delete_prepared_file, name="delete-prepared-file"
    ),
    # Circularity data URLs
    path("circularity/upload/", views.upload_circularity_data, name="upload-circularity"),
    path(
        "modelrun/<int:modelrun_id>/circularity/upload/",
        views.upload_circularity_data,
        name="upload-circularity-for-modelrun",
    ),
    # ======= PRODUCTION-GRADE WORKER MANAGEMENT ENDPOINTS =======
    # PRIMARY: Tick endpoint for all state transitions (POST only)
    path("workers/tick/", views_worker.workers_tick, name="workers-tick"),
    # HTMX endpoints (side-effect-free GETs, mutating POSTs)
    path("htmx/workers/status/", views_worker.worker_status_htmx, name="worker-status-htmx"),
    path("htmx/workers/add/", views_worker.add_worker_htmx, name="add-worker-htmx"),
    path("htmx/workers/drain/", views_worker.drain_worker_htmx, name="drain-worker-htmx"),
    path("htmx/workers/drain/<str:worker_id>/", views_worker.drain_worker_htmx, name="drain-specific-worker-htmx"),
    path("htmx/workers/abort/<str:worker_id>/", views_worker.abort_worker_htmx, name="abort-worker-htmx"),
    path("htmx/workers/cleanup/", views_worker.cleanup_workers_htmx, name="cleanup-workers-htmx"),
    # JSON API endpoints (side-effect-free GETs, mutating POSTs/DELETEs)
    path("api/workers/status/", views_worker.worker_status_json, name="worker-status-json"),
    path("api/workers/add/", views_worker.add_worker_json, name="add-worker-json"),
    path("api/workers/drain/", views_worker.drain_worker_json, name="drain-worker-json"),
    path("api/workers/drain/<str:worker_id>/", views_worker.drain_worker_json, name="drain-specific-worker-json"),
    path("api/workers/drain-all/", views_worker.drain_all_workers_json, name="drain-all-workers-json"),
    path("api/workers/<str:worker_id>/abort/", views_worker.abort_worker_json, name="abort-worker-json"),
    path(
        "api/workers/cleanup/", views_worker.cleanup_workers_htmx, name="cleanup-workers-json"
    ),  # Using HTMX version for now
    # ======= SCENARIO MANAGEMENT ENDPOINTS =======
    # Temporarily commented out until views are created
    # # Scenarios
    # path('scenarios/', scenarios.list, name='scenario_list'),
    # path('scenarios/new/', scenarios.create, name='scenario_create'),
    # path('scenarios/<int:id>/', scenarios.detail, name='scenario_detail'),
    # path('scenarios/<int:id>/edit/', scenarios.update, name='scenario_update'),
    # path('scenarios/<int:id>/delete/', scenarios.delete, name='scenario_delete'),
    # path('scenarios/<int:id>/clone/', scenarios.clone, name='scenario_clone'),
    # path('scenarios/<int:id>/run/', scenarios.run, name='scenario_run'),
    # # Scenario Variations
    # path('scenarios/<int:scenario_id>/variations/new/',
    #      scenario_variations.create, name='variation_create'),
    # path('variations/<int:id>/',
    #      scenario_variations.detail, name='variation_detail'),
    # path('variations/<int:id>/edit/',
    #      scenario_variations.update, name='variation_update'),
    # path('variations/<int:id>/delete/',
    #      scenario_variations.delete, name='variation_delete'),
    # # Sensitivity Sweeps
    # path('scenarios/<int:scenario_id>/sweeps/new/',
    #      sensitivity_sweeps.create, name='sweep_create'),
    # path('sweeps/<int:id>/',
    #      sensitivity_sweeps.detail, name='sweep_detail'),
    # path('sweeps/<int:id>/generate/',
    #      sensitivity_sweeps.generate_runs, name='sweep_generate'),
    # path('sweeps/<int:id>/delete/',
    #      sensitivity_sweeps.delete, name='sweep_delete'),
    # # Comparison
    # path('compare/', scenarios.compare, name='scenario_compare'),
]
