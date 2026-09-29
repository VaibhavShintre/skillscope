from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from skillscope.models import AnalysisResult, LocalDataSummary, PrivacySettings


def default_database_path() -> Path:
    configured = os.environ.get("SKILLSCOPE_DATA_DIR")
    root = Path(configured) if configured else Path.home() / ".skillscope"
    return root / "skillscope.db"


class LocalStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    project_name TEXT NOT NULL,
                    value TEXT NOT NULL
                );
                """
            )

    def get_privacy(self) -> PrivacySettings:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = 'privacy'").fetchone()
        return PrivacySettings.model_validate_json(row["value"]) if row else PrivacySettings()

    def save_privacy(self, settings: PrivacySettings) -> PrivacySettings:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO settings(key, value) VALUES('privacy', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (settings.model_dump_json(),),
            )
        return settings

    def save_analysis(self, result: AnalysisResult) -> AnalysisResult:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO analyses(id, created_at, project_name, value)
                VALUES (?, ?, ?, ?)
                """,
                (
                    result.analysis_id,
                    result.created_at.isoformat(),
                    result.project.name,
                    result.model_dump_json(),
                ),
            )
        return result

    def get_analysis(self, analysis_id: str) -> AnalysisResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM analyses WHERE id = ?", (analysis_id,)
            ).fetchone()
        return AnalysisResult.model_validate_json(row["value"]) if row else None

    def export_all(self) -> dict[str, object]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT value FROM analyses ORDER BY created_at DESC"
            ).fetchall()
        return {
            "privacy": self.get_privacy().model_dump(mode="json"),
            "analyses": [json.loads(row["value"]) for row in rows],
        }

    def delete_all(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM analyses")
            connection.execute("DELETE FROM settings")

    def summary(self) -> LocalDataSummary:
        with self._connect() as connection:
            count = connection.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        privacy = self.get_privacy()
        return LocalDataSummary(
            analyses=count,
            database_path=str(self.path),
            monitoring_preference=privacy.monitoring_mode,
        )
