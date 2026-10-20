"""The ``steelo-viewer`` command: serve the scenario viewer for local runs on 127.0.0.1, or rebuild their files.

The app comes from ``app/`` beside this module, Plotly from the installed ``plotly`` package, ``manifest.json``,
``inputs.json`` and ``geo.json`` from the catalogue on every request, over the runs found on disk again each time, and
each run's files from its folder under ``runs/<folder>/``, refused when the resolved path leaves the run folder.
"""

import argparse
import functools
import http.server
import json
import logging
import os
import sys
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any

import plotly

from . import catalogue, extract

logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).parent / "app"
PLOTLY_JS = Path(plotly.__file__).parent / "package_data" / "plotly.min.js"
RUNS_PREFIX = "/runs/"
# An empty path never opens, so the parent handler answers 404
NOT_FOUND = ""


def decoded_path(path: str) -> str:
    """A request path without its query or fragment, percent-decoded.

    Args:
        path: The raw request path.

    Returns:
        The decoded path, as the parent handler's ``translate_path`` decodes it.
    """
    return urllib.parse.unquote(path.split("?", 1)[0].split("#", 1)[0])


def resolve_run_file(runs: dict[str, Path], url_path: str) -> Path | None:
    """The file or folder a ``/runs/<folder>/<path>`` URL names, when it lies inside that run folder.

    Args:
        runs: The served run folders by folder name.
        url_path: The decoded URL path, starting with ``/runs/``.

    Returns:
        ``runs[folder] / path`` when its resolved path lies under the run folder's resolved path, symlinks
        resolved on both sides; None for an unknown folder or a path that leaves the run folder.
    """
    folder, _, rel = url_path.removeprefix(RUNS_PREFIX).partition("/")
    if folder not in runs:
        return None
    candidate = runs[folder] / rel
    try:
        candidate.resolve().relative_to(runs[folder].resolve())
    except ValueError:
        return None
    return candidate


class ViewerHandler(http.server.SimpleHTTPRequestHandler):
    """Request handler for the viewer: the three set files per request, everything else as files on disk."""

    output_base: Path | None = None
    run_dirs: list[Path] = []
    runs: dict[str, Path] = {}

    def refresh_runs(self) -> list[Path]:
        """Find the served runs on disk again, for this request and the run file requests after it.

        Returns:
            The runs holding ``viewer/run.json`` under ``output_base`` or among ``run_dirs``, oldest first;
            ``runs`` is set to them by folder name.

        Notes:
            Each set file calls it, so a run finished since start-up is listed, and a run that lost ``run.json``
            (deleted, or part-way through ``regenerate``) is skipped instead of failing the request.
        """
        found = catalogue.discover_runs(self.output_base, self.run_dirs)
        ViewerHandler.runs = {run_dir.name: run_dir for run_dir in found}
        return found

    def do_GET(self) -> None:
        """Answer ``/manifest.json`` and ``/inputs.json`` from the catalogue, anything else as a file."""
        url_path = decoded_path(self.path)
        if url_path == "/manifest.json":
            self.send_body(200, "application/json", catalogue.build_manifest(self.refresh_runs(), with_made_in=False))
        elif url_path == "/inputs.json":
            run_dirs = self.refresh_runs()
            manifest = catalogue.build_manifest(run_dirs, with_made_in=False)
            try:
                inputs = catalogue.build_inputs(run_dirs, manifest)
            except ValueError as error:
                # The View data pane shows the body, so a mismatched topic is named there
                self.send_body(500, "text/plain; charset=utf-8", str(error))
                return
            self.send_body(200, "application/json", inputs)
        else:
            super().do_GET()

    def send_body(self, status: int, content_type: str, body: Any) -> None:
        """Send one complete response.

        Args:
            status: The HTTP status.
            content_type: The Content-Type header.
            body: A string, sent as UTF-8, or anything else, sent as compact JSON.
        """
        text = body if isinstance(body, str) else json.dumps(body, separators=(",", ":"))
        data = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def translate_path(self, path: str) -> str:
        """Map a URL to the file it serves, or to a path that does not exist.

        Args:
            path: The raw request path.

        Returns:
            ``app/index.html`` for ``/`` and ``/index.html``; the Plotly bundle for
            ``/assets/vendor/plotly.min.js``; a file under ``app/`` for ``/assets/...``; the newest run's
            ``geo.json`` for ``/geo.json``, from the runs found again; a file of a run the last set file found,
            through :func:`resolve_run_file`, for ``/runs/...``; ``NOT_FOUND`` otherwise, so the parent answers 404.

        Notes:
            ``/assets/...`` goes through the parent's own mapping onto ``app/``, which drops ``..`` segments.
        """
        url_path = decoded_path(path)
        if url_path in ("/", "/index.html"):
            return str(APP_DIR / "index.html")
        if url_path == "/assets/vendor/plotly.min.js":
            return str(PLOTLY_JS)
        if url_path.startswith("/assets/"):
            return super().translate_path(path)
        if url_path == "/geo.json":
            geo = catalogue.geo_path(self.refresh_runs())
            return NOT_FOUND if geo is None else str(geo)
        if url_path.startswith(RUNS_PREFIX):
            run_file = resolve_run_file(self.runs, url_path)
            return NOT_FOUND if run_file is None else str(run_file)
        return NOT_FOUND

    def log_message(self, format: str, *args: Any) -> None:
        """Send the per-request log line to the module logger at DEBUG, so the terminal stays quiet.

        Args:
            format: The parent's format string.
            *args: Its arguments.
        """
        logger.debug(format % args)


