from datetime import UTC, datetime

from rich.console import Console

from issuehunter.cli import _print_analysis
from issuehunter.models import (
    AnalysisEvidence,
    Issue,
    IssueAnalysis,
    IssueState,
)


def test_print_analysis_includes_key_sections(monkeypatch):
    monkeypatch.setattr(
        "issuehunter.cli.console",
        Console(file=None, width=120, record=True),
    )
    console = __import__("issuehunter.cli", fromlist=["console"]).console
    console.capture()

    issue = Issue(
        id=286,
        number=286,
        title="Space key bug",
        body="The Space key triggers host transport.",
        html_url="https://github.com/tone-3000/tone3000-plugin/issues/286",
        state=IssueState.OPEN,
        repository_full_name="tone-3000/tone3000-plugin",
        repository_url="https://api.github.com/repos/tone-3000/tone3000-plugin",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    analysis = IssueAnalysis(
        issue_id=286,
        summary="Space is forwarded to the host.",
        suspected_root_cause="Keyboard passthrough runs before text focus handling.",
        root_cause_confirmed=False,
        evidence=[
            AnalysisEvidence(
                source="plugin/ui/NativeEditor.cpp",
                description="Space is forwarded to the host.",
                evidence_type="repository",
            )
        ],
        investigation_notes=["Text fields should consume Space."],
        relevant_files=["plugin/ui/NativeEditor.cpp"],
        relevant_components=["NativeEditor", "WindowKeyEvents"],
        implementation_plan=["Add focused-text-field guard."],
        suggested_changes=["Guard host forwarding."],
        regression_tests=["Test Space in a text field."],
        risks=["Transport shortcuts may change."],
        confidence=35,
        estimated_hours=2,
        analyzed_at=datetime.now(UTC),
    )

    _print_analysis(issue, analysis)

    captured_output = console.export_text()

    assert "Summary" in captured_output
    assert "Evidence" in captured_output
    assert "Relevant files" in captured_output
    assert "Regression tests" in captured_output
