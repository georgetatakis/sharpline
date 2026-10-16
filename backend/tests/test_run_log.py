import json

import pytest

from app.run_log import logged_run


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_successful_run_logs_start_and_end_with_summary(tmp_path):
    with logged_run("example", tmp_path, season=2024) as log:
        log.summary["rows"] = 10

    start, end = read_lines(tmp_path / "example.jsonl")
    assert start["event"] == "run_start" and start["season"] == 2024
    assert end["event"] == "run_end" and end["rows"] == 10
    assert start["run_id"] == end["run_id"]
    assert "duration_s" in end


def test_failed_run_logs_error_and_reraises(tmp_path):
    with pytest.raises(ValueError):
        with logged_run("example", tmp_path):
            raise ValueError("boom")

    lines = read_lines(tmp_path / "example.jsonl")
    assert lines[-1]["event"] == "run_failed"
    assert lines[-1]["error"] == "ValueError: boom"


def test_runs_append_with_distinct_run_ids(tmp_path):
    for _ in range(2):
        with logged_run("example", tmp_path):
            pass

    lines = read_lines(tmp_path / "example.jsonl")
    assert len(lines) == 4
    assert lines[0]["run_id"] != lines[2]["run_id"]
