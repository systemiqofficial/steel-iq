import csv

from steelo.utilities.stage_timing import (
    STAGE_METRICS_FILE,
    STAGE_TIMING_FILE,
    StageTimer,
    get_metric_records,
    get_stage_records,
    record_metric,
    reset_stage_timings,
    set_stage_year,
    stop_stage,
    write_stage_timings,
)


def test_stop_stage_records_year_stage_and_both_clocks():
    reset_stage_timings()
    set_stage_year(2026)
    timer = StageTimer()
    sum(i * i for i in range(200_000))

    wall_s = stop_stage(timer, "demo")

    (record,) = get_stage_records()
    assert (record.year, record.stage) == (2026, "demo")
    assert record.wall_s == wall_s > 0
    assert record.cpu_s > 0


def test_write_stage_timings_replaces_file_and_reset_clears(tmp_path):
    reset_stage_timings()
    set_stage_year(2025)
    stop_stage(StageTimer(), "a")
    write_stage_timings(tmp_path)
    set_stage_year(2026)
    stop_stage(StageTimer(), "b")
    path = write_stage_timings(tmp_path)

    assert path == tmp_path / STAGE_TIMING_FILE
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["year"], r["stage"]) for r in rows] == [("2025", "a"), ("2026", "b")]

    reset_stage_timings()
    assert get_stage_records() == []


def test_metrics_are_written_with_their_year_and_cleared_by_reset(tmp_path):
    reset_stage_timings()
    set_stage_year(2026)
    record_metric("lp_columns", 1_680_000)
    record_metric("clusters", 194)
    write_stage_timings(tmp_path)

    with open(tmp_path / STAGE_METRICS_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [(r["year"], r["metric"], r["value"]) for r in rows] == [
        ("2026", "lp_columns", "1680000"),
        ("2026", "clusters", "194"),
    ]

    reset_stage_timings()
    assert get_metric_records() == []
