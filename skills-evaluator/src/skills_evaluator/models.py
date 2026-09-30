from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator


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
    categories: list[str] = Field(default_factory=list)
    # The built-in control skill: never recommended, only measured.
    decoy: bool = False


LABELS = ("positive", "negative", "unlabeled")


class EvalPrompt(BaseModel):
    id: str
    text: str
    expected_skill: str | None = None
    expected_skills: list[str] = Field(default_factory=list)
    origin: str
    capability: str | None = None
    # positive: some skill in the expected set should fire; negative: none should;
    # unlabeled: unknown, so excluded from scoring. None means "not resolved yet".
    label: str | None = None
    label_source: str | None = None

    @field_validator("label")
    @classmethod
    def _known_label(cls, value: str | None) -> str | None:
        if value is not None and value not in LABELS:
            raise ValueError(f"label must be one of {', '.join(LABELS)}")
        return value

    @model_validator(mode="after")
    def _label_matches_expectation(self) -> EvalPrompt:
        expected = bool(self.expected_skill or self.expected_skills)
        if self.label == "positive" and not expected:
            raise ValueError(f"Prompt {self.id!r} is positive but names no expected skill.")
        if self.label in ("negative", "unlabeled") and expected:
            raise ValueError(f"Prompt {self.id!r} is {self.label} but names an expected skill.")
        return self


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
    estimated_expected_cost_usd: float = 0.0
    planned_sessions: int
    # Auto-discovered skills the planner left out because the caps could not cover them.
    dropped_candidates: list[str] = Field(default_factory=list)


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


class LabelSummary(BaseModel):
    positive_prompts: int = 0
    negative_prompts: int = 0
    unlabeled_prompts: int = 0
    unlabeled_prompt_ids: list[str] = Field(default_factory=list)
    unlabeled_sessions: int = 0
    uncategorized_skills: list[str] = Field(default_factory=list)
    # Activations of uncategorized skills on capability-derived prompts: neither right nor wrong.
    unjudged_activations: int = 0


class ConfigScore(BaseModel):
    id: str
    kind: str
    skill_names: list[str] = Field(default_factory=list)
    # Rates over labeled sessions of each kind; the score is their average, so a configuration
    # that stays silent scores 50% however many negative prompts there are.
    positive_score: float | None = None
    negative_score: float | None = None
    score: float | None = None
    accuracy: float | None = None  # plain share of labeled sessions handled correctly
    positive_prompts: int = 0
    negative_prompts: int = 0
    # True when its advantage over no skills is no larger than a useless decoy skill's.
    matches_decoy: bool = False


class DecoyReport(BaseModel):
    name: str
    sessions: int = 0  # completed sessions in which the decoy was listed
    fired: int = 0
    fire_rate: float | None = None
    fired_on_negative: int = 0
    fired_on_positive: int = 0
    # The most listing a useless skill lifted a score by: max(decoy alone - baseline,
    # full + decoy - full). None when those configurations were not measured.
    advantage: float | None = None
    flagged_configs: list[str] = Field(default_factory=list)
    recommended_matches: bool = False


class Advice(BaseModel):
    """The short "What to do" section: plain statements built from measured facts."""

    # "install", "inconclusive", "no_skills" or "insufficient"; only "install" carries advice.
    status: str
    headline: str
    keep: list[str] = Field(default_factory=list)
    drop: list[str] = Field(default_factory=list)
    cautions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)


class SkillVerdict(BaseModel):
    skill_id: str
    name: str
    verdict: str
    # Facts behind the plain-language advice. `helped_with` are the kinds of task where it
    # fired correctly (in the recommended bundle for a recommended skill); `over_fires_on` are
    # the kinds where it fired and should not have.
    helped_with: list[str] = Field(default_factory=list)
    over_fires_on: list[str] = Field(default_factory=list)
    fired: int = 0
    sessions: int = 0  # completed sessions in which the skill was listed
    # True for skills the planner added from the public catalog, not passed with --skill.
    auto_discovered: bool = False
    # Precision is measured in the full bundle; None when no labeled prompt activated it.
    precision: float | None = None
    precision_by_config: dict[str, float | None] = Field(default_factory=dict)
    recall: float | None = None
    singleton_score: float | None = None
    leave_one_out_delta: float | None = None
    # For a skill in the recommended bundle: the recommended bundle's score minus the score of
    # that bundle without the skill (the baseline, for a one-skill bundle), when it was measured.
    bundle_delta: float | None = None
    selected: bool
    reasons: list[str] = Field(default_factory=list)


class Recommendation(BaseModel):
    run_id: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    recommended_config: str
    recommended_skill_ids: list[str]
    recommended_count: int
    best_score: float | None = None
    recommended_score: float | None = None
    tolerance: float
    total_cost_usd: float
    completed_sessions: int
    verdicts: list[SkillVerdict]
    labeling: LabelSummary = Field(default_factory=LabelSummary)
    # True when the no-skill baseline scored at least as well as the best bundle.
    no_skills_recommended: bool = False
    # True when more than half the prompts are unlabeled: no recommendation is issued.
    insufficient_evidence: bool = False
    # True when the no-skill baseline and the best bundle differ by no more than the noise floor.
    inconclusive: bool = False
    best_config: str = ""
    config_scores: list[ConfigScore] = Field(default_factory=list)
    positive_prompts_per_config: int = 0
    negative_prompts_per_config: int = 0
    # How much one prompt can move the balanced score (the larger of the two classes' steps).
    noise_step: float | None = None
    decoy: DecoyReport | None = None
    what_to_do: Advice | None = None
    # The tolerance actually applied: at least one prompt's worth of score.
    effective_tolerance: float | None = None
    # Estimates for the sessions that ran, to compare with total_cost_usd.
    expected_cost_usd: float = 0.0
    worst_case_cost_usd: float = 0.0
    baseline_score: float | None = None
    scored_prompts_per_config: int = 0
    limitations: list[str]


def path_text(path: Path) -> str:
    return str(path.resolve())
