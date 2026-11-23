
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from issuehunter.config import get_settings
from issuehunter.storage import Storage


@dataclass
class RepositoryCheck:
    """Result of screening a GitHub repository."""

    full_name: str
    status: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    stars: int = 0
    forks: int = 0
    size_kb: int = 0
    age_days: int = 0

    @property
    def approved(self) -> bool:
        """Whether the repository can proceed to LLM analysis."""
        return self.status == "approved"


class RepositoryPolicyError(Exception):
    """Raised when repository screening cannot be completed."""


class RepositoryPolicy:
    """Screens GitHub repositories before cloning or LLM analysis."""

    MIN_STARS = 50
    MIN_AGE_DAYS = 90
    MAX_INACTIVE_DAYS = 730
    MAX_SIZE_KB = 100_000
    CACHE_HOURS = 24

    ACCEPTED_LICENSES = {
        "MIT",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "MPL-2.0",
        "LGPL-2.1",
        "LGPL-3.0",
        "GPL-2.0",
        "GPL-3.0",
        "AGPL-3.0",
        "Unlicense",
        "0BSD",
        "CC0-1.0",
    }

    def __init__(
        self,
        storage: Storage | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        settings = get_settings()

        self.storage = storage or Storage()
        self.storage.initialize()

        self._owns_client = client is None

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "AutoIssueHunter",
        }

        if settings.github_token:
            headers["Authorization"] = (
                f"Bearer {settings.github_token}"
            )

        self.client = client or httpx.Client(
            base_url=settings.github_api_url.rstrip("/"),
            headers=headers,
            timeout=settings.request_timeout,
        )

    def close(self) -> None:
        """Close the HTTP client if this instance owns it."""
        if self._owns_client:
            self.client.close()

    def _validate_repository_name(
        self,
        full_name: str,
    ) -> tuple[str, str]:
        """Validate and split an owner/repository identifier."""
        parts = full_name.split("/")

        if (
            len(parts) != 2
            or not all(parts)
            or any(part in {".", ".."} for part in parts)
            or any(
                not all(
                    char.isalnum() or char in "-_."
                    for char in part
                )
                for part in parts
            )
        ):
            raise ValueError(
                "Repository must use 'owner/repository' format."
            )

        return parts[0], parts[1]

    def _get_metadata(self, full_name: str) -> dict:
        """Retrieve repository metadata from GitHub."""
        owner, repository = self._validate_repository_name(full_name)

        endpoint = (
            f"/repos/{quote(owner, safe='')}/"
            f"{quote(repository, safe='')}"
        )

        try:
            response = self.client.get(endpoint)
            response.raise_for_status()
            data = response.json()

        except httpx.HTTPStatusError as exc:
            raise RepositoryPolicyError(
                f"GitHub returned HTTP {exc.response.status_code} "
                f"while checking {full_name}."
            ) from exc

        except (httpx.RequestError, ValueError) as exc:
            raise RepositoryPolicyError(
                f"Could not verify repository {full_name}: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise RepositoryPolicyError(
                "GitHub returned invalid repository metadata."
            )

        return data

    
    def _evaluate(self, metadata: dict) -> RepositoryCheck:
        """Apply repository metadata screening rules."""
        full_name = metadata["full_name"]
        now = datetime.now(UTC)

        try:
            created_at = datetime.fromisoformat(
                metadata["created_at"].replace("Z", "+00:00")
            )

            pushed_value = metadata.get("pushed_at")

            pushed_at = (
                datetime.fromisoformat(
                    pushed_value.replace("Z", "+00:00")
                )
                if pushed_value
                else None
            )

        except (KeyError, TypeError, ValueError) as exc:
            raise RepositoryPolicyError(
                f"Invalid repository dates for {full_name}."
            ) from exc

        if (
            created_at.tzinfo is None
            or (
                pushed_at is not None
                and pushed_at.tzinfo is None
            )
        ):
            raise RepositoryPolicyError(
                f"Repository dates lack timezone information: {full_name}."
            )

        age_days = (now - created_at).days

        inactive_days = (
            (now - pushed_at).days
            if pushed_at is not None
            else None
        )

        stars = int(metadata.get("stargazers_count", 0))
        forks = int(metadata.get("forks_count", 0))
        size_kb = int(metadata.get("size", 0))

        reasons: list[str] = []
        warnings: list[str] = []

        #license_data = metadata.get("license") or {}

        #license_id = (
        #    license_data.get("spdx_id")
        #    if isinstance(license_data, dict)
        #    else None
        #)

        if metadata.get("private"):
            reasons.append("Repository is private.")

        if metadata.get("archived"):
            reasons.append("Repository is archived.")

        if metadata.get("disabled"):
            reasons.append("Repository is disabled.")

        if metadata.get("fork"):
            reasons.append("Forked repositories are not allowed.")

        if age_days < self.MIN_AGE_DAYS:
            reasons.append(
                f"Repository is too young ({age_days} days)."
            )

        if stars < self.MIN_STARS:
            reasons.append(
                f"Repository has fewer than {self.MIN_STARS} stars."
            )

        #if license_id not in self.ACCEPTED_LICENSES:
        #    reasons.append(
        #        f"License is missing or not approved: {license_id!r}."
        #    )

        if (
            inactive_days is None
            or inactive_days > self.MAX_INACTIVE_DAYS
        ):
            reasons.append(
                "Repository has no recent recorded activity."
            )

        if size_kb > self.MAX_SIZE_KB:
            reasons.append(
                "Repository exceeds the 100 MB mid-size limit."
            )

        if not metadata.get("default_branch"):
            reasons.append("Repository has no default branch.")

        if reasons:
            status = "rejected"
        elif warnings:
            status = "needs_review"
        else:
            status = "approved"

        return RepositoryCheck(
            full_name=full_name,
            status=status,
            reasons=reasons,
            warnings=warnings,
            stars=stars,
            forks=forks,
            size_kb=size_kb,
            age_days=age_days,
        )

    def _from_cache(self, row) -> RepositoryCheck:
        """Convert a cached database row into a screening result."""
        import json

        status = row["screening_status"]
        reasons = json.loads(row["screening_reasons_json"])

        if (
            status == "approved"
            and row["size_kb"] > self.MAX_SIZE_KB
        ):
            status = "rejected"
            reasons.append(
                "Repository exceeds the 100 MB mid-size limit."
            )

        return RepositoryCheck(
            full_name=row["full_name"],
            status=status,
            reasons=reasons,
            warnings=json.loads(row["screening_warnings_json"]),
            stars=row["stars"],
            forks=row["forks"],
            size_kb=row["size_kb"],
            age_days=(
                datetime.now(UTC)
                - datetime.fromisoformat(row["created_at"])
            ).days,
        )

    def check(
        self,
        full_name: str,
        force_refresh: bool = False,
    ) -> RepositoryCheck:
        """
        Screen a repository using cached or fresh GitHub information.

        Cache lifetime: 24 hours.
        API verification failures never result in approval.
        """
        self._validate_repository_name(full_name)

        if not force_refresh:
            cached = self.storage.get_cached_repository(
                full_name,
                max_age_hours=self.CACHE_HOURS,
            )

            if cached is not None:
                return self._from_cache(cached)

        metadata = self._get_metadata(full_name)

        actual_name = metadata.get("full_name")

        if (
            actual_name is None
            or actual_name.casefold() != full_name.casefold()
        ):
            raise RepositoryPolicyError(
                "GitHub returned metadata for a different repository."
            )

        result = self._evaluate(metadata)

        # Security checks are performed even if metadata already
        # contains a rejection, so the cached record has full results.

        if result.reasons:
            result.status = "rejected"
        elif result.warnings:
            result.status = "needs_review"
        else:
            result.status = "approved"

        self.storage.save_repository_screening(
            metadata=metadata,
            screening_status=result.status,
            reasons=result.reasons,
            warnings=result.warnings,
        )

        return result

    def __enter__(self) -> "RepositoryPolicy":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
