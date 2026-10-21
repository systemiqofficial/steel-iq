"""Views that serve the offline scenario viewer from the app, over the app's runs and the CLI runs beside them.

The page and its assets come from the ``steelo.viewer`` package; ``manifest.json``, ``inputs.json`` and ``geo.json``
from its catalogue, over the runs found on disk again with each of them; each run's files through the CLI server's
path check. Every view answers GET only and may be framed by the app's own pages.

Notes:
    ``steelo.viewer``'s catalogue and server import ``steelo.simulation``, which takes over a second, so the views
    import them when first called rather than with the app's URLs.
"""

import logging
from pathlib import Path

from django.http import (
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponsePermanentRedirect,
    HttpResponseServerError,
    JsonResponse,
)
from django.http.response import HttpResponseBase
from django.urls import reverse
from django.views import static
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET

from steelo.config import get_steelo_home

from .models import ModelRun

logger = logging.getLogger(__name__)

# The runs the last manifest, inputs or geo request found, by folder name; the run file view serves these
RUNS: dict[str, Path] = {}


def cli_base() -> Path | None:
    """The output base of the CLI runs on this machine, when there is one.

    Returns:
        ``$STEELO_HOME/output`` when that folder exists, else ``None``.
    """
    output_base = get_steelo_home() / "output"
    return output_base if output_base.is_dir() else None


def viewer_runs() -> tuple[list[Path], dict[str, ModelRun]]:
    """The runs the viewer lists, found on disk again, and the app run behind each app run folder.

    Returns:
        The run folders holding ``viewer/run.json``, oldest first: the output folders of the finished ``ModelRun``s
        and the CLI runs under :func:`cli_base`; and the finished ``ModelRun``s by output folder name.

    Notes:
        ``RUNS`` is set to the returned folders, for the run file requests that follow. A CLI folder named like an
        app run's folder (``run_<pk>``) is skipped with a warning, so the app run is the one served under that name.
    """
    from steelo.viewer import catalogue

    global RUNS
    app_runs = {
        Path(run.output_directory).name: run
        for run in ModelRun.objects.filter(state=ModelRun.RunState.FINISHED).exclude(output_directory="")
    }
    found = catalogue.discover_runs(cli_base(), [Path(run.output_directory) for run in app_runs.values()])
    clashes = [
        run_dir
        for run_dir in found
        if run_dir.name in app_runs and run_dir != Path(app_runs[run_dir.name].output_directory)
    ]
    if clashes:
        logger.warning(f"skipped, named like an app run's folder: {', '.join(str(run_dir) for run_dir in clashes)}")
    runs = [run_dir for run_dir in found if run_dir not in clashes]
    RUNS = {run_dir.name: run_dir for run_dir in runs}
    return runs, app_runs


@require_GET
@xframe_options_sameorigin
def viewer_index(request: HttpRequest) -> FileResponse:
    """The viewer page, which fetches everything else relative to ``/viewer/``.

    Args:
        request: The GET request.

    Returns:
        The package's ``app/index.html``.
    """
    from steelo.viewer import server

    return FileResponse((server.APP_DIR / "index.html").open("rb"))


@require_GET
@xframe_options_sameorigin
def viewer_plotly(request: HttpRequest) -> FileResponse:
    """The Plotly bundle the viewer's pages load, from the installed ``plotly`` package.

    Args:
        request: The GET request.

    Returns:
        ``plotly.min.js``.
    """
    from steelo.viewer import server

    return FileResponse(server.PLOTLY_JS.open("rb"))


@require_GET
@xframe_options_sameorigin
def viewer_asset(request: HttpRequest, path: str) -> FileResponse:
    """One of the viewer's scripts, styles or images.

    Args:
        request: The GET request.
        path: The path under the package's ``app/assets/``.

    Returns:
        The file.

    Raises:
        Http404: If the path leaves ``app/assets/`` or names no file.
    """
    from steelo.viewer import server

    assets = server.APP_DIR / "assets"
    asset = assets / path
    try:
        asset.resolve().relative_to(assets.resolve())
    except ValueError:
        raise Http404("Not a viewer asset")
    if not asset.is_file():
        raise Http404("Not a viewer asset")
    return FileResponse(asset.open("rb"))


@require_GET
@xframe_options_sameorigin
def viewer_manifest(request: HttpRequest) -> JsonResponse:
    """``manifest.json`` over the runs found again, with the "Made in" filter; an app run links to its run page.

    Args:
        request: The GET request.

    Returns:
        The catalogue's manifest.
    """
    from steelo.viewer import catalogue

    runs, app_runs = viewer_runs()
    manifest = catalogue.build_manifest(runs, with_made_in=True)
    for run in manifest["sets"][0]["runs"]:
        if run["dir"] in app_runs:
            run["link"] = reverse("modelrun-detail", args=[app_runs[run["dir"]].pk])
    return JsonResponse(manifest)


@require_GET
@xframe_options_sameorigin
def viewer_inputs(request: HttpRequest) -> HttpResponse:
    """``inputs.json``, the View data pane's diff of the runs found again.

    Args:
        request: The GET request.

    Returns:
        The catalogue's inputs; a 500 with the error as plain text when the runs' inputs do not line up.
    """
    from steelo.viewer import catalogue

    runs, _ = viewer_runs()
    manifest = catalogue.build_manifest(runs, with_made_in=True)
    try:
        inputs = catalogue.build_inputs(runs, manifest)
    except ValueError as error:
        # The View data pane shows the body, so a mismatched topic is named there
        return HttpResponseServerError(str(error), content_type="text/plain; charset=utf-8")
    return JsonResponse(inputs)


@require_GET
@xframe_options_sameorigin
def viewer_geo(request: HttpRequest) -> FileResponse:
    """``geo.json``: the newest run's copy among the runs found again.

    Args:
        request: The GET request.

    Returns:
        The file.

    Raises:
        Http404: If no run holds one.
    """
    from steelo.viewer import catalogue

    runs, _ = viewer_runs()
    geo = catalogue.geo_path(runs)
    if geo is None:
        raise Http404("No run holds geo.json")
    return FileResponse(geo.open("rb"))


@require_GET
@xframe_options_sameorigin
def viewer_run_file(request: HttpRequest, folder: str, path: str = "") -> HttpResponseBase:
    """A file of a run the last manifest, inputs or geo request found, or a listing of one of its folders.

    Args:
        request: The GET request.
        folder: The run folder's name.
        path: The path inside the run folder; empty for the folder itself.

    Returns:
        The file; a folder's listing, or a redirect to the folder's URL with its trailing slash.

    Raises:
        Http404: If the folder is not a listed run, or the path leaves it or names nothing.
    """
    from steelo.viewer import server

    runs = RUNS
    run_file = server.resolve_run_file(runs, server.RUNS_PREFIX + folder + "/" + path)
    if run_file is None or not run_file.exists():
        raise Http404("Not a file of a listed run")
    if run_file.is_dir():
        if not request.path.endswith("/"):
            return HttpResponsePermanentRedirect(request.path + "/")
        return static.serve(request, path, document_root=str(runs[folder]), show_indexes=True)
    return FileResponse(run_file.open("rb"))
