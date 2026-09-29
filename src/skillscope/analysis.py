from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from uuid import uuid4

from skillscope.catalog import CATALOG, CATALOG_VERIFIED_AT, SkillDefinition
from skillscope.models import (
    AnalysisResult,
    Capability,
    Posture,
    ProjectInput,
    ScoreComponents,
    SkillRecommendation,
    TriggerEvidence,
)


@dataclass(frozen=True)
class CapabilityRule:
    id: str
    name: str
    description: str
    default_weight: int
    keywords: tuple[str, ...]


RULES: tuple[CapabilityRule, ...] = (
    CapabilityRule(
        "authentication",
        "Authentication",
        "Identity, sessions, sign-in, invitations, and account lifecycle.",
        5,
        ("authentication", "login", "sign in", "sign-up", "signup", "session", "clerk", "auth0"),
    ),
    CapabilityRule(
        "authorization",
        "Authorization",
        "Roles, permissions, tenancy boundaries, and access control.",
        5,
        ("authorization", "permission", "role-based", "rbac", "multi-tenant", "organization"),
    ),
    CapabilityRule(
        "billing",
        "Billing",
        "Subscriptions, checkout, pricing, invoicing, and payment lifecycle.",
        5,
        ("billing", "stripe", "subscription", "checkout", "invoice", "payment", "pricing"),
    ),
    CapabilityRule(
        "frontend",
        "Frontend application",
        "Interactive pages, components, state, and user-facing application behavior.",
        4,
        (
            "frontend",
            "dashboard",
            "component",
            "react",
            "next.js",
            "nextjs",
            "web app",
            "user interface",
        ),
    ),
    CapabilityRule(
        "routing",
        "Routing and rendering",
        "Routes, layouts, loading states, navigation, and rendering boundaries.",
        4,
        ("app router", "route", "routing", "layout", "loading state", "next.js", "nextjs"),
    ),
    CapabilityRule(
        "database",
        "Data layer",
        "Relational models, persistence, queries, transactions, and migrations.",
        4,
        (
            "database",
            "postgres",
            "postgresql",
            "drizzle",
            "prisma",
            "sql",
            "migration",
            "data model",
        ),
    ),
    CapabilityRule(
        "api",
        "API and integrations",
        "Service contracts, validation, webhooks, and external integrations.",
        4,
        ("api", "webhook", "integration", "endpoint", "graphql", "rest", "fastapi", "express"),
    ),
    CapabilityRule(
        "testing",
        "Automated testing",
        "Critical-flow, integration, browser, and regression testing.",
        4,
        ("test", "testing", "playwright", "vitest", "cypress", "quality assurance", "regression"),
    ),
    CapabilityRule(
        "styling",
        "Styling",
        "Responsive layout, visual styling, and reusable presentation conventions.",
        3,
        ("tailwind", "css", "responsive", "styling", "visual design"),
    ),
    CapabilityRule(
        "design-system",
        "Design system",
        "Reusable UI primitives, tokens, and consistent interaction patterns.",
        3,
        ("design system", "component library", "design token", "shared components", "storybook"),
    ),
    CapabilityRule(
        "accessibility",
        "Accessibility",
        "Keyboard, screen-reader, semantic, and inclusive interaction requirements.",
        3,
        ("accessibility", "accessible", "wcag", "screen reader", "keyboard navigation", "a11y"),
    ),
    CapabilityRule(
        "deployment",
        "Deployment",
        "Hosting, environment configuration, previews, releases, and rollout.",
        3,
        ("deploy", "deployment", "hosting", "vercel", "production environment", "release"),
    ),
    CapabilityRule(
        "ci-cd",
        "Continuous integration",
        "Automated checks, builds, and release workflows.",
        3,
        ("continuous integration", "ci/cd", "github actions", "build pipeline", "release pipeline"),
    ),
    CapabilityRule(
        "observability",
        "Observability",
        "Logs, metrics, traces, production diagnostics, and alerting.",
        3,
        ("observability", "logging", "metrics", "tracing", "sentry", "datadog", "alerting"),
    ),
    CapabilityRule(
        "security",
        "Security",
        "Threat-aware handling of secrets, input, dependencies, and sensitive data.",
        4,
        ("security", "secure", "threat", "secret", "soc 2", "compliance", "audit log"),
    ),
    CapabilityRule(
        "documentation",
        "Documentation",
        "Architecture, API, onboarding, and operational documentation.",
        2,
        ("documentation", "docs", "runbook", "architecture decision", "onboarding guide"),
    ),
    CapabilityRule(
        "claude-api",
        "Claude API development",
        "Applications powered by Claude or the Anthropic SDK.",
        4,
        ("claude api", "anthropic api", "anthropic sdk", "claude sdk"),
    ),
    CapabilityRule(
        "mcp",
        "MCP server development",
        "Model Context Protocol servers and external-service tools.",
        4,
        ("mcp server", "model context protocol", "fastmcp"),
    ),
    CapabilityRule(
        "skill-authoring",
        "Agent Skill authoring",
        "Creating, improving, or benchmarking Agent Skills.",
        4,
        ("agent skill", "claude skill", "skill.md", "skill benchmark"),
    ),
    CapabilityRule(
        "claude-artifact",
        "Claude web artifact",
        "Complex interactive HTML artifacts designed to run in Claude.ai.",
        4,
        ("claude artifact", "claude.ai artifact", "web artifact"),
    ),
    CapabilityRule(
        "word-documents",
        "Word documents",
        "Microsoft Word document creation or editing.",
        3,
        (".docx", "word document", "microsoft word"),
    ),
    CapabilityRule(
        "pdf",
        "PDF documents",
        "PDF creation, extraction, editing, forms, or OCR.",
        3,
        (".pdf", "pdf document", "pdf form", "pdf report"),
    ),
    CapabilityRule(
        "presentations",
        "Presentations",
        "PowerPoint presentations, slide decks, or pitch decks.",
        3,
        (".pptx", "powerpoint", "slide deck", "pitch deck", "presentation"),
    ),
    CapabilityRule(
        "spreadsheets",
        "Spreadsheets",
        "Spreadsheet creation, editing, analysis, or charting.",
        3,
        (".xlsx", "spreadsheet", "excel workbook", "excel file"),
    ),
    CapabilityRule(
        "internal-comms",
        "Internal communications",
        "Internal status, leadership, incident, or company communications.",
        2,
        ("internal communications", "leadership update", "status report", "company newsletter"),
    ),
    CapabilityRule(
        "generative-art",
        "Generative art",
        "Original algorithmic artwork made with code.",
        3,
        ("generative art", "algorithmic art", "flow field", "particle art"),
    ),
    CapabilityRule(
        "visual-art",
        "Static visual design",
        "Original posters, visual art, or static canvas designs.",
        3,
        ("poster design", "static visual", "canvas design"),
    ),
    CapabilityRule(
        "anthropic-brand",
        "Anthropic branding",
        "Artifacts that must follow Anthropic's brand identity.",
        3,
        ("anthropic brand", "anthropic branding", "anthropic style"),
    ),
    CapabilityRule(
        "theming",
        "Artifact theming",
        "Coordinated visual themes for documents, slides, or web artifacts.",
        2,
        ("artifact theme", "theme factory", "themed slides", "themed document"),
    ),
    CapabilityRule(
        "slack-gif",
        "Slack GIF",
        "Animated GIF creation optimized for Slack.",
        2,
        ("slack gif", "gif for slack"),
    ),
    CapabilityRule(
        "claude-learning",
        "Claude learning resources",
        "Claude Academy training, onboarding, or learning guidance.",
        2,
        ("claude academy", "learn claude", "claude training"),
    ),
    CapabilityRule(
        "decision-support",
        "Decision review",
        "Prompts that help users examine assumptions before acting.",
        2,
        ("decision review", "challenge assumptions", "discernment"),
    ),
)


