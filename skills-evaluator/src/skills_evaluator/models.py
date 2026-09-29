from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field


class ProjectProfile(BaseModel):
    root: str
    name: str
    content_hash: str
    languages: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)
    context_files: list[str] = Field(default_factory=list)
    excluded_patterns: list[str] = Field(default_factory=list)
    summary: str = ""


class SecurityFinding(BaseModel):
    severity: str
    message: str
    path: str | None = None


class SkillCandidate(BaseModel):
    id: str
    name: str
    description: str
    source: str
    source_path: str
    source_commit: str | None = None
    content_hash: str
    body: str
    files: list[str] = Field(default_factory=list)
    findings: list[SecurityFinding] = Field(default_factory=list)
    blocked: bool = False
    user_requested: bool = False
    relevance: float = Field(default=0, ge=0, le=1)


class EvalPrompt(BaseModel):
    id: str
    text: str
    expected_skill: str | None = None
    origin: str
    capability: str | None = None


class ExperimentConfig(BaseModel):
    id: str
    skill_ids: list[str]
    kind: str


class RunPlan(BaseModel):
    schema_version: str = "1"
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    project: ProjectProfile
    candidates: list[SkillCandidate]
    prompts: list[EvalPrompt]
    configurations: list[ExperimentConfig]
    model: str
    seed: int
    max_sessions: int
    max_cost_usd: float
    estimated_max_cost_usd: float
    planned_sessions: int


class SessionResult(BaseModel):
    session_key: str
    config_id: str
    prompt_id: str
    prompt_origin: str = "unknown"
    expected_skill: str | None
    available_skills: list[str]
    selected_skills: list[str] = Field(default_factory=list)
    answer: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0
    duration_ms: int = 0
    outcome: str
    error: str | None = None


class SkillVerdict(BaseModel):
    skill_id: str
    name: str
    verdict: str
    precision: float
    recall: float
    singleton_score: float
    leave_one_out_delta: float
    selected: bool
    reasons: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    run_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    recommended_config: str
    recommended_skill_ids: list[str]
    recommended_count: int
    best_score: float
    recommended_score: float
    tolerance: float
    total_cost_usd: float
    completed_sessions: int
    verdicts: list[SkillVerdict]
    limitations: list[str]


def path_text(path: Path) -> str:
    return str(path.resolve())
