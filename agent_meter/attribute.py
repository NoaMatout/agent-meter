"""Attributing calls to the task that made them.

The relay knows which agent called, because each agent has its own port. It
does not know which scheduled job was running at the time, and that is the
question worth answering: "my agent costs nine cents a day" is a number, "my
mail brief costs one and a half cents and my web watch two and a half" is a
decision.

Rather than asking the agent framework to cooperate, this reads the
scheduler's own execution log. Any scheduler that records a job id with a start
and an end will do, which is most of them. Nothing is modified, and the log is
opened read-only.
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime

INTERACTIVE = "interactive"


def _keys(row) -> set:
    """sqlite3.Row exposes keys(); a plain dict does not need the call."""
    return set(row.keys()) if hasattr(row, "keys") else set()


@dataclass(frozen=True)
class Window:
    """One execution of one job.

    `label` names the agent this scheduler drives. With two agents on one host,
    each has its own scheduler and its own port, and a call from one must never
    be attributed to a job of the other. None matches any agent.
    """

    job: str
    start: float
    end: float
    label: str | None = None

    @property
    def length(self) -> float:
        return self.end - self.start


def _epoch(value) -> float | None:
    """Accept an epoch number or an ISO 8601 string."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def load_windows(config: dict) -> list[Window]:
    """Read one scheduler's execution log into windows.

    Rows without an end are skipped: a job still running has no duration, and
    guessing one would silently attribute later calls to it.
    """
    table = config.get("table", "executions")
    job_col = config.get("job_column", "job_id")
    start_col = config.get("start_column", "started_at")
    end_col = config.get("end_column", "finished_at")
    names = config.get("names", {})

    cx = sqlite3.connect(f"file:{config['database']}?mode=ro", uri=True)
    windows = []
    for job, start, end in cx.execute(
        f'select "{job_col}", "{start_col}", "{end_col}" from "{table}"'
    ):
        s, e = _epoch(start), _epoch(end)
        if s is None or e is None or e < s:
            continue
        windows.append(Window(names.get(str(job), str(job)), s, e, config.get("label")))
    cx.close()
    return windows


def attribute(calls: list, windows: list[Window]) -> dict[str, list]:
    """Group calls by the job that was running when each was made.

    When executions overlap, the shortest window wins. A long job that
    delegates to a short one would otherwise absorb the whole cost, and the
    shorter window is always the more specific claim.

    Every call lands in exactly one group, so the totals per job always add up
    to the overall total. Calls outside every window are interactive use.
    """
    ordered = sorted(windows, key=lambda w: w.start)
    starts = [w.start for w in ordered]
    longest = max((w.length for w in ordered), default=0.0)

    grouped: dict[str, list] = {}
    for call in calls:
        ts = call["ts"]
        best: Window | None = None
        # Windows starting after this call cannot contain it. Walking back from
        # there, no window starting earlier than the longest known execution
        # can still be open, which bounds the scan.
        label = call["label"] if "label" in _keys(call) else None
        for w in reversed(ordered[: bisect_right(starts, ts)]):
            if w.start < ts - longest:
                break
            if w.end < ts:
                continue
            if w.label is not None and label is not None and w.label != label:
                continue
            if best is None or w.length < best.length:
                best = w
        grouped.setdefault(best.job if best else INTERACTIVE, []).append(call)
    return grouped
