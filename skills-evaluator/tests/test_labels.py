from pathlib import Path

import pytest

from skills_evaluator.analysis import analyze
from skills_evaluator.engine import FakeEngine
from skills_evaluator.labels import resolve_label, summarize_labels
from skills_evaluator.models import EvalPrompt, SessionResult, SkillCandidate
from skills_evaluator.planner import build_plan
from skills_evaluator.profiler import profile_project
from skills_evaluator.prompts import generate_prompts
from skills_evaluator.skills import load_skill
from skills_evaluator.storage import append_result, session_order

FIXTURES = Path(__file__).parent / "fixtures"


def _skill(name: str, categories: list[str] | None = None) -> SkillCandidate:
    return SkillCandidate(
        id=f"test@{name}#1",
        name=name,
        description=f"{name} skill",
        source="test",
        source_path=f"/skills/{name}",
        content_hash=name,
        body="",
        categories=categories or [],
    )


def _project_prompt(capability: str) -> EvalPrompt:
    return EvalPrompt(id=f"project-{capability}", text="x", origin="project", capability=capability)


class OverSelectingEngine:
    """An over-eager model: it loads every available skill on every prompt."""

    def run(self, session_key, config_id, prompt, profile, skills) -> SessionResult:
        return SessionResult(
            session_key=session_key,
            config_id=config_id,
            prompt_id=prompt.id,
            prompt_origin=prompt.origin,
            expected_skill=prompt.expected_skill,
            available_skills=[item.id for item in skills],
            selected_skills=[item.id for item in skills],
            outcome="completed",
        )


def test_capability_heuristic_labels_by_category_not_description() -> None:
    frontend = _skill("frontend-design")
    testing = _skill("browser-testing")
    candidates = [frontend, testing]

    positive = resolve_label(_project_prompt("frontend"), candidates)
    assert (positive.kind, positive.expected) == ("positive", {frontend.id})
    positive = resolve_label(_project_prompt("testing"), candidates)
    assert (positive.kind, positive.expected) == ("positive", {testing.id})
    # No candidate covers database work, and every candidate's category is known.
    assert resolve_label(_project_prompt("database"), candidates).kind == "negative"
    # A capability the heuristic has no mapping for is not guessed.
    assert resolve_label(_project_prompt("accessibility"), candidates).kind == "unlabeled"
    # The description mentions databases, but only the declared category counts.
    misleading = _skill("frontend-design", categories=["docs"])
    misleading.description = "Design database schemas and frontend pages"
    assert resolve_label(_project_prompt("database"), [misleading]).kind == "negative"
    assert resolve_label(_project_prompt("frontend"), [misleading]).kind == "negative"


def test_one_uncategorized_skill_makes_heuristic_labels_unlabeled() -> None:
    candidates = [_skill("frontend-design"), _skill("mystery-helper")]
    assert resolve_label(_project_prompt("frontend"), candidates).kind == "unlabeled"
    assert resolve_label(_project_prompt("database"), candidates).kind == "unlabeled"
    # Rules that don't depend on categories still apply.
    control = EvalPrompt(id="c", text="x", origin="control")
    assert resolve_label(control, candidates).kind == "negative"

    summary = summarize_labels([_project_prompt("frontend"), control], candidates)
    assert summary.unlabeled_prompts == 1
    assert summary.unlabeled_prompt_ids == ["project-frontend"]
    assert summary.uncategorized_skills == ["mystery-helper"]


def test_skill_frontmatter_category_is_parsed(tmp_path: Path) -> None:
    folder = tmp_path / "skill"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: pdf-helper\ndescription: Work with PDFs.\ncategory: [Docs, pdf]\n---\n# PDF\n",
        encoding="utf-8",
    )
    skill = load_skill(folder, profile_project(FIXTURES / "project"), "local", True)
    assert skill.categories == ["docs", "pdf"]


def test_explicit_labels_are_validated() -> None:
    with pytest.raises(ValueError, match="positive but names no expected skill"):
        EvalPrompt(id="p", text="x", origin="user", label="positive")
    with pytest.raises(ValueError, match="names an expected skill"):
        EvalPrompt(id="p", text="x", origin="user", label="negative", expected_skill="a")
    with pytest.raises(ValueError, match="label must be one of"):
        EvalPrompt(id="p", text="x", origin="user", label="maybe")


