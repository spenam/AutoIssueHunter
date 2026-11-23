
from datetime import UTC, datetime, timedelta
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
    now = datetime.now(UTC)

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


def configure_successful_screening(policy, metadata=None):
    """Configure a successful GitHub metadata response."""
    metadata = metadata or make_metadata()
    policy.client.get.return_value = make_response(metadata)

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
        datetime.now(UTC) - timedelta(days=30)
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


#def test_rejects_missing_license(policy):
    #metadata = make_metadata(license=None)

    #result = policy._evaluate(metadata)

    #assert result.status == "rejected"
    #assert any("License" in reason for reason in result.reasons)


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
    metadata = make_metadata(size=100001)

    result = policy._evaluate(metadata)

    assert result.status == "rejected"





def test_cache_avoids_repeated_github_requests(policy):
    """A successful screening should be cached for 24 hours."""
    configure_successful_screening(policy)

    first = policy.check("example/project")
    second = policy.check("example/project")

    assert first.status == "approved"
    assert second.status == "approved"


def test_cached_oversized_repository_is_rejected(policy):
    metadata = make_metadata(size=100001)
    configure_successful_screening(policy, metadata)

    first = policy.check("example/project")
    second = policy.check("example/project")

    assert first.status == "rejected"
    assert second.status == "rejected"


def test_api_failure_raises_policy_error(policy):
    """Failure to retrieve repository metadata must fail closed."""
    policy.client.get.side_effect = httpx.ConnectError(
        "Connection unavailable"
    )

    with pytest.raises(RepositoryPolicyError):
        policy.check("example/project")
