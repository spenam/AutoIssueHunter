
from dataclasses import dataclass

from issuehunter.config import get_settings
from issuehunter.github_client import GitHubClient
from issuehunter.models import Issue
from issuehunter.storage import Storage


@dataclass
class DiscoveryResult:
    """Summary of a discovery operation."""

    query: str
    issues: list[Issue]
    saved_count: int

    @property
    def discovered_count(self) -> int:
        return len(self.issues)


class IssueDiscovery:
    """Coordinates GitHub issue searches and local persistence."""

    def __init__(
        self,
        github: GitHubClient | None = None,
        storage: Storage | None = None,
    ) -> None:
        self.settings = get_settings()
        self.github = github or GitHubClient()
        self.storage = storage or Storage()

        self.storage.initialize()

    def build_query(
        self,
        language: str | None = None,
        labels: list[str] | None = None,
        keywords: list[str] | None = None,
    ) -> str:
        """Build a GitHub issue search query from discovery preferences."""
        language = language or self.settings.default_language

        parts = [
            f"language:{language}",
            "no:assignee",
        ]

        if labels:
            for label in labels:
                cleaned = label.strip()

                if cleaned:
                    parts.append(f'label:"{cleaned}"')

        if keywords:
            for keyword in keywords:
                cleaned = keyword.strip()

                if cleaned:
                    parts.append(f'"{cleaned}"')

        return " ".join(parts)

    def discover(
        self,
        language: str | None = None,
        labels: list[str] | None = None,
        keywords: list[str] | None = None,
        limit: int | None = None,
    ) -> DiscoveryResult:
        """Search GitHub, persist results, and return a discovery summary."""
        max_issues = limit or self.settings.max_issues_per_scan

        if not 1 <= max_issues <= 1000:
            raise ValueError("Discovery limit must be between 1 and 1000.")

        query = self.build_query(
            language=language,
            labels=labels,
            keywords=keywords,
        )

        issues: list[Issue] = []
        page = 1

        while len(issues) < max_issues:
            remaining = max_issues - len(issues)

            batch = self.github.search_issues(
                query=query,
                per_page=min(100, remaining),
                page=page,
            )

            if not batch:
                break

            issues.extend(batch)
            page += 1

        issues = issues[:max_issues]
        saved_count = self.storage.save_issues(issues)

        return DiscoveryResult(
            query=query,
            issues=issues,
            saved_count=saved_count,
        )

    def close(self) -> None:
        """Close the GitHub client if this service owns it."""
        self.github.close()
