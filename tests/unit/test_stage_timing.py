import csv

from steelo.utilities.stage_timing import (
    STAGE_TIMING_FILE,
    StageTimer,
    get_stage_records,
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
