from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.models import StoreEvent


def utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class InsertResult:
    accepted: int
    inserted: int
    duplicates: int
    duplicate_event_ids: list[str]


class StoreDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    camera_id TEXT,
                    source_id TEXT,
                    source_type TEXT,
                    source_label TEXT,
                    video_path TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    analysis_start TEXT,
                    analysis_end TEXT,
                    model_version TEXT,
                    calibration_version TEXT,
                    event_count INTEGER NOT NULL DEFAULT 0,
                    error TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_store_created
                    ON sessions(store_id, created_at);

                CREATE INDEX IF NOT EXISTS idx_sessions_status
                    ON sessions(status);

                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT,
                    store_id TEXT NOT NULL,
                    camera_id TEXT NOT NULL,
                    visitor_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    zone_id TEXT,
                    dwell_ms INTEGER NOT NULL,
                    is_staff INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_store_time
                    ON events(store_id, timestamp);

                CREATE INDEX IF NOT EXISTS idx_events_store_visitor
                    ON events(store_id, visitor_id);

                CREATE TABLE IF NOT EXISTS pos_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    basket_value_inr REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_pos_store_time
                    ON pos_transactions(store_id, timestamp);

                CREATE TABLE IF NOT EXISTS visitor_corrections (
                    correction_id TEXT PRIMARY KEY,
                    store_id TEXT NOT NULL,
                    session_id TEXT,
                    visitor_id TEXT NOT NULL,
                    is_staff INTEGER NOT NULL,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );

                CREATE INDEX IF NOT EXISTS idx_visitor_corrections_store_session
                    ON visitor_corrections(store_id, session_id, visitor_id, created_at);
                """
            )
            self._ensure_column(connection, "events", "session_id", "TEXT")
            connection.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_events_session_time
                    ON events(session_id, timestamp);

                CREATE INDEX IF NOT EXISTS idx_events_store_session_time
                    ON events(store_id, session_id, timestamp);
                """
            )
            connection.commit()

    @staticmethod
    def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        return {row["name"] for row in rows}

    @classmethod
    def _ensure_column(
        cls,
        connection: sqlite3.Connection,
        table: str,
        column: str,
        column_type: str,
    ) -> None:
        if column not in cls._columns(connection, table):
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")

    def create_session(self, session: dict) -> dict:
        now = utc_iso(datetime.now(timezone.utc))
        payload = {
            "session_id": session["session_id"],
            "store_id": session["store_id"],
            "camera_id": session.get("camera_id"),
            "source_id": session.get("source_id"),
            "source_type": session.get("source_type"),
            "source_label": session.get("source_label"),
            "video_path": session.get("video_path"),
            "status": session.get("status", "created"),
            "created_at": session.get("created_at") or now,
            "started_at": session.get("started_at"),
            "completed_at": session.get("completed_at"),
            "analysis_start": session.get("analysis_start"),
            "analysis_end": session.get("analysis_end"),
            "model_version": session.get("model_version"),
            "calibration_version": session.get("calibration_version"),
            "event_count": int(session.get("event_count", 0)),
            "error": session.get("error"),
            "metadata": session.get("metadata") or {},
        }

        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, store_id, camera_id, source_id, source_type, source_label,
                    video_path, status, created_at, started_at, completed_at, analysis_start,
                    analysis_end, model_version, calibration_version, event_count, error,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    store_id = excluded.store_id,
                    camera_id = COALESCE(excluded.camera_id, sessions.camera_id),
                    source_id = COALESCE(excluded.source_id, sessions.source_id),
                    source_type = COALESCE(excluded.source_type, sessions.source_type),
                    source_label = COALESCE(excluded.source_label, sessions.source_label),
                    video_path = COALESCE(excluded.video_path, sessions.video_path),
                    status = excluded.status,
                    started_at = COALESCE(excluded.started_at, sessions.started_at),
                    completed_at = COALESCE(excluded.completed_at, sessions.completed_at),
                    analysis_start = COALESCE(excluded.analysis_start, sessions.analysis_start),
                    analysis_end = COALESCE(excluded.analysis_end, sessions.analysis_end),
                    model_version = COALESCE(excluded.model_version, sessions.model_version),
                    calibration_version = COALESCE(excluded.calibration_version, sessions.calibration_version),
                    event_count = excluded.event_count,
                    error = excluded.error,
                    metadata_json = excluded.metadata_json
                """,
                (
                    payload["session_id"],
                    payload["store_id"],
                    payload["camera_id"],
                    payload["source_id"],
                    payload["source_type"],
                    payload["source_label"],
                    payload["video_path"],
                    payload["status"],
                    payload["created_at"],
                    payload["started_at"],
                    payload["completed_at"],
                    payload["analysis_start"],
                    payload["analysis_end"],
                    payload["model_version"],
                    payload["calibration_version"],
                    payload["event_count"],
                    payload["error"],
                    json.dumps(payload["metadata"], separators=(",", ":")),
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (payload["session_id"],),
            ).fetchone()
        return self._session_row_to_dict(row)

    def update_session(self, session_id: str, updates: dict) -> dict | None:
        allowed = {
            "status",
            "started_at",
            "completed_at",
            "analysis_start",
            "analysis_end",
            "event_count",
            "error",
            "metadata",
        }
        values = {key: value for key, value in updates.items() if key in allowed and value is not None}
        if not values:
            return self.get_session(session_id)

        assignments: list[str] = []
        params: list[object] = []
        for key, value in values.items():
            column = "metadata_json" if key == "metadata" else key
            assignments.append(f"{column} = ?")
            if key == "metadata":
                params.append(json.dumps(value, separators=(",", ":")))
            else:
                params.append(value)
        params.append(session_id)

        with closing(self.connect()) as connection:
            connection.execute(
                f"UPDATE sessions SET {', '.join(assignments)} WHERE session_id = ?",
                params,
            )
            connection.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict | None:
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._session_row_to_dict(row) if row else None

    def list_sessions(
        self,
        store_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        safe_limit = max(1, min(int(limit), 200))
        query = "SELECT * FROM sessions WHERE 1=1"
        params: list[object] = []
        if store_id:
            query += " AND store_id = ?"
            params.append(store_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC, session_id DESC LIMIT ?"
        params.append(safe_limit)

        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._session_row_to_dict(row) for row in rows]

    def count_sessions(self, store_id: str | None = None) -> int:
        query = "SELECT COUNT(*) AS count FROM sessions WHERE 1=1"
        params: list[object] = []
        if store_id:
            query += " AND store_id = ?"
            params.append(store_id)
        with closing(self.connect()) as connection:
            row = connection.execute(query, params).fetchone()
        return int(row["count"])

    @staticmethod
    def _ensure_session_for_event(connection: sqlite3.Connection, event: StoreEvent) -> None:
        if not event.session_id:
            return
        existing = connection.execute(
            "SELECT session_id FROM sessions WHERE session_id = ?",
            (event.session_id,),
        ).fetchone()
        if existing:
            return
        now = utc_iso(datetime.now(timezone.utc))
        connection.execute(
            """
            INSERT INTO sessions (
                session_id, store_id, camera_id, source_type, source_label, status,
                created_at, analysis_start, analysis_end, event_count, metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.session_id,
                event.store_id,
                event.camera_id,
                "event_ingest",
                "Auto-created from event ingestion",
                "created",
                now,
                utc_iso(event.timestamp),
                utc_iso(event.timestamp),
                0,
                json.dumps({"auto_created": True}, separators=(",", ":")),
            ),
        )

    @staticmethod
    def _refresh_session_event_counts(connection: sqlite3.Connection, session_ids: set[str]) -> None:
        for session_id in session_ids:
            row = connection.execute(
                """
                SELECT COUNT(*) AS event_count, MIN(timestamp) AS analysis_start, MAX(timestamp) AS analysis_end
                FROM events
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if not row:
                continue
            connection.execute(
                """
                UPDATE sessions
                SET event_count = ?, analysis_start = ?, analysis_end = ?
                WHERE session_id = ?
                """,
                (
                    int(row["event_count"]),
                    row["analysis_start"],
                    row["analysis_end"],
                    session_id,
                ),
            )

    def insert_events(self, events: Iterable[StoreEvent]) -> InsertResult:
        accepted = 0
        inserted = 0
        duplicate_event_ids: list[str] = []
        touched_session_ids: set[str] = set()

        with closing(self.connect()) as connection:
            for event in events:
                accepted += 1
                if event.session_id:
                    self._ensure_session_for_event(connection, event)
                    touched_session_ids.add(event.session_id)
                try:
                    connection.execute(
                        """
                        INSERT INTO events (
                            event_id, session_id, store_id, camera_id, visitor_id, event_type,
                            timestamp, zone_id, dwell_ms, is_staff, confidence,
                            metadata_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(event.event_id),
                            event.session_id,
                            event.store_id,
                            event.camera_id,
                            event.visitor_id,
                            event.event_type.value,
                            utc_iso(event.timestamp),
                            event.zone_id,
                            event.dwell_ms,
                            int(event.is_staff),
                            event.confidence,
                            json.dumps(event.metadata.model_dump(mode="json"), separators=(",", ":")),
                        ),
                    )
                    inserted += 1
                except sqlite3.IntegrityError:
                    duplicate_event_ids.append(str(event.event_id))
            self._refresh_session_event_counts(connection, touched_session_ids)
            connection.commit()

        return InsertResult(
            accepted=accepted,
            inserted=inserted,
            duplicates=len(duplicate_event_ids),
            duplicate_event_ids=duplicate_event_ids,
        )

    def fetch_events(
        self,
        store_id: str,
        start: str | None = None,
        end: str | None = None,
        session_id: str | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM events WHERE store_id = ?"
        params: list[str] = [store_id]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
        query += " ORDER BY timestamp ASC, event_id ASC"

        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()

        return [self._event_row_to_dict(row) for row in rows]

    def fetch_recent_events(
        self,
        store_id: str,
        limit: int = 50,
        session_id: str | None = None,
    ) -> list[dict]:
        safe_limit = max(1, min(int(limit), 200))
        query = """
                SELECT *
                FROM events
                WHERE store_id = ?
                """
        params: list[object] = [store_id]
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        query += """
                ORDER BY timestamp DESC, event_id DESC
                LIMIT ?
                """
        params.append(safe_limit)
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._event_row_to_dict(row) for row in reversed(rows)]

    def count_events(self, store_id: str | None = None, session_id: str | None = None) -> int:
        with closing(self.connect()) as connection:
            query = "SELECT COUNT(*) AS count FROM events WHERE 1=1"
            params: list[object] = []
            if store_id is not None:
                query += " AND store_id = ?"
                params.append(store_id)
            if session_id is not None:
                query += " AND session_id = ?"
                params.append(session_id)
            row = connection.execute(query, params).fetchone()
        return int(row["count"])

    def count_pos_transactions(self, store_id: str | None = None) -> int:
        with closing(self.connect()) as connection:
            if store_id is None:
                row = connection.execute("SELECT COUNT(*) AS count FROM pos_transactions").fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM pos_transactions WHERE store_id = ?",
                    (store_id,),
                ).fetchone()
        return int(row["count"])

    def event_count_by_store(self) -> dict[str, int]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT store_id, COUNT(*) AS count
                FROM events
                GROUP BY store_id
                ORDER BY store_id
                """
            ).fetchall()
        return {row["store_id"]: int(row["count"]) for row in rows}

    def pos_count_by_store(self) -> dict[str, int]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT store_id, COUNT(*) AS count
                FROM pos_transactions
                GROUP BY store_id
                ORDER BY store_id
                """
            ).fetchall()
        return {row["store_id"]: int(row["count"]) for row in rows}

    def upsert_pos_transactions(self, rows: Iterable[dict]) -> int:
        count = 0
        with closing(self.connect()) as connection:
            for row in rows:
                connection.execute(
                    """
                    INSERT INTO pos_transactions (
                        transaction_id, store_id, timestamp, basket_value_inr, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(transaction_id) DO UPDATE SET
                        store_id = excluded.store_id,
                        timestamp = excluded.timestamp,
                        basket_value_inr = excluded.basket_value_inr,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        row["transaction_id"],
                        row["store_id"],
                        row["timestamp"],
                        float(row["basket_value_inr"]),
                        json.dumps(row.get("metadata", {}), separators=(",", ":")),
                    ),
                )
                count += 1
            connection.commit()
        return count

    def fetch_pos_transactions(
        self,
        store_id: str,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM pos_transactions WHERE store_id = ?"
        params: list[str] = [store_id]
        if start:
            query += " AND timestamp >= ?"
            params.append(start)
        if end:
            query += " AND timestamp <= ?"
            params.append(end)
        query += " ORDER BY timestamp ASC, transaction_id ASC"

        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()

        return [
            {
                "transaction_id": row["transaction_id"],
                "store_id": row["store_id"],
                "timestamp": row["timestamp"],
                "basket_value_inr": float(row["basket_value_inr"]),
                "metadata": json.loads(row["metadata_json"] or "{}"),
            }
            for row in rows
        ]

    def upsert_visitor_correction(self, correction: dict) -> dict:
        now = utc_iso(datetime.now(timezone.utc))
        payload = {
            "correction_id": correction.get("correction_id") or f"CORR_{uuid.uuid4().hex}",
            "store_id": correction["store_id"],
            "session_id": correction.get("session_id"),
            "visitor_id": correction["visitor_id"],
            "is_staff": bool(correction["is_staff"]),
            "reason": correction.get("reason"),
            "created_at": correction.get("created_at") or now,
            "metadata": correction.get("metadata") or {},
        }
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT INTO visitor_corrections (
                    correction_id, store_id, session_id, visitor_id, is_staff,
                    reason, created_at, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(correction_id) DO UPDATE SET
                    store_id = excluded.store_id,
                    session_id = excluded.session_id,
                    visitor_id = excluded.visitor_id,
                    is_staff = excluded.is_staff,
                    reason = excluded.reason,
                    created_at = excluded.created_at,
                    metadata_json = excluded.metadata_json
                """,
                (
                    payload["correction_id"],
                    payload["store_id"],
                    payload["session_id"],
                    payload["visitor_id"],
                    int(payload["is_staff"]),
                    payload["reason"],
                    payload["created_at"],
                    json.dumps(payload["metadata"], separators=(",", ":")),
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM visitor_corrections WHERE correction_id = ?",
                (payload["correction_id"],),
            ).fetchone()
        return self._correction_row_to_dict(row)

    def fetch_visitor_corrections(
        self,
        store_id: str,
        session_id: str | None = None,
    ) -> list[dict]:
        query = "SELECT * FROM visitor_corrections WHERE store_id = ?"
        params: list[object] = [store_id]
        if session_id:
            query += " AND (session_id = ? OR session_id IS NULL)"
            params.append(session_id)
        query += " ORDER BY created_at ASC, correction_id ASC"
        with closing(self.connect()) as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._correction_row_to_dict(row) for row in rows]

    def last_event_by_store(self) -> list[dict]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                """
                SELECT store_id, MAX(timestamp) AS last_event_timestamp, COUNT(*) AS event_count
                FROM events
                GROUP BY store_id
                ORDER BY store_id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _event_row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "event_id": row["event_id"],
            "session_id": row["session_id"] if "session_id" in row.keys() else None,
            "store_id": row["store_id"],
            "camera_id": row["camera_id"],
            "visitor_id": row["visitor_id"],
            "event_type": row["event_type"],
            "timestamp": row["timestamp"],
            "zone_id": row["zone_id"],
            "dwell_ms": int(row["dwell_ms"]),
            "is_staff": bool(row["is_staff"]),
            "confidence": float(row["confidence"]),
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @staticmethod
    def _session_row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "store_id": row["store_id"],
            "camera_id": row["camera_id"],
            "source_id": row["source_id"],
            "source_type": row["source_type"],
            "source_label": row["source_label"],
            "video_path": row["video_path"],
            "status": row["status"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "analysis_start": row["analysis_start"],
            "analysis_end": row["analysis_end"],
            "model_version": row["model_version"],
            "calibration_version": row["calibration_version"],
            "event_count": int(row["event_count"]),
            "error": row["error"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    @staticmethod
    def _correction_row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "correction_id": row["correction_id"],
            "store_id": row["store_id"],
            "session_id": row["session_id"],
            "visitor_id": row["visitor_id"],
            "is_staff": bool(row["is_staff"]),
            "reason": row["reason"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }
