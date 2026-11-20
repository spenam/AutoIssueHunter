
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from issuehunter.analysis import (
    IssueAnalysisError,
    IssueAnalyzer,
)
from issuehunter.models import Issue, IssueState


@pytest.fixture
def issue():
    return Issue(
        id=12345,
        number=10,
        title="Application crashes on startup",
        body="The application crashes when launched.",
        html_url="https://github.com/example/project/issues/10",
        state=IssueState.OPEN,
        repository_full_name="example/project",
        repository_url="https://api.github.com/repos/example/project",
        repository_description="Example project",
        language="Python",
        labels=["bug"],
        comments_count=2,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture
def storage():
    mock = Mock()
    mock.get_repository.return_value = {
        "screening_status": "approved",
    }
    mock.get_latest_analysis.return_value = None
    return mock


@pytest.fixture
def llm():
    mock = Mock()
    mock.generate.return_value = """
    {
        "summary": "The application crashes during startup.",
        "suspected_root_cause": "Possible initialization error.",
        "relevant_files": [],
        "relevant_components": ["startup"],
        "difficulty": "intermediate",
        "estimated_hours": 2,
        "confidence": 50,
        "implementation_plan": ["Investigate startup initialization."],
        "risks": []
    }
    """
    return mock


@pytest.fixture
def analyzer(storage, llm):
    return IssueAnalyzer(llm=llm, storage=storage)


def test_approved_repository_can_be_analyzed(
    analyzer,
    issue,
    storage,
    llm,
):
    result = analyzer.analyze(issue)

    assert result.issue_id == issue.id
    assert result.summary
    storage.get_repository.assert_called_once_with(
        "example/project"
    )
    llm.generate.assert_called_once()
    storage.save_analysis.assert_called_once()


def test_unscreened_repository_is_blocked(
    analyzer,
    issue,
    storage,
    llm,
):
    storage.get_repository.return_value = None

    with pytest.raises(IssueAnalysisError):
        analyzer.analyze(issue)

    llm.generate.assert_not_called()


@pytest.mark.parametrize(
    "status",
    ["rejected", "needs_review"],
)
def test_non_approved_repository_is_blocked(
    analyzer,
    issue,
    storage,
    llm,
    status,
):
    storage.get_repository.return_value = {
        "screening_status": status,
    }

    with pytest.raises(IssueAnalysisError):
        analyzer.analyze(issue)

    llm.generate.assert_not_called()
    storage.save_analysis.assert_not_called()


def test_cached_analysis_cannot_bypass_screening(
    analyzer,
    issue,
    storage,
    llm,
):
    storage.get_repository.return_value = {
        "screening_status": "needs_review",
    }

    storage.get_latest_analysis.return_value = Mock()

    with pytest.raises(IssueAnalysisError):
        analyzer.analyze(issue)

    storage.get_latest_analysis.assert_not_called()
    llm.generate.assert_not_called()


def test_approved_repository_can_reuse_cached_analysis(
    analyzer,
    issue,
    storage,
    llm,
):
    cached_analysis = Mock()
    storage.get_latest_analysis.return_value = cached_analysis

    result = analyzer.analyze(issue)

    assert result is cached_analysis
    llm.generate.assert_not_called()
    storage.save_analysis.assert_not_called()


def test_force_reanalyzes_approved_repository(
    analyzer,
    issue,
    storage,
    llm,
):
    result = analyzer.analyze(issue, force=True)

    assert result.issue_id == issue.id
    llm.generate.assert_called_once()
    storage.save_analysis.assert_called_once()
