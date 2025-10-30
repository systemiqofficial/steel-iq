from django import template
from django.utils.safestring import mark_safe
import os

register = template.Library()


@register.filter
def basename(value):
    """Return the basename of a file path."""
    try:
        return os.path.basename(str(value))
    except:  # noqa
        return value


@register.simple_tag
def render_step_timing_table(step_timings):
    """Render high-level step timing table."""
    if not step_timings:
        return ""

    html = """
    <table class="table table-sm table-striped">
        <thead>
            <tr>
                <th>Step</th>
                <th class="text-end">Time (seconds)</th>
                <th class="text-end">Percentage</th>
            </tr>
        </thead>
        <tbody>
    """

    for step in step_timings:
        html += f"""
            <tr>
                <td>{step["name"]}</td>
                <td class="text-end">{step["duration"]}</td>
                <td class="text-end">{step["percentage"]}%</td>
            </tr>
        """

    html += """
        </tbody>
    </table>
    """

    return mark_safe(html)


@register.simple_tag(takes_context=True)
def render_file_timing_table(context, file_timings, preparation_pk):
    """Render detailed file creation timing table."""
    from django.urls import reverse

    if not file_timings:
        return ""

    # Decide how many files to show
    total_files = len(file_timings)
    display_limit = 50 if total_files <= 50 else 30

    html = """
    <table class="table table-sm table-striped">
        <thead>
            <tr>
                <th>File</th>
                <th>Source</th>
                <th class="text-end">Time (s)</th>
                <th class="text-center">Actions</th>
            </tr>
        </thead>
        <tbody>
    """

    # Show top files
    for i, file_info in enumerate(file_timings[:display_limit]):
        filename = file_info["filename"]
        source = file_info["source"]
        duration = file_info["duration"]

        # Make JSON and CSV files clickable (and other text files)
        clickable_extensions = (".json", ".csv", ".txt", ".log", ".xml", ".yml", ".yaml")
        if filename.endswith(clickable_extensions):
            url = reverse("view-prepared-file", kwargs={"pk": preparation_pk, "filename": filename})
            file_link = f'<a href="{url}" class="text-decoration-none" title="View {filename}">{filename}</a>'
        else:
            file_link = filename

        # Format duration - show dash for untracked files
        duration_display = f"{duration}" if duration > 0 else "-"

        # Add delete button for JSON/CSV files that can be regenerated
        deleteable_extensions = (".json", ".csv")
        if filename.endswith(deleteable_extensions):
            delete_url = reverse("delete-prepared-file", kwargs={"pk": preparation_pk, "filename": filename})
            delete_button = f'''
                <button hx-delete="{delete_url}" 
                        hx-confirm="Are you sure you want to delete {filename}? You can regenerate it by clicking 'Re-run Preparation'."
                        hx-target="#file-row-{i}"
                        hx-swap="outerHTML"
                        class="btn btn-outline-danger btn-sm" 
                        title="Delete {filename}">
                    <i class="fas fa-trash"></i>
                </button>
            '''
        else:
            delete_button = '<span class="text-muted">-</span>'

        html += f"""
            <tr id="file-row-{i}">
                <td>{file_link}</td>
                <td><small class="text-muted">{source}</small></td>
                <td class="text-end">{duration_display}</td>
                <td class="text-center">{delete_button}</td>
            </tr>
        """

    # Add "more files" row if needed
    if total_files > display_limit:
        html += f"""
            <tr>
                <td colspan="2" class="text-muted">... and {total_files - display_limit} more files</td>
                <td class="text-end text-muted">...</td>
                <td class="text-center text-muted">-</td>
            </tr>
        """

    # Add total row (only for tracked files with duration > 0)
    total_time = sum(f["duration"] for f in file_timings if f["duration"] > 0)
    html += f"""
            <tr class="table-active fw-bold">
                <td>Total (tracked files)</td>
                <td>All sources</td>
                <td class="text-end">{round(total_time, 2)}</td>
                <td class="text-center text-muted">-</td>
            </tr>
        """

    html += """
        </tbody>
    </table>
    """

    return mark_safe(html)
