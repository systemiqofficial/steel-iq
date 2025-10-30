#!/usr/bin/env python
"""
Demo script to demonstrate the Django data preparation workflow.
This is not a pytest test file - it's a demonstration script.
"""

import os
import django


from steeloweb.models import DataPackage, DataPreparation, ModelRun

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()


def test_data_preparation():
    print("=== Django Data Preparation Test ===\n")

    # 1. List available data packages
    print("1. Available Data Packages:")
    for dp in DataPackage.objects.all():
        print(f"   - {dp}")

    # 2. Create a data preparation
    print("\n2. Creating Data Preparation...")

    core_package = DataPackage.objects.filter(name="core-data").first()
    geo_package = DataPackage.objects.filter(name="geo-data").first()

    if not core_package or not geo_package:
        print("   ERROR: Required packages not found. Run 'python manage.py import_data_packages --from-s3' first.")
        return

    preparation = DataPreparation.objects.create(
        name="Test Preparation v1.0.3",
        core_data_package=core_package,
        geo_data_package=geo_package,
    )
    print(f"   Created: {preparation}")

    # 3. Run preparation (synchronously for demo)
    print("\n3. Running Data Preparation...")
    print("   Note: In production, this would run asynchronously via django-tasks")

    from steeloweb.services import DataPreparationService

    service = DataPreparationService()

    # Set up environment
    os.environ["STEELO_DEVELOPMENT"] = "true"
    os.environ["STEELO_OUTPUT_DIR"] = "/tmp/steelo_outputs"
    os.environ["STEELO_HOME"] = "/tmp/steelo_home"
    os.environ["MPLBACKEND"] = "Agg"

    success, message = service.prepare_data(preparation)

    if success:
        print(f"   ✓ Success: {message}")
        print(f"   Data prepared at: {preparation.data_directory}")
    else:
        print(f"   ✗ Failed: {message}")
        return

    # 4. Create a model run using the preparation
    print("\n4. Creating Model Run with Data Preparation...")

    model_run = ModelRun.objects.create(
        data_preparation=preparation,
        config={
            "start_year": 2025,
            "end_year": 2030,
            "demand_sheet_name": "Steel_Demand_Chris Bataille",
        },
    )
    print(f"   Created: ModelRun {model_run.id}")

    # 5. Show what would happen when running
    print("\n5. Model Run Configuration:")
    print(f"   - Will use data from: {preparation.data_directory}")
    print(f"   - Start Year: {model_run.config['start_year']}")
    print(f"   - End Year: {model_run.config['end_year']}")

    print("\n=== Test Complete ===")
    print("\nTo run a simulation:")
    print("1. Go to Django Admin")
    print("2. Select the Model Run")
    print("3. Change state to 'Running' and save")
    print("4. Or use: run_simulation_task.enqueue(model_run.id)")


if __name__ == "__main__":
    test_data_preparation()
