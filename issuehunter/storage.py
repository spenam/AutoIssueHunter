
import json
import sqlite3
from pathlib import Path

from issuehunter.config import get_settings
from issuehunter.models import Issue


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

    def save_issues(self, issues: list[Issue]) -> int:
        """
        Save issues to SQLite.

        Existing issues are updated rather than duplicated.
        Returns the number of issues processed.
        """
        if not issues:
            return 0

        query = """
        INSERT INTO issues (
            id,
            github_number,
            repository_full_name,
            title,
            body,
            html_url,
            state,
            language,
            labels_json,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repository_full_name, github_number)
        DO UPDATE SET
            id = excluded.id,
            title = excluded.title,
            body = excluded.body,
            html_url = excluded.html_url,
            state = excluded.state,
            language = excluded.language,
            labels_json = excluded.labels_json,
            updated_at = excluded.updated_at
        """

        records = [
            (
                issue.id,
                issue.number,
                issue.repository_full_name,
                issue.title,
                issue.body,
                str(issue.html_url),
                issue.state.value,
                issue.language,
                json.dumps(issue.labels),
                issue.created_at.isoformat(),
                issue.updated_at.isoformat(),
            )
            for issue in issues
        ]

        with self.connect() as connection:
            connection.executemany(query, records)

        return len(records)

    def get_issue(self, issue_id: int) -> sqlite3.Row | None:
        """Retrieve an issue by its GitHub issue ID."""
        with self.connect() as connection:
            return connection.execute(
                "SELECT * FROM issues WHERE id = ?",
                (issue_id,),
            ).fetchone()

    def list_issues(
        self,
        state: str | None = "open",
        limit: int = 50,
    ) -> list[sqlite3.Row]:
        """Retrieve stored issues, optionally filtered by state."""
        if limit < 1:
            raise ValueError("limit must be at least 1.")

        query = "SELECT * FROM issues"
        parameters: list[object] = []

        if state is not None:
            query += " WHERE state = ?"
            parameters.append(state)

        query += " ORDER BY updated_at DESC LIMIT ?"
        parameters.append(limit)

        with self.connect() as connection:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()

        return rows
