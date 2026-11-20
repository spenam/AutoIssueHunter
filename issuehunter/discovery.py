
from dataclasses import dataclass, field

from issuehunter.config import get_settings
from issuehunter.github_client import GitHubClient
from issuehunter.models import Issue
from issuehunter.repository_policy import (
    RepositoryCheck,
    RepositoryPolicy,
    RepositoryPolicyError,
)
from issuehunter.storage import Storage


@dataclass
class DiscoveryResult:
    """Summary of a discovery operation."""

    query: str
    issues: list[Issue]
    saved_count: int

    screened_repository_count: int = 0
    approved_repository_count: int = 0
    rejected_repository_count: int = 0
    review_repository_count: int = 0
    verification_failed_count: int = 0

    screening_errors: list[str] = field(default_factory=list)

    @property
    def discovered_count(self) -> int:
        """Number of approved issues discovered."""
        return len(self.issues)


class IssueDiscovery:
    """Coordinates GitHub searches, repository screening and persistence."""

    def __init__(
        self,
        github: GitHubClient | None = None,
        storage: Storage | None = None,
        policy: RepositoryPolicy | None = None,
    ) -> None:
        self.settings = get_settings()

        self.github = github or GitHubClient()
        self.storage = storage or Storage()

        self._owns_github = github is None
        self._owns_policy = policy is None

        self.storage.initialize()

        self.policy = policy or RepositoryPolicy(
            storage=self.storage,
        )

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

    def _screen_repositories(
        self,
        issues: list[Issue],
    ) -> tuple[list[Issue], dict[str, RepositoryCheck], list[str]]:
        """
        Screen each unique repository once.

        Only approved repositories are allowed to contribute issues.
        API failures and review-required results fail closed.
        """
        repository_names = {
            issue.repository_full_name
            for issue in issues
        }

        results: dict[str, RepositoryCheck] = {}
        errors: list[str] = []

        for full_name in sorted(repository_names):
            try:
                result = self.policy.check(full_name)
                results[full_name] = result

            except RepositoryPolicyError as exc:
                errors.append(f"{full_name}: {exc}")

        approved_issues = [
            issue
            for issue in issues
            if (
                issue.repository_full_name in results
                and results[issue.repository_full_name].approved
            )
        ]

        return approved_issues, results, errors

    def discover(
        self,
        language: str | None = None,
        labels: list[str] | None = None,
        keywords: list[str] | None = None,
        limit: int | None = None,
    ) -> DiscoveryResult:
        """
        Search GitHub, screen repositories and persist approved issues.

        Repositories marked rejected or needs_review are excluded.
        Repositories that cannot be verified are also excluded.
        """
        max_issues = (
            self.settings.max_issues_per_scan
            if limit is None
            else limit
        )

        if not 1 <= max_issues <= 1000:
            raise ValueError(
                "Discovery limit must be between 1 and 1000."
            )

        query = self.build_query(
            language=language,
            labels=labels,
            keywords=keywords,
        )

        discovered_issues: list[Issue] = []
        page = 1

        while len(discovered_issues) < max_issues:
            remaining = max_issues - len(discovered_issues)

            batch = self.github.search_issues(
                query=query,
                per_page=min(100, remaining),
                page=page,
            )

            if not batch:
                break

            discovered_issues.extend(batch)
            page += 1

        discovered_issues = discovered_issues[:max_issues]

        approved_issues, screening_results, errors = (
            self._screen_repositories(discovered_issues)
        )

        saved_count = self.storage.save_issues(approved_issues)

        statuses = [
            result.status
            for result in screening_results.values()
        ]

        return DiscoveryResult(
            query=query,
            issues=approved_issues,
            saved_count=saved_count,
            screened_repository_count=len(screening_results),
            approved_repository_count=statuses.count("approved"),
            rejected_repository_count=statuses.count("rejected"),
            review_repository_count=statuses.count("needs_review"),
            verification_failed_count=len(errors),
            screening_errors=errors,
        )

    def close(self) -> None:
        """Close only the clients owned by this discovery service."""
        if self._owns_github:
            self.github.close()

        if self._owns_policy:
            self.policy.close()

    def __enter__(self) -> "IssueDiscovery":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
