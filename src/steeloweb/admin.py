from django.contrib import admin
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from .models import (
    ModelRun,
    ResultImages,
    DataPackage,
    DataPreparation,
    SimulationPlot,
    MasterExcelFile,
    Scenario,
    ScenarioVariation,
    SensitivitySweep,
)
from .forms import DataPreparationForm


@admin.register(ModelRun)
class ModelRunAdmin(admin.ModelAdmin):
    list_display = ("id", "state", "started_at", "finished_at")
    list_filter = ("state",)
    readonly_fields = ("started_at",)
    search_fields = ("id", "state")


@admin.register(ResultImages)
class ResultImagesAdmin(admin.ModelAdmin):
    list_display = ("modelrun", "lcoe_map", "lcoh_map")
    readonly_fields = ("modelrun",)


@admin.register(DataPackage)
class DataPackageAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "source_type", "size_mb", "is_active", "uploaded_at")
    list_filter = ("name", "source_type", "is_active")
    search_fields = ("name", "version")
    readonly_fields = ("checksum", "uploaded_at", "size_mb")

    def get_readonly_fields(self, request, obj=None):
        # Make source_url readonly for S3 packages
        if obj and obj.source_type == DataPackage.SourceType.S3:
            return self.readonly_fields + ("source_url",)
        return self.readonly_fields


