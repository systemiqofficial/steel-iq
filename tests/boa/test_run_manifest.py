import pytest

from boa.config.paths import PathConfig
from boa.config import run_manifest


def _cfg(tmp_path, **kw):
    cfg = PathConfig.from_root(tmp_path, **kw)
    cfg.costs_dir.mkdir(parents=True, exist_ok=True)
    cfg.input_data_path.write_bytes(b"xlsx-v1")
    return cfg


def test_creates_then_appends(tmp_path):
    cfg = _cfg(tmp_path, input_set="cds", cost_set="c1")
    m1 = run_manifest.record_invocation(cfg, "build-cache", ["--region", "EU"])
    m2 = run_manifest.record_invocation(cfg, "query", ["--region", "EU"])
    assert m1["created_at"] == m2["created_at"]
    assert [i["command"] for i in m2["invocations"]] == ["build-cache", "query"]
    assert m2["provenance"]["cost_set"] == "c1"


def test_records_resolved_parameters(tmp_path):
    cfg = _cfg(tmp_path, input_set="cds", cost_set="c1")
    params = {"demand_mw": 1000.0, "coverage": 0.85, "p": 15, "samples": 1000, "years": [2025, 2026]}
    m = run_manifest.record_invocation(cfg, "run", [], parameters=params)
    assert m["invocations"][-1]["parameters"] == params


def test_refuses_mixed_provenance(tmp_path):
    cfg = _cfg(tmp_path, input_set="cds", cost_set="c1", run="shared")
    run_manifest.record_invocation(cfg, "query", [])
    cfg.input_data_path.write_bytes(b"xlsx-v2")
    with pytest.raises(RuntimeError, match="input_data_sha256"):
        run_manifest.record_invocation(cfg, "query", [])
