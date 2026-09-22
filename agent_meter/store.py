"""Where calls are recorded. Counters only, never content."""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from .usage import Usage

SCHEMA = """
create table if not exists calls (
    id integer primary key,
    ts real not null,
    label text not null,
    model text,
    path text,
    status integer,
    duration real,
    input_total integer default 0,
    cache_read integer default 0,
    cache_write integer default 0,
    input_fresh integer default 0,
    output integer default 0,
    error text
);
create index if not exists idx_calls_ts on calls(ts);
"""

MIGRATIONS = ["alter table calls add column error text"]


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as c:
            c.executescript(SCHEMA)
            for sql in MIGRATIONS:
                try:
                    c.execute(sql)
                except sqlite3.OperationalError:
                    pass  # already applied

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=15)
        c.execute("pragma journal_mode=wal")
        return c

    def record(self, label: str, model: str | None, path: str | None,
               status: int, duration: float, usage: Usage | None,
               error: str | None = None) -> None:
        u = usage or Usage()
        with self._lock, self._connect() as cx:
            cx.execute(
                "insert into calls(ts, label, model, path, status, duration,"
                " input_total, cache_read, cache_write, input_fresh, output, error)"
                " values(?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), label, model, path, status, duration,
                 u.input_total, u.cache_read, u.cache_write, u.input_fresh, u.output, error),
            )

    def read(self, since: float, until: float | None = None) -> list[sqlite3.Row]:
        with self._connect() as cx:
            cx.row_factory = sqlite3.Row
            return list(cx.execute(
                "select * from calls where ts >= ? and ts < ? order by ts",
                (since, until if until is not None else time.time() + 1),
            ))