@admin.register(DataPreparation)
class DataPreparationAdmin(admin.ModelAdmin):
    form = DataPreparationForm
    list_display = ("name", "status", "core_package", "geo_package", "has_master_excel", "created_at", "prepared_at")
    list_filter = ("status", "created_at")
    search_fields = ("name",)
    readonly_fields = (
        "status",
        "data_directory",
        "prepared_at",
        "created_at",
        "updated_at",
        "preparation_log_display",
        "validation_report_display",
    )

    fieldsets = (
        (None, {"fields": ("name", "status", "core_data_package", "geo_data_package")}),
        ("Master Excel", {"fields": ("master_excel_file", "validation_report_display")}),
        ("Status", {"fields": ("data_directory", "prepared_at", "error_message")}),
        ("Log", {"fields": ("preparation_log_display",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def core_package(self, obj):
        return f"{obj.core_data_package.name} v{obj.core_data_package.version}"

    core_package.short_description = "Core Data"

    def geo_package(self, obj):
        return f"{obj.geo_data_package.name} v{obj.geo_data_package.version}"

    geo_package.short_description = "Geo Data"

    def has_master_excel(self, obj):
        """Check if master Excel file is uploaded."""
        return bool(obj.master_excel_file)

    has_master_excel.boolean = True
    has_master_excel.short_description = "Has Master Excel"

    def validation_report_display(self, obj):
        """Display validation report with HTML formatting."""
        if not obj.master_excel_validation_report:
            return "No validation report"

        report = obj.master_excel_validation_report
        summary = report.get("summary", {})
        errors = report.get("errors", [])
        warnings = report.get("warnings", [])
        info = report.get("info", [])
        validated_at = report.get("validated_at", "Unknown")

        html = '<div style="font-family: monospace; max-width: 800px;">'
        html += f"<p><strong>Validated at:</strong> {validated_at}</p>"

        # Summary
        html += "<h4>Summary:</h4>"
        html += "<ul>"
        html += f"<li>Errors: {summary.get('error_count', 0)}</li>"
        html += f"<li>Warnings: {summary.get('warning_count', 0)}</li>"
        html += f"<li>Info: {summary.get('info_count', 0)}</li>"
        html += "</ul>"

        # Errors
        if errors:
            html += '<h4 style="color: red;">Errors:</h4>'
            html += '<ul style="color: red;">'
            for error in errors[:10]:  # Show first 10
                html += f"<li>{error}</li>"
            if len(errors) > 10:
                html += f"<li><em>... and {len(errors) - 10} more errors</em></li>"
            html += "</ul>"

        # Warnings
        if warnings:
            html += '<h4 style="color: orange;">Warnings:</h4>'
            html += '<ul style="color: orange;">'
            for warning in warnings[:10]:  # Show first 10
                html += f"<li>{warning}</li>"
            if len(warnings) > 10:
                html += f"<li><em>... and {len(warnings) - 10} more warnings</em></li>"
            html += "</ul>"

        # Info
        if info:
            html += '<h4 style="color: blue;">Info:</h4>'
            html += '<ul style="color: blue;">'
            for i in info[:5]:  # Show first 5
                html += f"<li>{i}</li>"
            if len(info) > 5:
                html += f"<li><em>... and {len(info) - 5} more info messages</em></li>"
            html += "</ul>"

        html += "</div>"
        return mark_safe(html)

    validation_report_display.short_description = "Master Excel Validation Report"

    def preparation_log_display(self, obj):
        """Display preparation log with HTML formatting."""
        if obj.preparation_log:
            return format_html(
                '<pre style="white-space: pre-wrap; max-height: 500px; overflow-y: auto;">{}</pre>', obj.preparation_log
            )
        return "No log entries"

    preparation_log_display.short_description = "Preparation Log"

    actions = ["prepare_data"]

    def prepare_data(self, request, queryset):
        """Action to trigger data preparation."""
        from .tasks import prepare_data_task

        for preparation in queryset:
            if preparation.status not in [
                DataPreparation.Status.READY,
                DataPreparation.Status.DOWNLOADING,
                DataPreparation.Status.EXTRACTING,
                DataPreparation.Status.PREPARING,
            ]:
                prepare_data_task.enqueue(preparation.id)
                self.message_user(request, f"Started preparation for {preparation.name}")
            else:
                self.message_user(request, f"Skipped {preparation.name} - already processing or ready", level="warning")

    prepare_data.short_description = "Prepare selected data"


@admin.register(SimulationPlot)
class SimulationPlotAdmin(admin.ModelAdmin):
    list_display = ("title", "modelrun", "plot_type", "product_type", "created_at")
    list_filter = ("plot_type", "product_type", "created_at")
    search_fields = ("title", "modelrun__id")
    readonly_fields = ("created_at",)

    fieldsets = (
        (None, {"fields": ("modelrun", "title", "plot_type", "product_type")}),
        ("Image", {"fields": ("image",)}),
        ("Metadata", {"fields": ("created_at",), "classes": ("collapse",)}),
    )


@admin.register(MasterExcelFile)
class MasterExcelFileAdmin(admin.ModelAdmin):
    list_display = ("name", "validation_status_badge", "is_template", "is_example", "created_at", "file_size")
    list_filter = ("validation_status", "is_template", "is_example", "created_at")
    search_fields = ("name", "description")
    readonly_fields = (
        "validation_status",
        "validation_report_display",
        "created_at",
        "updated_at",
        "file_size",
    )

    fieldsets = (
        (None, {"fields": ("name", "description", "file", "is_template", "is_example")}),
        ("Validation", {"fields": ("validation_status", "validation_report_display")}),
        ("Metadata", {"fields": ("created_at", "updated_at", "file_size"), "classes": ("collapse",)}),
    )

    def validation_status_badge(self, obj):
        """Display validation status as a colored badge."""
        colors = {
            "pending": "#6c757d",
            "valid": "#28a745",
            "warnings": "#ffc107",
            "invalid": "#dc3545",
        }
        color = colors.get(obj.validation_status, "#6c757d")
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px;">{}</span>',
            color,
            obj.get_validation_status_display(),
        )

    validation_status_badge.short_description = "Validation Status"

    def file_size(self, obj):
        """Display file size in MB."""
        if obj.file:
            size_mb = obj.file.size / (1024 * 1024)
            return f"{size_mb:.2f} MB"
        return "-"

    file_size.short_description = "File Size"

    def validation_report_display(self, obj):
        """Display validation report with HTML formatting."""
        if not obj.validation_report:
            return "No validation report"

        report = obj.validation_report
        summary = report.get("summary", {})
        errors = report.get("errors", [])
        warnings = report.get("warnings", [])
        info = report.get("info", [])
        validated_at = report.get("validated_at", "Unknown")

        html = '<div style="font-family: monospace; max-width: 800px;">'
        html += f"<p><strong>Validated at:</strong> {validated_at}</p>"

        # Summary
        html += "<h4>Summary:</h4>"
        html += "<ul>"
        html += f"<li>Errors: {summary.get('error_count', 0)}</li>"
        html += f"<li>Warnings: {summary.get('warning_count', 0)}</li>"
        html += f"<li>Info: {summary.get('info_count', 0)}</li>"
        html += "</ul>"

        # Errors
        if errors:
            html += '<h4 style="color: red;">Errors:</h4>'
            html += '<ul style="color: red;">'
            for error in errors[:10]:  # Show first 10
                html += f"<li>{error}</li>"
            if len(errors) > 10:
                html += f"<li><em>... and {len(errors) - 10} more errors</em></li>"
            html += "</ul>"

        # Warnings
        if warnings:
            html += '<h4 style="color: orange;">Warnings:</h4>'
            html += '<ul style="color: orange;">'
            for warning in warnings[:10]:  # Show first 10
                html += f"<li>{warning}</li>"
            if len(warnings) > 10:
                html += f"<li><em>... and {len(warnings) - 10} more warnings</em></li>"
            html += "</ul>"

        # Info
        if info:
            html += '<h4 style="color: blue;">Info:</h4>'
            html += '<ul style="color: blue;">'
            for i in info[:5]:  # Show first 5
                html += f"<li>{i}</li>"
            if len(info) > 5:
                html += f"<li><em>... and {len(info) - 5} more info messages</em></li>"
            html += "</ul>"

        html += "</div>"
        return mark_safe(html)

    validation_report_display.short_description = "Validation Report"

    actions = ["validate_files"]

    def validate_files(self, request, queryset):
        """Action to validate selected master Excel files."""
        for master_excel in queryset:
            try:
                master_excel.validate()
                if master_excel.validation_status == "invalid":
                    self.message_user(request, f"{master_excel.name}: Validation failed", level="error")
                elif master_excel.validation_status == "warnings":
                    self.message_user(request, f"{master_excel.name}: Valid with warnings", level="warning")
                else:
                    self.message_user(request, f"{master_excel.name}: Validation successful")
            except Exception as e:
                self.message_user(request, f"{master_excel.name}: Validation error - {str(e)}", level="error")

    validate_files.short_description = "Validate selected files"


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ('name', 'master_excel', 'start_year', 'end_year', 'created_by', 'created_at', 'variation_count')
    list_filter = ('created_at', 'start_year', 'end_year')
    search_fields = ('name', 'description')
    readonly_fields = ('created_at', 'updated_at', 'variation_count', 'run_count')
    raw_id_fields = ('master_excel', 'base_scenario', 'created_by')

    fieldsets = (
        (None, {
            'fields': ('name', 'description', 'master_excel', 'base_scenario', 'created_by')
        }),
        ('Simulation Timeframe', {
            'fields': ('start_year', 'end_year')
        }),
        ('Override Parameters', {
            'fields': ('technology_overrides', 'economic_overrides', 'geospatial_overrides', 'policy_overrides', 'agent_overrides'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at', 'variation_count', 'run_count'),
            'classes': ('collapse',)
        }),
    )

    def variation_count(self, obj):
        return obj.count_variations()
    variation_count.short_description = 'Active Variations'

    def run_count(self, obj):
        return obj.count_runs()
    run_count.short_description = 'Total Runs'


@admin.register(ScenarioVariation)
class ScenarioVariationAdmin(admin.ModelAdmin):
    list_display = ('name', 'scenario', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at', 'scenario')
    search_fields = ('name', 'description', 'scenario__name')
    readonly_fields = ('created_at',)
    raw_id_fields = ('scenario',)

    fieldsets = (
        (None, {
            'fields': ('scenario', 'name', 'description', 'is_active')
        }),
        ('Additional Overrides', {
            'fields': ('additional_overrides',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )


@admin.register(SensitivitySweep)
class SensitivitySweepAdmin(admin.ModelAdmin):
    list_display = ('name', 'scenario', 'parameter_path', 'variation_type', 'run_count', 'created_at')
    list_filter = ('variation_type', 'created_at', 'scenario')
    search_fields = ('name', 'parameter_path', 'scenario__name')
    readonly_fields = ('created_at', 'run_count')
    raw_id_fields = ('scenario',)

    fieldsets = (
        (None, {
            'fields': ('scenario', 'name', 'parameter_path')
        }),
        ('Variation Configuration', {
            'fields': ('base_value', 'variation_type', 'variation_values')
        }),
        ('Metadata', {
            'fields': ('created_at', 'run_count'),
            'classes': ('collapse',)
        }),
    )

    def run_count(self, obj):
        return obj.count_runs()
    run_count.short_description = 'Expected Runs'
