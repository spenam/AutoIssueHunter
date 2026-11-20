
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import httpx
import pytest

from issuehunter.repository_policy import (
    RepositoryPolicy,
    RepositoryPolicyError,
)
from issuehunter.storage import Storage


def make_metadata(**overrides):
    """Build realistic GitHub repository metadata for testing."""
    now = datetime.now(timezone.utc)

    metadata = {
        "id": 123456,
        "full_name": "example/project",
        "name": "project",
        "owner": {"login": "example"},
        "html_url": "https://github.com/example/project",
        "description": "Example open-source project",
        "stargazers_count": 100,
        "forks_count": 10,
        "size": 10000,
        "license": {
            "spdx_id": "MIT",
            "name": "MIT License",
        },
        "created_at": (
            now - timedelta(days=180)
        ).isoformat(),
        "updated_at": now.isoformat(),
        "pushed_at": (
            now - timedelta(days=10)
        ).isoformat(),
        "archived": False,
        "disabled": False,
        "fork": False,
        "default_branch": "main",
    }

    metadata.update(overrides)
    return metadata


def make_response(payload, status_code=200):
    """Build a mocked HTTP response."""
    response = Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def configure_successful_screening(policy, metadata=None, alerts=None):
    """Configure metadata and Dependabot responses."""
    metadata = metadata or make_metadata()
    alerts = [] if alerts is None else alerts

    policy.client.get.side_effect = [
        make_response(metadata),
        make_response(alerts),
    ]


@pytest.fixture
def storage(tmp_path):
    """Create an isolated temporary SQLite database."""
    database = Storage(tmp_path / "test.sqlite3")
    database.initialize()
    return database


@pytest.fixture
def policy(storage):
    """Create a policy with a mocked HTTP client."""
    client = Mock()

    return RepositoryPolicy(
        storage=storage,
        client=client,
    )


def test_approved_repository(policy):
    metadata = make_metadata()

    result = policy._evaluate(metadata)

    assert result.status == "approved"
    assert result.approved is True
    assert result.reasons == []


def test_rejects_repository_with_too_few_stars(policy):
    metadata = make_metadata(stargazers_count=49)

    result = policy._evaluate(metadata)

    assert result.status == "rejected"
    assert any("stars" in reason for reason in result.reasons)


def test_rejects_repository_too_young(policy):
    created_at = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat()

    metadata = make_metadata(created_at=created_at)

    result = policy._evaluate(metadata)

    assert result.status == "rejected"
    assert any("young" in reason for reason in result.reasons)


def test_rejects_fork(policy):
    metadata = make_metadata(fork=True)

    result = policy._evaluate(metadata)

    assert result.status == "rejected"
    assert any("Forked" in reason for reason in result.reasons)


def test_rejects_missing_license(policy):
    metadata = make_metadata(license=None)

    result = policy._evaluate(metadata)

    assert result.status == "rejected"
    assert any("License" in reason for reason in result.reasons)


def test_rejects_unrecognized_license(policy):
    metadata = make_metadata(
        license={
            "spdx_id": "UNKNOWN-LICENSE",
            "name": "Unknown License",
        }
    )

    result = policy._evaluate(metadata)

    assert result.status == "rejected"


def test_rejects_archived_repository(policy):
    metadata = make_metadata(archived=True)

    result = policy._evaluate(metadata)

    assert result.status == "rejected"


def test_rejects_oversized_repository(policy):
    metadata = make_metadata(size=600000)

    result = policy._evaluate(metadata)

    assert result.status == "rejected"


def test_open_dependabot_alert_requires_review(policy, storage):
    """An open Dependabot alert must prevent automatic approval."""
    configure_successful_screening(
        policy,
        alerts=[{"number": 1, "state": "open"}],
    )

    result = policy.check("example/project")

    assert result.status == "needs_review"
    assert result.approved is False
    assert any(
        "Dependabot alert" in warning
        for warning in result.warnings
    )

    cached = storage.get_repository("example/project")
    assert cached["screening_status"] == "needs_review"


def test_no_open_dependabot_alerts_allows_approval(policy):
    """An accessible, empty alert list does not add a warning."""
    configure_successful_screening(policy, alerts=[])

    result = policy.check("example/project")

    assert result.status == "approved"
    assert result.approved is True
    assert result.warnings == []
    assert policy.client.get.call_count == 2


@pytest.mark.parametrize("status_code", [401, 403, 404, 500])
def test_inaccessible_dependabot_endpoint_requires_review(
    policy,
    status_code,
):
    """An inaccessible security endpoint must never mean 'clean'."""
    policy.client.get.side_effect = [
        make_response(make_metadata()),
        make_response(
            {"message": "Access unavailable"},
            status_code=status_code,
        ),
    ]

    result = policy.check("example/project")

    assert result.status == "needs_review"
    assert result.approved is False
    assert any(
        "Dependabot alerts could not be inspected" in warning
        for warning in result.warnings
    )


def test_invalid_dependabot_response_requires_review(policy):
    """Unexpected response formats must require manual review."""
    policy.client.get.side_effect = [
        make_response(make_metadata()),
        make_response({"unexpected": "format"}),
    ]

    result = policy.check("example/project")

    assert result.status == "needs_review"
    assert result.approved is False
    assert any(
        "unexpected Dependabot alert format" in warning
        for warning in result.warnings
    )


def test_dependabot_network_failure_requires_review(policy):
    """A network failure during security checking is not a clean result."""
    policy.client.get.side_effect = [
        make_response(make_metadata()),
        httpx.ConnectError("Connection unavailable"),
    ]

    result = policy.check("example/project")

    assert result.status == "needs_review"
    assert result.approved is False


def test_cache_avoids_repeated_github_requests(policy):
    """A successful screening should be cached for 24 hours."""
    configure_successful_screening(policy)

    first = policy.check("example/project")
    second = policy.check("example/project")

    assert first.status == "approved"
    assert second.status == "approved"

    # One metadata request and one Dependabot request.
    assert policy.client.get.call_count == 2


def test_cache_preserves_needs_review_decision(policy):
    """A cached security warning must not become approval."""
    configure_successful_screening(
        policy,
        alerts=[{"number": 1, "state": "open"}],
    )

    first = policy.check("example/project")
    second = policy.check("example/project")

    assert first.status == "needs_review"
    assert second.status == "needs_review"
    assert second.approved is False

    # The second screening should use the cached decision.
    assert policy.client.get.call_count == 2


def test_api_failure_raises_policy_error(policy):
    """Failure to retrieve repository metadata must fail closed."""
    policy.client.get.side_effect = httpx.ConnectError(
        "Connection unavailable"
    )

    with pytest.raises(RepositoryPolicyError):
        policy.check("example/project")
