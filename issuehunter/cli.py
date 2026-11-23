import json
import sys
from datetime import datetime
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from issuehunter.analysis import IssueAnalysisError, IssueAnalyzer
from issuehunter.discovery import IssueDiscovery
from issuehunter.github_client import GitHubAPIError
from issuehunter.models import Issue, IssueAnalysis
from issuehunter.repository_policy import RepositoryPolicy, RepositoryPolicyError
from issuehunter.storage import Storage

app = typer.Typer(
    name="issuehunter",
    help="Discover and investigate open-source GitHub issues.",
    no_args_is_help=True,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

console = Console()


def _fail(message: str) -> NoReturn:
    console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=1)


def _issue_from_row(row: object) -> Issue:
    """Convert a SQLite issue row into the public issue model."""
    try:
        return Issue(
            id=row["id"],
            number=row["github_number"],
            title=row["title"],
            body=row["body"],
            html_url=row["html_url"],
            state=row["state"],
            repository_full_name=row["repository_full_name"],
            repository_url=(f"https://api.github.com/repos/{row['repository_full_name']}"),
            language=row["language"],
            labels=json.loads(row["labels_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Stored issue data is invalid.") from exc


@app.command()
def discover(
    language: Annotated[
        str | None,
        typer.Option("--language", "-l", help="GitHub issue language filter."),
    ] = None,
    label: Annotated[
        list[str] | None,
        typer.Option("--label", help="Issue label; may be repeated."),
    ] = None,
    keyword: Annotated[
        list[str] | None,
        typer.Option("--keyword", help="Issue keyword; may be repeated."),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option("--limit", "-n", min=1, max=1000),
    ] = None,
) -> None:
    """Search GitHub, screen repositories, and save approved issues."""
    try:
        with IssueDiscovery() as discovery_service:
            result = discovery_service.discover(
                language=language,
                labels=label,
                keywords=keyword,
                limit=limit,
            )
    except (GitHubAPIError, RepositoryPolicyError, ValueError) as exc:
        _fail(str(exc))

    console.print(
        f"Discovered [bold]{result.discovered_count}[/bold] approved issue(s); "
        f"saved {result.saved_count}."
    )
    console.print(
        f"Repositories: {result.approved_repository_count} approved, "
        f"{result.rejected_repository_count} rejected, "
        f"{result.review_repository_count} needing review."
    )
    if result.screening_errors:
        console.print("[yellow]Screening errors:[/yellow]")
        for error in result.screening_errors:
            console.print(f"- {error}")

    _print_issue_table(result.issues)


@app.command("screen")
def screen_repository(
    repository: Annotated[
        str,
        typer.Argument(help="Repository in owner/name format."),
    ],
    force: Annotated[
        bool,
        typer.Option("--force", help="Ignore the 24-hour screening cache."),
    ] = False,
) -> None:
    """Screen a GitHub repository and cache the decision."""
    try:
        with RepositoryPolicy() as policy:
            result = policy.check(repository, force_refresh=force)
    except (RepositoryPolicyError, ValueError) as exc:
        _fail(str(exc))

    color = "green" if result.approved else "yellow"
    console.print(f"[{color}]{result.status}[/{color}] {result.full_name}")
    console.print(
        f"Stars: {result.stars} | Forks: {result.forks} | "
        f"Size: {result.size_kb:,} KB | Age: {result.age_days} days"
    )
    for reason in result.reasons:
        console.print(f"[red]- {reason}[/red]")
    for warning in result.warnings:
        console.print(f"[yellow]- {warning}[/yellow]")

    if not result.approved:
        raise typer.Exit(code=2)


@app.command("issues")
def list_issues(
    state: Annotated[
        str | None,
        typer.Option("--state", help="Filter by state, or use 'all'."),
    ] = "open",
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 50,
) -> None:
    """List issues saved in the local database."""
    if state == "all":
        state = None
    elif state not in {"open", "closed"}:
        _fail("State must be 'open', 'closed', or 'all'.")

    storage = Storage()
    storage.initialize()
    rows = storage.list_issues(state=state, limit=limit)
    _print_issue_table([_issue_from_row(row) for row in rows])


@app.command("repositories")
def list_repositories(
    status: Annotated[
        str | None,
        typer.Option("--status", help="Filter by screening status."),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", "-n", min=1)] = 50,
) -> None:
    """List repository screening decisions saved locally."""
    storage = Storage()
    storage.initialize()
    try:
        rows = storage.list_screened_repositories(
            screening_status=status,
            limit=limit,
        )
    except ValueError as exc:
        _fail(str(exc))

    table = Table("Repository", "Status", "Stars", "Last checked")
    for row in rows:
        table.add_row(
            row["full_name"],
            row["screening_status"],
            f"{row['stars']:,}",
            row["last_checked_at"],
        )
    console.print(table)


@app.command()
def analyze(
    issue_id: Annotated[
        int,
        typer.Argument(help="GitHub issue ID from the local database."),
    ],
    force: Annotated[
        bool,
        typer.Option("--force", help="Regenerate an existing analysis."),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Print the complete analysis as JSON."),
    ] = False,
) -> None:
    """Analyze a saved issue with the configured local Ollama model."""
    storage = Storage()
    storage.initialize()
    row = storage.get_issue(issue_id)
    if row is None:
        _fail(f"Issue {issue_id} was not found in the local database.")

    try:
        issue = _issue_from_row(row)
        with IssueAnalyzer(storage=storage) as analyzer:
            result = analyzer.analyze(issue, force=force)
    except (IssueAnalysisError, ValueError, ValidationError) as exc:
        _fail(str(exc))

    if json_output:
        console.print_json(result.model_dump_json())
        return

    _print_analysis(issue, result)


@app.command()
def health() -> None:
    """Check that the local SQLite database is available."""
    storage = Storage()
    storage.initialize()
    if not storage.health_check():
        _fail("Database health check failed.")
    console.print("[green]Database is healthy.[/green]")


def _print_issue_table(issues: list[Issue]) -> None:
    table = Table("ID", "Repository", "Issue", "Labels", "URL")
    for issue in issues:
        table.add_row(
            str(issue.id),
            issue.repository_full_name,
            f"#{issue.number} {issue.title}",
            ", ".join(issue.labels) or "-",
            str(issue.html_url),
        )
    console.print(table)


def _print_bullets(title: str, values: list[str]) -> None:
    if values:
        console.print(f"\n[bold]{title}[/bold]")
        for value in values:
            console.print(f"- {value}")


def _print_analysis(issue: Issue, result: IssueAnalysis) -> None:
    console.print(f"\n[bold cyan]{issue.title}[/bold cyan]")
    console.print(
        f"[bold]{issue.repository_full_name}#{issue.number}[/bold]  [dim]{issue.html_url}[/dim]"
    )

    metadata = Table.grid(padding=(0, 2))
    metadata.add_row(
        "[bold]Confidence[/bold]",
        f"{result.confidence:.0f}%",
        "[bold]Difficulty[/bold]",
        result.difficulty.value,
    )
    metadata.add_row(
        "[bold]Estimated effort[/bold]",
        (f"{result.estimated_hours:g} hours" if result.estimated_hours is not None else "Unknown"),
        "[bold]Root cause[/bold]",
        "Confirmed" if result.root_cause_confirmed else "Hypothesis",
    )
    metadata.add_row(
        "[bold]Reproduction[/bold]",
        result.reproduction_status.value,
        "[bold]Analyzed[/bold]",
        result.analyzed_at.isoformat(),
    )
    console.print(metadata)

    console.print("\n[bold]Summary[/bold]")
    console.print(result.summary)

    if result.suspected_root_cause:
        console.print("\n[bold]Suspected root cause[/bold]")
        console.print(result.suspected_root_cause)

    if result.evidence:
        console.print("\n[bold]Evidence[/bold]")
        evidence_table = Table("Source", "Type", "Finding")
        for item in result.evidence:
            evidence_table.add_row(
                item.source,
                item.evidence_type,
                item.description,
            )
        console.print(evidence_table)

    if result.relevant_files:
        _print_bullets("Relevant files", result.relevant_files)
    if result.relevant_components:
        _print_bullets("Relevant components", result.relevant_components)

    _print_bullets("Investigation notes", result.investigation_notes)
    _print_bullets("Implementation plan", result.implementation_plan)
    _print_bullets("Suggested changes", result.suggested_changes)
    _print_bullets("Regression tests", result.regression_tests)
    _print_bullets("Risks", result.risks)


if __name__ == "__main__":
    app()