def extract_capabilities(project: ProjectInput) -> list[Capability]:
    stack_text = " ".join(project.stack)
    content = " ".join(
        (project.description, project.prd, project.technical_spec, stack_text)
    ).lower()
    capabilities: list[Capability] = []
    for rule in RULES:
        matches = sorted({keyword for keyword in rule.keywords if keyword in content})
        if not matches:
            continue
        capabilities.append(
            Capability(
                id=rule.id,
                name=rule.name,
                description=rule.description,
                weight=project.capability_weights.get(rule.id, rule.default_weight),
                evidence=[f"Matched “{keyword}”" for keyword in matches[:4]],
            )
        )
    if not capabilities:
        capabilities.append(
            Capability(
                id="documentation",
                name="Project definition",
                description=(
                    "Clarify the project requirements and architecture before specialization."
                ),
                weight=2,
                evidence=["No specific technical capability was detected"],
            )
        )
    return capabilities


def _compatibility(skill: SkillDefinition, project: ProjectInput, content: str) -> float:
    if not skill.stack_signals:
        return 82.0
    declared = set(project.stack)
    if any(signal in declared or signal in content for signal in skill.stack_signals):
        return 100.0
    return 38.0 if declared else 65.0


def _trigger_quality(evidence: TriggerEvidence) -> float:
    marginal = max(0.0, min(100.0, evidence.marginal_delta * 400))
    return (
        evidence.precision * 30
        + evidence.recall * 30
        + (1 - evidence.control_fire_rate) * 15
        + marginal * 0.25
    )


def _confidence(evidence: TriggerEvidence | None) -> str:
    if evidence is None:
        return "Planning estimate"
    if evidence.observations >= 30:
        return "High"
    if evidence.observations >= 12:
        return "Medium"
    return "Low"


def _select_skill_ids(
    posture: Posture,
    recommendations: list[dict[str, object]],
    capabilities: dict[str, Capability],
    total_weight: int,
) -> set[str]:
    if posture is Posture.LEAN:
        target, maximum, minimum = 0.75, 5, 3
    elif posture is Posture.COMPREHENSIVE:
        target, maximum, minimum = 1.0, 12, 6
    else:
        target, maximum, minimum = 0.92, 8, 5

    selected: set[str] = set()
    covered: set[str] = set()
    candidates = [
        item for item in recommendations if item["covered"] and float(item["score"]) >= 25
    ]
    while candidates and len(selected) < maximum:

        def selection_value(item: dict[str, object]) -> tuple[float, float]:
            skill = item["skill"]
            assert isinstance(skill, SkillDefinition)
            new_capabilities = set(item["covered"]) - covered
            marginal_weight = sum(capabilities[cap_id].weight for cap_id in new_capabilities)
            breadth = 1 + max(0, len(skill.capabilities) - 2) * 0.6
            value = marginal_weight * 10 / breadth + float(item["score"]) * 0.08
            return value, float(item["score"])

        choice = max(candidates, key=selection_value)
        skill = choice["skill"]
        assert isinstance(skill, SkillDefinition)
        selected.add(skill.id)
        covered.update(choice["covered"])
        candidates.remove(choice)
        coverage = sum(capabilities[cap_id].weight for cap_id in covered) / total_weight
        if len(selected) >= minimum and coverage >= target:
            break
    return selected


