from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Posture(StrEnum):
    LEAN = "lean"
    BALANCED = "balanced"
    COMPREHENSIVE = "comprehensive"


class MonitoringMode(StrEnum):
    MANUAL = "manual"
    LOCAL = "local"
    SCHEDULED = "scheduled"


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=20_000)
    prd: str = Field(default="", max_length=200_000)
    technical_spec: str = Field(default="", max_length=200_000)
    stack: list[str] = Field(default_factory=list, max_length=40)
    posture: Posture = Posture.BALANCED
    capability_weights: dict[str, int] = Field(default_factory=dict)

    @field_validator("stack")
    @classmethod
    def normalize_stack(cls, values: list[str]) -> list[str]:
        clean = {value.strip().lower() for value in values if value.strip()}
        return sorted(clean)

    @field_validator("capability_weights")
    @classmethod
    def validate_weights(cls, values: dict[str, int]) -> dict[str, int]:
        for key, value in values.items():
            if not 1 <= value <= 5:
                raise ValueError(f"capability weight for {key!r} must be between 1 and 5")
        return values


class Capability(BaseModel):
    id: str
    name: str
    description: str
    weight: int = Field(ge=1, le=5)
    evidence: list[str] = Field(default_factory=list)


class TriggerEvidence(BaseModel):
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    control_fire_rate: float = Field(ge=0, le=1)
    marginal_delta: float = Field(ge=-1, le=1)
    conflict_penalty: float = Field(default=0, ge=0, le=1)
    observations: int = Field(default=0, ge=0)


class ScoreComponents(BaseModel):
    coverage: float
    criticality: float
    compatibility: float
    uniqueness: float
    health: float
    trigger_quality: float | None = None
    conflict_penalty: float = 0


class SkillRecommendation(BaseModel):
    rank: int
    id: str
    name: str
    description: str
    importance_score: float
    tier: str
    selected: bool
    confidence: str
    evidence_source: str
    publisher: str | None = None
    source_url: str | None = None
    covered_capabilities: list[str]
    components: ScoreComponents
    reasons: list[str]
    cautions: list[str]


class AnalysisResult(BaseModel):
    analysis_id: str
    created_at: datetime
    project: ProjectInput
    capabilities: list[Capability]
    recommendations: list[SkillRecommendation]
    selected_count: int
    candidate_count: int
    weighted_coverage: float
    gaps: list[str]
    evidence_notice: str


class PrivacySettings(BaseModel):
    consent_version: str = "1"
    monitoring_mode: MonitoringMode = MonitoringMode.MANUAL
    monitored_scopes: list[str] = Field(default_factory=list)
    cloud_sync: bool = False
    anonymous_telemetry: bool = False
    automatic_api_spend: bool = False
    monthly_spend_limit_usd: float = Field(default=0, ge=0, le=10_000)
    automatic_changes: bool = False
    retention_days: int = Field(default=30, ge=0, le=3650)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("monitored_scopes")
    @classmethod
    def deduplicate_scopes(cls, values: list[str]) -> list[str]:
        return sorted({value.strip() for value in values if value.strip()})


class LocalDataSummary(BaseModel):
    analyses: int
    database_path: str
    monitoring_preference: MonitoringMode
