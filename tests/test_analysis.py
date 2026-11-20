
from datetime import UTC, datetime
from unittest.mock import Mock

import pytest

from issuehunter.analysis import (
    IssueAnalysisError,
    IssueAnalyzer,
)
from issuehunter.models import Issue, IssueState
from issuehunter.repository import RepositoryInfo


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
def repository():
    mock = Mock()
    mock.clone.return_value = Mock()
    mock.inspect.return_value = RepositoryInfo(
        full_name="example/project",
        path=Mock(),
        files=[
            "src/main.py",
            "src/startup.py",
            "tests/test_startup.py",
        ],
        project_files=["pyproject.toml", "README.md"],
        primary_language="Python",
    )
    mock.read_file.side_effect = lambda full_name, path: {
        "src/main.py": "def main():\\n    initialize()\\n",
        "src/startup.py": "def initialize():\\n    pass\\n",
        "tests/test_startup.py": "def test_startup():\\n    assert True\\n",
        "pyproject.toml": "[project]\\nname = 'example'\\n",
        "README.md": "# Example project\\n",
    }[path]
    return mock


@pytest.fixture
def analyzer(storage, llm, repository):
    return IssueAnalyzer(
        llm=llm,
        storage=storage,
        repository_inspector=repository,
    )


def test_approved_repository_can_be_analyzed(
    analyzer,
    issue,
    storage,
    llm,
    repository,
):
    result = analyzer.analyze(issue)

    assert result.issue_id == issue.id
    assert result.summary
    storage.get_repository.assert_called_once_with(
        "example/project"
    )
    repository.clone.assert_called_once_with("example/project")
    repository.inspect.assert_called_once_with("example/project")
    llm.generate.assert_called_once()
    storage.save_analysis.assert_called_once()


def test_unscreened_repository_is_blocked(
    analyzer,
    issue,
    storage,
    llm,
    repository,
):
    storage.get_repository.return_value = None

    with pytest.raises(IssueAnalysisError):
        analyzer.analyze(issue)

    llm.generate.assert_not_called()
    repository.clone.assert_not_called()


@pytest.mark.parametrize(
    "status",
    ["rejected", "needs_review"],
)
def test_non_approved_repository_is_blocked(
    analyzer,
    issue,
    storage,
    llm,
    repository,
    status,
):
    storage.get_repository.return_value = {
        "screening_status": status,
    }

    with pytest.raises(IssueAnalysisError):
        analyzer.analyze(issue)

    llm.generate.assert_not_called()
    storage.save_analysis.assert_not_called()
    repository.clone.assert_not_called()


def test_cached_analysis_cannot_bypass_screening(
    analyzer,
    issue,
    storage,
    llm,
    repository,
):
    storage.get_repository.return_value = {
        "screening_status": "needs_review",
    }
    storage.get_latest_analysis.return_value = Mock()

    with pytest.raises(IssueAnalysisError):
        analyzer.analyze(issue)

    storage.get_latest_analysis.assert_not_called()
    llm.generate.assert_not_called()
    repository.clone.assert_not_called()


def test_approved_repository_can_reuse_cached_analysis(
    analyzer,
    issue,
    storage,
    llm,
    repository,
):
    cached_analysis = Mock()
    storage.get_latest_analysis.return_value = cached_analysis

    result = analyzer.analyze(issue)

    assert result is cached_analysis
    llm.generate.assert_not_called()
    storage.save_analysis.assert_not_called()
    repository.clone.assert_not_called()


def test_force_reanalyzes_approved_repository(
    analyzer,
    issue,
    storage,
    llm,
    repository,
):
    result = analyzer.analyze(issue, force=True)

    assert result.issue_id == issue.id
    llm.generate.assert_called_once()
    storage.save_analysis.assert_called_once()
    repository.clone.assert_called_once_with("example/project")

def test_source_files_are_prioritized_over_documentation(
    analyzer,
    issue,
):
    files = [
        "README.md",
        "docs/startup.md",
        "pyproject.toml",
        "src/startup.py",
        "src/main.py",
    ]

    selected = analyzer._select_relevant_files(
        issue,
        files,
        ["README.md", "pyproject.toml"],
    )

    assert "src/startup.py" in selected
    assert "src/main.py" in selected
    assert "README.md" not in selected
    assert "pyproject.toml" not in selected


def test_relevant_filenames_receive_higher_priority(
    analyzer,
    issue,
):
    files = [
        "src/database.py",
        "src/startup.py",
        "src/network.py",
    ]

    selected = analyzer._select_relevant_files(
        issue,
        files,
        [],
    )

    assert selected[0] == "src/startup.py"


def test_file_selection_respects_maximum_file_count(
    analyzer,
    issue,
):
    files = [
        f"src/module_{index}.py"
        for index in range(20)
    ]

    selected = analyzer._select_relevant_files(
        issue,
        files,
        [],
    )

    assert len(selected) <= analyzer.MAX_FILES_TO_READ


def test_unsupported_file_types_are_excluded(
    analyzer,
    issue,
):
    files = [
        "src/startup.py",
        "assets/logo.png",
        "data/archive.zip",
        "docs/manual.pdf",
        "src/main.exe",
    ]

    selected = analyzer._select_relevant_files(
        issue,
        files,
        [],
    )

    assert selected == ["src/startup.py"]
