
import sqlite3
from pathlib import Path
from typing import Iterator

from issuehunter.config import get_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    github_number INTEGER NOT NULL,
    repository_full_name TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    html_url TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    language TEXT,
    labels_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(repository_full_name, github_number)
);

CREATE INDEX IF NOT EXISTS idx_issues_state
ON issues(state);

CREATE INDEX IF NOT EXISTS idx_issues_repository
ON issues(repository_full_name);

CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER NOT NULL,
    summary TEXT NOT NULL,
    analysis_json TEXT NOT NULL,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY(issue_id) REFERENCES issues(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_analyses_issue
ON analyses(issue_id);
"""


class Storage:
    """SQLite storage manager for AutoIssueHunter."""

    def __init__(self, database_path: Path | None = None) -> None:
        settings = get_settings()

        self.database_path = database_path or settings.database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        """Create and configure a SQLite connection."""
        connection = sqlite3.connect(
            self.database_path,
            timeout=30,
        )

        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")

        return connection

    def initialize(self) -> None:
        """Create the database schema if it does not exist."""
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    def health_check(self) -> bool:
        """Return True when the database can execute a simple query."""
        try:
            with self.connect() as connection:
                result = connection.execute("SELECT 1").fetchone()

            return result is not None and result[0] == 1

        except sqlite3.Error:
            return False
