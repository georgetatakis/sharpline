"""Structured JSON-lines logging for import and model runs.

Each script appends to its own file in backend/logs/ (e.g. backend/logs/import.jsonl).
Every line is one JSON object with a timestamp, a run_id shared by all lines from
the same run, an event name, and any extra fields — queryable later with pandas
or jq, not just readable.
"""

import json
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

# backend/logs/ locally; /app/logs in Docker, which is the same folder via the mount.
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


class RunLog:
    def __init__(self, name: str, log_dir: Path | None = None):
        log_dir = log_dir or LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / f"{name}.jsonl"
        self.run_id = uuid.uuid4().hex[:12]
        # Fields collected during the run and written on the run_end line.
        self.summary: dict = {}

    def event(self, event: str, **fields) -> None:
        record = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        with self.path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")


@contextmanager
def logged_run(name: str, log_dir: Path | None = None, **context):
    """Log run_start, then run_end (with log.summary) or run_failed (and re-raise)."""
    log = RunLog(name, log_dir)
    log.event("run_start", **context)
    started = time.perf_counter()
    try:
        yield log
    except Exception as exc:
        log.event(
            "run_failed",
            error=f"{type(exc).__name__}: {exc}",
            duration_s=round(time.perf_counter() - started, 2),
            **log.summary,
        )
        raise
    log.event(
        "run_end", duration_s=round(time.perf_counter() - started, 2), **log.summary
    )
