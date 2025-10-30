# Generated migration for log_file_uuid field
import uuid
from django.db import migrations, models


def backfill_uuids(apps, schema_editor):
    """Generate unique UUIDs for all existing ModelRun records."""
    ModelRun = apps.get_model("steeloweb", "ModelRun")
    for modelrun in ModelRun.objects.all():
        modelrun.log_file_uuid = uuid.uuid4()
        modelrun.save(update_fields=["log_file_uuid"])


def reverse_backfill(apps, schema_editor):
    """No-op reverse migration."""
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("steeloweb", "0027_allow_blank_result_images"),
    ]

    operations = [
        # Step 1: Add field without unique constraint
        migrations.AddField(
            model_name="modelrun",
            name="log_file_uuid",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                null=True,  # Temporarily allow NULL
                help_text="UUID used for log file naming to prevent collisions across app reinstalls",
            ),
        ),
        # Step 2: Backfill unique UUIDs for existing rows
        migrations.RunPython(backfill_uuids, reverse_backfill),
        # Step 3: Make field non-nullable and add unique constraint
        migrations.AlterField(
            model_name="modelrun",
            name="log_file_uuid",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                help_text="UUID used for log file naming to prevent collisions across app reinstalls",
            ),
        ),
    ]