def test_yaml_labels_override_the_heuristic(tmp_path: Path) -> None:
    labels = tmp_path / "labels.yaml"
    labels.write_text(
        "\n".join(
            [
                "- id: project-05-testing",  # generated prompt, heuristic says positive
                "  label: negative",
                "- id: project-01-api",  # generated prompt, heuristic says negative
                "  expected_skills: [frontend-design]",
                "- id: mine",
                "  text: Review the visual design.",
                "  expected_skills: [frontend-design]",
            ]
        ),
        encoding="utf-8",
    )
    plan, _ = build_plan(
        project=FIXTURES / "project",
        sources=[str(FIXTURES / "skills" / "testing"), str(FIXTURES / "skills" / "frontend")],
        run_root=tmp_path / "runs",
        include_anthropic=False,
        prompt_file=labels,
    )
    frontend = next(item.id for item in plan.candidates if item.name == "frontend-design")
    by_id = {item.id: item for item in plan.prompts}

    testing = by_id["project-05-testing"]
    assert (testing.label, testing.label_source) == ("negative", "user")
    assert testing.expected_skills == []
    api = by_id["project-01-api"]
    assert (api.label, api.label_source, api.expected_skills) == ("positive", "user", [frontend])
    assert by_id["mine"].origin == "user"
    assert resolve_label(by_id["mine"], plan.candidates).expected == {frontend}
    # Untouched generated prompts keep their heuristic label.
    assert by_id["project-04-frontend"].label_source == "heuristic"


def test_label_override_for_unknown_prompt_is_rejected(tmp_path: Path) -> None:
    labels = tmp_path / "labels.yaml"
    labels.write_text("- id: does-not-exist\n  label: negative\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown prompt"):
        build_plan(
            project=FIXTURES / "project",
            sources=[str(FIXTURES / "skills" / "testing")],
            run_root=tmp_path / "runs",
            include_anthropic=False,
            prompt_file=labels,
        )


def test_near_miss_prompts_are_negative_and_never_mention_skills() -> None:
    profile = profile_project(FIXTURES / "project")
    prompts = generate_prompts(profile, [_skill("frontend-design"), _skill("browser-testing")])
    near_misses = {item.id: item for item in prompts if item.origin == "near-miss"}
    assert set(near_misses) == {"near-miss-frontend", "near-miss-testing"}
    for prompt in near_misses.values():
        assert "skill" not in prompt.text.lower()
        assert resolve_label(prompt, []).kind == "negative"

    uncategorized = generate_prompts(profile, [_skill("mystery-helper")])
    assert not [item for item in uncategorized if item.origin == "near-miss"]


def test_ideal_engine_scores_perfectly_and_over_selecting_engine_does_not(tmp_path: Path) -> None:
    def evaluate(engine, root: Path):
        plan, run_dir = build_plan(
            project=FIXTURES / "project",
            sources=[
                str(FIXTURES / "skills" / "testing"),
                str(FIXTURES / "skills" / "frontend"),
            ],
            run_root=root / "runs",
            include_anthropic=False,
            max_sessions=100,
        )
        configs = {item.id: item for item in plan.configurations}
        prompts = {item.id: item for item in plan.prompts}
        candidates = {item.id: item for item in plan.candidates}
        results = []
        for config_id, prompt_id in session_order(run_dir):
            available = [candidates[item] for item in configs[config_id].skill_ids]
            result = engine.run(
                f"{config_id}::{prompt_id}", config_id, prompts[prompt_id], plan.project, available
            )
            append_result(run_dir, result)
            results.append(result)
        return analyze(plan, results)

    ideal = evaluate(FakeEngine(), tmp_path / "ideal")
    noisy = evaluate(OverSelectingEngine(), tmp_path / "noisy")

    assert ideal.recommended_score == 1.0
    assert ideal.no_skills_recommended is False
    assert ideal.labeling.unlabeled_prompts == 0
    assert all(item.precision == 1.0 for item in ideal.verdicts)

    assert noisy.best_score is not None and noisy.best_score < 0.5
    # The skills do worse than having none, and the report says so.
    assert noisy.no_skills_recommended is True
    # Firing on every prompt means most activations are false positives.
    assert all(item.precision is not None and item.precision < 0.5 for item in noisy.verdicts)
    assert not any(item.verdict == "essential" for item in noisy.verdicts)
