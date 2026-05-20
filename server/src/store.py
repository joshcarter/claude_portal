import sqlite3
import time
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts              INTEGER NOT NULL,
    five_hour       REAL,
    seven_day       REAL,
    seven_day_opus  REAL
);
CREATE INDEX IF NOT EXISTS samples_ts ON samples(ts);
"""


class Store:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        import logging
        try:
            with self._connect() as conn:
                conn.executescript(_SCHEMA)
        except Exception as exc:
            logging.error("DB init failed, recreating: %s", exc)
            Path(self.db_path).unlink(missing_ok=True)
            with self._connect() as conn:
                conn.executescript(_SCHEMA)

    def insert(
        self,
        ts: int,
        five_hour: float,
        seven_day: float,
        seven_day_opus: Optional[float],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO samples VALUES (?, ?, ?, ?)",
                (ts, five_hour, seven_day, seven_day_opus),
            )

    def recent_five_hour(self, since_ts: int) -> list[tuple[int, float]]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT ts, five_hour FROM samples WHERE ts >= ? AND five_hour IS NOT NULL ORDER BY ts",
                (since_ts,),
            ).fetchall()

    def hourly_peaks(self, hours: int) -> list[dict]:
        now = int(time.time())
        # align to start of current UTC hour
        hour_start = (now // 3600) * 3600
        start_ts = hour_start - (hours - 1) * 3600

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT (ts / 3600) * 3600 AS h, MAX(five_hour)
                FROM samples
                WHERE ts >= ? AND five_hour IS NOT NULL
                GROUP BY h
                ORDER BY h
                """,
                (start_ts,),
            ).fetchall()

        peaks = {row[0]: row[1] for row in rows}
        return [
            {"hour_unix": start_ts + i * 3600, "five_hour_peak": peaks.get(start_ts + i * 3600)}
            for i in range(hours)
        ]

    def prune(self, older_than_ts: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM samples WHERE ts < ?", (older_than_ts,))
