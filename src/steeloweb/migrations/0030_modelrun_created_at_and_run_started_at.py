# Rename ModelRun.started_at to created_at (its true semantic) and add
# run_started_at to capture when a run actually transitions to RUNNING.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("steeloweb", "0029_alter_simulationplot_options_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="modelrun",
            old_name="started_at",
            new_name="created_at",
        ),
        migrations.AlterField(
            model_name="modelrun",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, help_text="When the model run was created"),
        ),
        migrations.AddField(
            model_name="modelrun",
            name="run_started_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the simulation transitioned to RUNNING",
                null=True,
            ),
        ),
        migrations.AlterModelOptions(
            name="modelrun",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Model Run",
                "verbose_name_plural": "Model Runs",
            },
        ),
    ]