def analyze_project(
    project: ProjectInput,
    evidence_by_skill: dict[str, TriggerEvidence] | None = None,
) -> AnalysisResult:
    evidence_by_skill = evidence_by_skill or {}
    capabilities = extract_capabilities(project)
    cap_by_id = {cap.id: cap for cap in capabilities}
    total_weight = sum(cap.weight for cap in capabilities)
    content = " ".join(
        (project.description, project.prd, project.technical_spec, " ".join(project.stack))
    ).lower()

    providers: dict[str, int] = {
        cap.id: sum(cap.id in skill.capabilities for skill in CATALOG) for cap in capabilities
    }
    drafts: list[dict[str, object]] = []
    for skill in CATALOG:
        covered = sorted(set(cap_by_id) & set(skill.capabilities))
        covered_weight = sum(cap_by_id[cap_id].weight for cap_id in covered)
        coverage = 100 * covered_weight / total_weight if total_weight else 0
        criticality = 100 * max((cap_by_id[cap_id].weight for cap_id in covered), default=0) / 5
        compatibility = _compatibility(skill, project, content)
        uniqueness_values = [1 / providers[cap_id] for cap_id in covered if providers[cap_id]]
        uniqueness = 100 * mean(uniqueness_values) if uniqueness_values else 0
        health = skill.health * 100
        evidence = evidence_by_skill.get(skill.id)
        trigger_quality = _trigger_quality(evidence) if evidence else None
        breadth_penalty = max(0.0, (len(skill.capabilities) - 3) * 6.0)
        conflict_penalty = (evidence.conflict_penalty * 100 if evidence else 0) + breadth_penalty
        planning_score = (
            coverage * 0.4
            + criticality * 0.2
            + compatibility * 0.2
            + uniqueness * 0.1
            + health * 0.1
        )
        score = planning_score
        if evidence:
            score = planning_score * 0.55 + trigger_quality * 0.45
        score = max(0.0, min(100.0, score - conflict_penalty * 0.18))

        reasons: list[str] = []
        cautions: list[str] = []
        if covered:
            names = [cap_by_id[cap_id].name for cap_id in covered]
            plural = "s" if len(covered) != 1 else ""
            reasons.append(f"Covers {len(covered)} detected area{plural}: {', '.join(names)}.")
            critical = [
                cap_by_id[cap_id].name for cap_id in covered if cap_by_id[cap_id].weight >= 4
            ]
            if critical:
                reasons.append(f"Supports high-priority work: {', '.join(critical)}.")
        else:
            cautions.append(
                "No direct requirement coverage was detected in the supplied project context."
            )
        if compatibility >= 90:
            reasons.append("Matches the declared or inferred technical stack.")
        elif compatibility < 50:
            cautions.append("Its preferred stack was not found in the supplied architecture.")
        if evidence:
            reasons.append(
                f"Measured on {evidence.observations} observations with "
                f"{evidence.precision:.0%} precision and {evidence.recall:.0%} recall."
            )
            if evidence.marginal_delta > 0:
                reasons.append(
                    "Removing it worsened routing by "
                    f"{evidence.marginal_delta * 100:.1f} percentage points."
                )
            if evidence.control_fire_rate > 0.1:
                cautions.append(f"Selected on {evidence.control_fire_rate:.0%} of control prompts.")
        else:
            cautions.append("Claude trigger behavior has not been measured yet.")
        if breadth_penalty:
            cautions.append("Broad coverage may overlap more specialized skills.")

        if evidence and evidence.conflict_penalty >= 0.5:
            tier = "Name-only"
        elif score >= 75 and any(cap_by_id[cap_id].weight >= 4 for cap_id in covered):
            tier = "Core"
        elif score >= 55:
            tier = "Recommended"
        elif score >= 38:
            tier = "Optional"
        else:
            tier = "Exclude"
        drafts.append(
            {
                "skill": skill,
                "covered": covered,
                "score": round(score, 1),
                "tier": tier,
                "confidence": _confidence(evidence),
                "source": "Measured trigger evidence" if evidence else "Planning estimate",
                "components": ScoreComponents(
                    coverage=round(coverage, 1),
                    criticality=round(criticality, 1),
                    compatibility=round(compatibility, 1),
                    uniqueness=round(uniqueness, 1),
                    health=round(health, 1),
                    trigger_quality=(
                        round(trigger_quality, 1) if trigger_quality is not None else None
                    ),
                    conflict_penalty=round(conflict_penalty, 1),
                ),
                "reasons": reasons,
                "cautions": cautions,
            }
        )

    drafts.sort(key=lambda item: (-float(item["score"]), str(item["skill"].id)))
    selected_ids = _select_skill_ids(project.posture, drafts, cap_by_id, total_weight)
    recommendations: list[SkillRecommendation] = []
    for index, draft in enumerate(drafts, start=1):
        skill = draft["skill"]
        assert isinstance(skill, SkillDefinition)
        recommendations.append(
            SkillRecommendation(
                rank=index,
                id=skill.id,
                name=skill.name,
                description=skill.description,
                importance_score=float(draft["score"]),
                tier=str(draft["tier"]),
                selected=skill.id in selected_ids,
                confidence=str(draft["confidence"]),
                evidence_source=str(draft["source"]),
                publisher=skill.publisher,
                source_url=skill.source_url,
                covered_capabilities=list(draft["covered"]),
                components=draft["components"],
                reasons=list(draft["reasons"]),
                cautions=list(draft["cautions"]),
            )
        )

    selected = [recommendation for recommendation in recommendations if recommendation.selected]
    covered_selected = {
        cap_id for recommendation in selected for cap_id in recommendation.covered_capabilities
    }
    selected_weight = sum(cap.weight for cap in capabilities if cap.id in covered_selected)
    weighted_coverage = 100 * selected_weight / total_weight if total_weight else 0
    gaps = [cap.name for cap in capabilities if cap.id not in covered_selected]
    has_measured = bool(evidence_by_skill)
    return AnalysisResult(
        analysis_id=str(uuid4()),
        created_at=datetime.now(UTC),
        project=project,
        capabilities=capabilities,
        recommendations=recommendations,
        selected_count=len(selected),
        candidate_count=len(recommendations),
        weighted_coverage=round(weighted_coverage, 1),
        gaps=gaps,
        evidence_notice=(
            "Measured trigger evidence was combined with project-planning evidence."
            if has_measured
            else (
                "Catalog package identities were verified against Anthropic's public "
                f"repository on {CATALOG_VERIFIED_AT}. Project fit is a planning estimate "
                "only; run the local Claude benchmark before treating routing scores as verified."
            )
        ),
    )
