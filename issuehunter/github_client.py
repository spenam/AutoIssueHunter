
from typing import Any

import httpx

from issuehunter.config import get_settings
from issuehunter.models import Issue, IssueState


class GitHubAPIError(Exception):
    """Raised when a GitHub API request fails."""


class GitHubClient:
    """Client for searching GitHub issues through the REST API."""

    def __init__(self) -> None:
        settings = get_settings()

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AutoIssueHunter",
        }

        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        self.base_url = settings.github_api_url.rstrip("/")
        self.client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=settings.request_timeout,
        )

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self.client.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def search_issues(
        self,
        query: str,
        per_page: int = 30,
        page: int = 1,
    ) -> list[Issue]:
        """
        Search for open GitHub issues.

        Example query:
            language:python label:bug

        Pull requests are excluded from the results.
        """
        if not query.strip():
            raise ValueError("GitHub search query cannot be empty.")

        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100.")

        if page < 1:
            raise ValueError("page must be at least 1.")

        search_query = f"{query.strip()} is:issue is:open -linked:pr"

        try:
            response = self.client.get(
                "/search/issues",
                params={
                    "q": search_query,
                    "per_page": per_page,
                    "page": page,
                    "sort": "updated",
                    "order": "desc",
                },
            )
            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            raise GitHubAPIError(
                f"GitHub API returned HTTP {status}: "
                f"{exc.response.text[:500]}"
            ) from exc

        except httpx.RequestError as exc:
            raise GitHubAPIError(
                f"Could not connect to GitHub: {exc}"
            ) from exc

        try:
            payload = response.json()
            items = payload["items"]
        except (ValueError, KeyError, TypeError) as exc:
            raise GitHubAPIError(
                "GitHub returned an unexpected search response."
            ) from exc

        issues = []

        for item in items:
            try:
                issues.append(self._parse_issue(item))
            except (KeyError, TypeError, ValueError) as exc:
                raise GitHubAPIError(
                    "Could not parse a GitHub issue response."
                ) from exc

        return issues

    @staticmethod
    def _parse_issue(data: dict[str, Any]) -> Issue:
        """Convert a GitHub API issue response into an Issue model."""
        repository_url = data["repository_url"]
        repository_full_name = repository_url.rstrip("/").split("/repos/")[-1]

        labels = [
            label["name"] if isinstance(label, dict) else str(label)
            for label in data.get("labels", [])
        ]

        return Issue(
            id=data["id"],
            number=data["number"],
            title=data["title"],
            body=data.get("body") or "",
            html_url=data["html_url"],
            state=IssueState(data["state"]),
            repository_full_name=repository_full_name,
            repository_url=repository_url,
            labels=labels,
            comments_count=data.get("comments", 0),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )
