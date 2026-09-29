from skillscope.analysis import analyze_project, extract_capabilities
from skillscope.models import Posture, ProjectInput, TriggerEvidence


def project(posture: Posture = Posture.BALANCED) -> ProjectInput:
    return ProjectInput(
        name="Northstar",
        description="A multi-tenant SaaS dashboard for agencies.",
        prd=(
            "Teams need role-based permissions, email invitations, Stripe subscriptions, "
            "analytics dashboards, accessible keyboard navigation, and critical-flow testing."
        ),
        technical_spec=(
            "Use Next.js App Router, React, TypeScript, PostgreSQL with Drizzle, Clerk, "
            "Tailwind, Playwright, GitHub Actions, Vercel, and Sentry observability."
        ),
        stack=[
            "Next.js",
            "React",
            "TypeScript",
            "PostgreSQL",
            "Drizzle",
            "Clerk",
            "Stripe",
            "Tailwind",
            "Playwright",
        ],
        posture=posture,
    )


def test_extracts_capabilities_with_evidence_and_weights() -> None:
    capabilities = {item.id: item for item in extract_capabilities(project())}
    assert {"authentication", "authorization", "billing", "frontend", "database"} <= set(
        capabilities
    )
    assert capabilities["billing"].weight == 5
    assert any("stripe" in evidence.lower() for evidence in capabilities["billing"].evidence)


def test_user_capability_weight_overrides_inference() -> None:
    overridden = project().model_copy(update={"capability_weights": {"billing": 1}})
    capabilities = {item.id: item for item in extract_capabilities(overridden)}
    assert capabilities["billing"].weight == 1


def test_balanced_recommendation_uses_verified_project_specific_skills() -> None:
    result = analyze_project(project())
    selected = {item.id for item in result.recommendations if item.selected}
    assert selected == {"frontend-design", "webapp-testing"}
    assert result.weighted_coverage < 50
    assert "Billing" in result.gaps
    assert "verified against Anthropic's public repository" in result.evidence_notice
    assert all(item.evidence_source == "Planning estimate" for item in result.recommendations)
    assert all(item.source_url and item.publisher == "Anthropic" for item in result.recommendations)


def test_catalog_posture_changes_selected_count() -> None:
    rich_project = ProjectInput(
        name="Artifacts",
        description=(
            "Build a Claude web artifact and MCP server, test the web app, create a PDF, "
            "PowerPoint slide deck, Excel spreadsheet, Word document, and technical docs."
        ),
    )
    lean = analyze_project(rich_project.model_copy(update={"posture": Posture.LEAN}))
    balanced = analyze_project(rich_project.model_copy(update={"posture": Posture.BALANCED}))
    comprehensive = analyze_project(
        rich_project.model_copy(update={"posture": Posture.COMPREHENSIVE})
    )
    assert lean.selected_count <= balanced.selected_count <= comprehensive.selected_count
    assert lean.selected_count < comprehensive.selected_count


def test_measured_evidence_is_labeled_and_affects_confidence() -> None:
    evidence = {
        "webapp-testing": TriggerEvidence(
            precision=0.95,
            recall=0.9,
            control_fire_rate=0.02,
            marginal_delta=0.2,
            observations=36,
        )
    }
    result = analyze_project(project(), evidence)
    tested = next(item for item in result.recommendations if item.id == "webapp-testing")
    assert tested.evidence_source == "Measured trigger evidence"
    assert tested.confidence == "High"
    assert tested.components.trigger_quality is not None
    assert any("36 observations" in reason for reason in tested.reasons)


def test_vague_project_does_not_claim_measured_evidence() -> None:
    result = analyze_project(ProjectInput(name="Idea", description="Something useful"))
    assert result.capabilities[0].id == "documentation"
    assert "Project fit is a planning estimate only" in result.evidence_notice
