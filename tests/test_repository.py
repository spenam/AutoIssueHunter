
from unittest.mock import Mock

import pytest

from issuehunter.repository import (
    RepositoryError,
    RepositoryInspector,
)


@pytest.fixture
def storage():
    return Mock()


@pytest.fixture
def inspector(tmp_path, storage):
    return RepositoryInspector(
        workspace=tmp_path,
        storage=storage,
    )


def test_approved_repository_can_be_cloned(
    inspector,
    storage,
    monkeypatch,
):
    storage.get_repository.return_value = {
        "screening_status": "approved"
    }

    git_calls = []

    def fake_git(arguments, cwd=None, timeout=120):
        git_calls.append(arguments)
        return ""

    monkeypatch.setattr(
        RepositoryInspector,
        "_run_git",
        staticmethod(fake_git),
    )

    result = inspector.clone("owner/repository")

    assert result == inspector.workspace / "owner" / "repository"
    assert len(git_calls) == 1
    assert git_calls[0][0] == "-c"
    assert "clone" in git_calls[0]


@pytest.mark.parametrize(
    "status",
    ["rejected", "needs_review"],
)
def test_unapproved_repository_cannot_be_cloned(
    inspector,
    storage,
    monkeypatch,
    status,
):
    storage.get_repository.return_value = {
        "screening_status": status
    }

    git_called = False

    def fake_git(*args, **kwargs):
        nonlocal git_called
        git_called = True
        return ""

    monkeypatch.setattr(
        RepositoryInspector,
        "_run_git",
        staticmethod(fake_git),
    )

    with pytest.raises(RepositoryError):
        inspector.clone("owner/repository")

    assert git_called is False


def test_unscreened_repository_cannot_be_cloned(
    inspector,
    storage,
    monkeypatch,
):
    storage.get_repository.return_value = None

    git_called = False

    def fake_git(*args, **kwargs):
        nonlocal git_called
        git_called = True
        return ""

    monkeypatch.setattr(
        RepositoryInspector,
        "_run_git",
        staticmethod(fake_git),
    )

    with pytest.raises(RepositoryError):
        inspector.clone("owner/repository")

    assert git_called is False


def test_invalid_repository_name_is_rejected(inspector):
    with pytest.raises(ValueError):
        inspector.clone("../repository")
