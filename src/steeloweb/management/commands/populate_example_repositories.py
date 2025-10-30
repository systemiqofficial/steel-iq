"""
Management command to populate example repositories from prepared data.
"""

import json
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile

from steeloweb.models import Repository, DataPreparation


class Command(BaseCommand):
    help = "Populate example repositories from prepared data"

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Force update existing example repositories",
        )
        parser.add_argument(
            "--preparation-id",
            type=int,
            help="ID of DataPreparation to use as source (default: latest active preparation)",
        )

    def handle(self, *args, **options):
        force = options["force"]

        # Get data preparation to use
        if options.get("preparation_id"):
            try:
                preparation = DataPreparation.objects.get(pk=options["preparation_id"])
            except DataPreparation.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"DataPreparation with ID {options['preparation_id']} not found"))
                return
        else:
            # Use latest active preparation
            preparation = (
                DataPreparation.objects.filter(is_active=True, status="completed").order_by("-created_at").first()
            )
            if not preparation:
                self.stdout.write(
                    self.style.ERROR(
                        "No completed data preparation found. Please run 'python manage.py prepare_default_data' first."
                    )
                )
                return

        self.stdout.write(f"Using DataPreparation: {preparation}")

        # Get the fixtures directory from the preparation
        fixtures_dir = preparation.get_data_path() / "data" / "fixtures"
        if not fixtures_dir.exists():
            self.stdout.write(self.style.ERROR(f"Fixtures directory not found in preparation: {fixtures_dir}"))
            return

        # Define the fixture files to create as example repositories
        fixture_configs = [
            {
                "name": "Example Steel Plants",
                "description": "Example steel plant locations and configurations",
                "repo_type": Repository.RepoType.PLANTS,
                "fixture_paths": [
                    fixtures_dir / "plants.json",
                ],
            },
            {
                "name": "Example Demand Centers",
                "description": "Example demand center locations and data",
                "repo_type": Repository.RepoType.DEMAND_CENTERS,
                "fixture_paths": [
                    fixtures_dir / "demand_centers.json",
                ],
            },
        ]

        for config in fixture_configs:
            # Check if this example repository already exists
            existing = Repository.objects.filter(name=config["name"], is_example=True).first()

            if existing and not force:
                self.stdout.write(
                    self.style.WARNING(f"Example repository '{config['name']}' already exists. Use --force to update.")
                )
                continue

            # Find the fixture file - try multiple paths
            fixture_path = None
            for path in config["fixture_paths"]:
                if path.exists():
                    fixture_path = path
                    break

            if not fixture_path:
                self.stdout.write(self.style.ERROR(f"Fixture file not found in any of: {config['fixture_paths']}"))
                continue

            # Read the fixture file
            try:
                with open(fixture_path, "r", encoding="utf-8") as f:
                    fixture_data = json.load(f)

                # Validate it's valid JSON
                json_content = json.dumps(fixture_data, indent=2)

            except (json.JSONDecodeError, IOError) as e:
                self.stdout.write(self.style.ERROR(f"Error reading fixture file {fixture_path}: {e}"))
                continue

            # Create or update the repository
            if existing:
                # Update existing
                repository = existing
                repository.description = config["description"]
                repository.repo_type = config["repo_type"]
                # Update the file content
                repository.file.save(
                    f"{config['name'].lower().replace(' ', '_')}.json",
                    ContentFile(json_content.encode("utf-8")),
                    save=False,
                )
                repository.save()
                action = "Updated"
            else:
                # Create new
                repository = Repository(
                    name=config["name"],
                    description=config["description"],
                    repo_type=config["repo_type"],
                    is_example=True,
                )
                repository.file.save(
                    f"{config['name'].lower().replace(' ', '_')}.json",
                    ContentFile(json_content.encode("utf-8")),
                )
                repository.save()
                action = "Created"

            self.stdout.write(
                self.style.SUCCESS(
                    f"{action} example repository '{config['name']}' "
                    f"({len(fixture_data)} entries) from {fixture_path.name}"
                )
            )
