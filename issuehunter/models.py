
from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class IssueState(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class DeveloperLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class ReproductionStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    REPRODUCED = "reproduced"
    NOT_REPRODUCED = "not_reproduced"
    INCONCLUSIVE = "inconclusive"


class Issue(BaseModel):
    """A GitHub issue discovered by AutoIssueHunter."""

    model_config = ConfigDict(extra="ignore")

    id: int
    number: int
    title: str
    body: str = ""
    html_url: HttpUrl
    state: IssueState = IssueState.OPEN

    repository_full_name: str
    repository_url: HttpUrl
    repository_description: str | None = None
    language: str | None = None

    labels: list[str] = Field(default_factory=list)
    comments_count: int = 0

    created_at: datetime
    updated_at: datetime

    @property
    def is_open(self) -> bool:
        return self.state == IssueState.OPEN


class ContributionScore(BaseModel):
    """Measures how well an issue matches a developer's abilities."""

    cms: float = Field(ge=0, le=100)
    skill_match: float = Field(ge=0, le=100)
    complexity_match: float = Field(ge=0, le=100)
    project_interest_match: float = Field(ge=0, le=100)
    explanation: str = ""


class IssueAnalysis(BaseModel):
    """AI-generated investigation results for a GitHub issue."""

    issue_id: int

    summary: str
    suspected_root_cause: str | None = None

    relevant_files: list[str] = Field(default_factory=list)
    relevant_components: list[str] = Field(default_factory=list)

    difficulty: DeveloperLevel = DeveloperLevel.INTERMEDIATE
    estimated_hours: float | None = Field(default=None, ge=0)

    reproduction_status: ReproductionStatus = (
        ReproductionStatus.NOT_ATTEMPTED
    )

    confidence: float = Field(default=0, ge=0, le=100)
    contribution_score: ContributionScore | None = None

    implementation_plan: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)

    analyzed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
