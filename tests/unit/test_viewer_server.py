"""Path and host safety of the scenario viewer's local server."""

import functools
import http.client
import http.server
import threading

from steelo.viewer import server


def test_resolve_run_file_refuses_paths_outside_the_run_folder(tmp_path):
    """
    Tests that a run file URL resolves to the file inside its run folder, and that a ../ path, a symlink pointing
    outside the run folder and an unknown run folder are refused.
    """
    run_dir = tmp_path / "output" / "sim_1"
    (run_dir / "viewer").mkdir(parents=True)
    (run_dir / "viewer" / "run.json").write_text("{}")
    (tmp_path / "output" / "other").write_text("outside")
    (run_dir / "outside").symlink_to(tmp_path / "output" / "other")
    runs = {"sim_1": run_dir}

    assert server.resolve_run_file(runs, "/runs/sim_1/viewer/run.json") == run_dir / "viewer" / "run.json"
    assert server.resolve_run_file(runs, "/runs/sim_1/../other") is None
    assert server.resolve_run_file(runs, "/runs/sim_1/outside") is None
    assert server.resolve_run_file(runs, "/runs/nope/viewer/run.json") is None


def test_server_refuses_requests_naming_another_host():
    """
    Tests that the viewer serves a request whose Host header is 127.0.0.1 or localhost on any port, as an ssh tunnel
    sends, and answers 404 to one naming another host, as a page pointing its own domain name at 127.0.0.1 would send.
    """
    handler = functools.partial(server.ViewerHandler, directory=str(server.APP_DIR))
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as httpd:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        port = httpd.server_address[1]
        hosts = [
            f"127.0.0.1:{port}",
            "LOCALHOST:9000",
            f"attacker.example:{port}",
            f"127.0.0.1.attacker.example:{port}",
        ]
        statuses = {}
        try:
            for host in hosts:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                connection.request("GET", "/", headers={"Host": host})
                statuses[host] = connection.getresponse().status
                connection.close()
        finally:
            httpd.shutdown()

    assert statuses == {
        f"127.0.0.1:{port}": 200,
        "LOCALHOST:9000": 200,
        f"attacker.example:{port}": 404,
        f"127.0.0.1.attacker.example:{port}": 404,
    }
