
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from issuehunter.config import get_settings
from issuehunter.models import Issue, IssueAnalysis


SCHEMA = """
CREATE TABLE IF NOT EXISTS repositories (
    full_name TEXT PRIMARY KEY,
    github_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    owner TEXT NOT NULL,
    html_url TEXT NOT NULL,
    description TEXT,

    stars INTEGER NOT NULL DEFAULT 0,
    forks INTEGER NOT NULL DEFAULT 0,
    size_kb INTEGER NOT NULL DEFAULT 0,

    license_spdx_id TEXT,
    license_name TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT,
    pushed_at TEXT,

    archived INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    is_fork INTEGER NOT NULL DEFAULT 0,

    default_branch TEXT,

    screening_status TEXT NOT NULL DEFAULT 'needs_review'
        CHECK (
            screening_status IN (
                'approved',
                'rejected',
                'needs_review'
            )
        ),

    screening_reasons_json TEXT NOT NULL DEFAULT '[]',
    screening_warnings_json TEXT NOT NULL DEFAULT '[]',

    last_checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repositories_screening_status
ON repositories(screening_status);

CREATE INDEX IF NOT EXISTS idx_repositories_last_checked
ON repositories(last_checked_at);

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
    analyzed_at TEXT NOT NULL,

    FOREIGN KEY(issue_id) REFERENCES issues(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_analyses_issue
ON analyses(issue_id);

CREATE INDEX IF NOT EXISTS idx_analyses_date
ON analyses(issue_id, analyzed_at DESC);
"""