def serve(output_base: Path | None, run_dirs: list[Path], runs: list[Path], port: int, open_browser: bool) -> None:
    """Serve the viewer on 127.0.0.1 until Ctrl-C.

    Args:
        output_base: The output base whose runs are served, or None.
        run_dirs: The run folders named one by one.
        runs: The runs found at start-up, served until the next set file finds them again.
        port: The port; 0 picks a free one.
        open_browser: Whether to open the viewer in the default browser.
    """
    ViewerHandler.output_base = output_base
    ViewerHandler.run_dirs = run_dirs
    ViewerHandler.runs = {run_dir.name: run_dir for run_dir in runs}
    handler = functools.partial(ViewerHandler, directory=str(APP_DIR))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        print(f"Scenario viewer at {url}", flush=True)
        print(f"Runs served: {len(runs)}; press Ctrl-C to stop", flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main(argv: list[str] | None = None) -> int:
    """The ``steelo-viewer`` command: serve the viewer, or ``steelo-viewer regenerate RUN_DIR ...``.

    Args:
        argv: Command-line arguments; None reads ``sys.argv``.

    Returns:
        0 on success, 1 when no run is found or ``regenerate`` fails.

    Notes:
        With RUN_DIRs only those runs are served; with none, every run under the output base
        (``--output-dir``, default ``<steelo-home>/output``). The runs are found again on disk with every set
        file, so a run finished while the viewer is open shows on the next page load.
    """
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if argv[:1] == ["regenerate"]:
        return extract.regenerate(argv[1:])
    parser = argparse.ArgumentParser(prog="steelo-viewer", description="Serve the scenario viewer for local runs.")
    parser.add_argument(
        "run_dirs", nargs="*", type=Path, metavar="RUN_DIR", help="run folder to serve (default: every run)"
    )
    parser.add_argument(
        "--steelo-home",
        type=Path,
        default=Path(os.environ.get("STEELO_HOME", str(Path.home() / ".steelo"))),
        help="STEELO_HOME directory (default: ~/.steelo or $STEELO_HOME)",
    )
    parser.add_argument(
        "--output-dir", type=Path, help="output base holding the run folders (default: $STEELO_HOME/output)"
    )
    parser.add_argument("--port", type=int, default=0, help="port to serve on (default: a free port)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the viewer in the browser")
    args = parser.parse_args(argv)

    # Folder names are the URL keys; the folders under one output base cannot share one
    names = [run_dir.name for run_dir in args.run_dirs]
    shared = [str(run_dir) for run_dir in args.run_dirs if names.count(run_dir.name) > 1]
    if shared:
        logger.error(f"run folders share a folder name, serve them one at a time: {', '.join(shared)}")
        return 1
    output_base = None if args.run_dirs else args.output_dir or args.steelo_home / "output"
    if output_base is not None and not output_base.is_dir():
        logger.error(f"no output folder at {output_base}")
        return 1
    runs = catalogue.discover_runs(output_base, args.run_dirs)
    if not runs:
        logger.error("no run with viewer/run.json found")
        return 1
    serve(output_base, args.run_dirs, runs, args.port, not args.no_browser)
    return 0