class Storage:
    """SQLite storage manager for AutoIssueHunter."""

    VALID_SCREENING_STATUSES = {
        "approved",
        "rejected",
        "needs_review",
    }

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

    def save_repository_screening(
        self,
        metadata: dict[str, Any],
        screening_status: str,
        reasons: list[str] | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        """
        Save GitHub repository metadata and its screening decision.

        Metadata should be the repository object returned by
        GitHub's GET /repos/{owner}/{repo} endpoint.
        """
        if screening_status not in self.VALID_SCREENING_STATUSES:
            raise ValueError(
                f"Invalid screening status: {screening_status}"
            )

        required_fields = (
            "id",
            "full_name",
            "name",
            "owner",
            "html_url",
            "created_at",
        )

        missing_fields = [
            field
            for field in required_fields
            if metadata.get(field) is None
        ]

        if missing_fields:
            raise ValueError(
                "Incomplete GitHub repository metadata. Missing: "
                + ", ".join(missing_fields)
            )

        owner_data = metadata["owner"]
        license_data = metadata.get("license") or {}

        if not isinstance(owner_data, dict):
            raise ValueError("Invalid repository owner metadata.")

        if not isinstance(license_data, dict):
            license_data = {}

        owner = owner_data.get("login")

        if not owner:
            raise ValueError("Repository owner is missing.")

        checked_at = datetime.now(timezone.utc).isoformat()

        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO repositories (
                    full_name,
                    github_id,
                    name,
                    owner,
                    html_url,
                    description,
                    stars,
                    forks,
                    size_kb,
                    license_spdx_id,
                    license_name,
                    created_at,
                    updated_at,
                    pushed_at,
                    archived,
                    disabled,
                    is_fork,
                    default_branch,
                    screening_status,
                    screening_reasons_json,
                    screening_warnings_json,
                    last_checked_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT(full_name)
                DO UPDATE SET
                    github_id = excluded.github_id,
                    name = excluded.name,
                    owner = excluded.owner,
                    html_url = excluded.html_url,
                    description = excluded.description,
                    stars = excluded.stars,
                    forks = excluded.forks,
                    size_kb = excluded.size_kb,
                    license_spdx_id = excluded.license_spdx_id,
                    license_name = excluded.license_name,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    pushed_at = excluded.pushed_at,
                    archived = excluded.archived,
                    disabled = excluded.disabled,
                    is_fork = excluded.is_fork,
                    default_branch = excluded.default_branch,
                    screening_status = excluded.screening_status,
                    screening_reasons_json =
                        excluded.screening_reasons_json,
                    screening_warnings_json =
                        excluded.screening_warnings_json,
                    last_checked_at = excluded.last_checked_at
                """,
                (
                    metadata["full_name"],
                    metadata["id"],
                    metadata["name"],
                    owner,
                    metadata["html_url"],
                    metadata.get("description"),
                    metadata.get("stargazers_count", 0),
                    metadata.get("forks_count", 0),
                    metadata.get("size", 0),
                    license_data.get("spdx_id"),
                    license_data.get("name"),
                    metadata["created_at"],
                    metadata.get("updated_at"),
                    metadata.get("pushed_at"),
                    int(bool(metadata.get("archived", False))),
                    int(bool(metadata.get("disabled", False))),
                    int(bool(metadata.get("fork", False))),
                    metadata.get("default_branch"),
                    screening_status,
                    json.dumps(reasons or []),
                    json.dumps(warnings or []),
                    checked_at,
                ),
            )

    def get_cached_repository(
        self,
        full_name: str,
        max_age_hours: int = 24,
    ) -> sqlite3.Row | None:
        """
        Retrieve repository metadata if its screening result is fresh.

        Returns None when the repository is missing or its cache
        has expired.
        """
        if max_age_hours < 1:
            raise ValueError("max_age_hours must be at least 1.")

        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(hours=max_age_hours)
        ).isoformat()

        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM repositories
                WHERE full_name = ?
                  AND last_checked_at >= ?
                """,
                (full_name, cutoff),
            ).fetchone()

    def get_repository(
        self,
        full_name: str,
    ) -> sqlite3.Row | None:
        """Retrieve repository metadata, even if its cache has expired."""
        with self.connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM repositories
                WHERE full_name = ?
                """,
                (full_name,),
            ).fetchone()

    def list_screened_repositories(
        self,
        screening_status: str | None = None,
        limit: int = 100,
    ) -> list[sqlite3.Row]:
        """List cached repositories, optionally filtered by decision."""
        if limit < 1:
            raise ValueError("limit must be at least 1.")

        if (
            screening_status is not None
            and screening_status not in self.VALID_SCREENING_STATUSES
        ):
            raise ValueError("Invalid screening status.")

        query = "SELECT * FROM repositories"
        parameters: list[object] = []

        if screening_status is not None:
            query += " WHERE screening_status = ?"
            parameters.append(screening_status)

        query += " ORDER BY last_checked_at DESC LIMIT ?"
        parameters.append(limit)

        with self.connect() as connection:
            return connection.execute(
                query,
                parameters,
            ).fetchall()

    def save_issues(self, issues: list[Issue]) -> int:
        """Save issues, updating existing records instead of duplicating them."""
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

    def save_analysis(self, analysis: IssueAnalysis) -> int:
        """
        Save an issue analysis.

        Multiple analyses per issue are allowed so that future
        investigations can be retained as history.

        Returns the database ID of the saved analysis.
        """
        with self.connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO analyses (
                    issue_id,
                    summary,
                    analysis_json,
                    analyzed_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    analysis.issue_id,
                    analysis.summary,
                    analysis.model_dump_json(),
                    analysis.analyzed_at.isoformat(),
                ),
            )

            return int(cursor.lastrowid)

    def get_latest_analysis(
        self,
        issue_id: int,
    ) -> IssueAnalysis | None:
        """Retrieve the most recent analysis for a GitHub issue."""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT analysis_json
                FROM analyses
                WHERE issue_id = ?
                ORDER BY analyzed_at DESC, id DESC
                LIMIT 1
                """,
                (issue_id,),
            ).fetchone()

        if row is None:
            return None

        return IssueAnalysis.model_validate_json(row["analysis_json"])

    def has_analysis(self, issue_id: int) -> bool:
        """Check whether an issue has at least one saved analysis."""
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM analyses
                WHERE issue_id = ?
                LIMIT 1
                """,
                (issue_id,),
            ).fetchone()

        return row is not None
