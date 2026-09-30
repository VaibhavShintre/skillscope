from pathlib import Path

from skills_evaluator import cli
from skills_evaluator.analysis import analyze
from skills_evaluator.decoy import DECOY_NAME, build_decoy
from skills_evaluator.engine import FakeEngine
from skills_evaluator.models import (
    EvalPrompt,
    ExperimentConfig,
    ProjectProfile,
    RunPlan,
    SessionResult,
    SkillCandidate,
)
from skills_evaluator.planner import build_plan
from skills_evaluator.report import markdown_report, write_reports
from skills_evaluator.storage import append_result, load_plan, session_order

FIXTURES = Path(__file__).parent / "fixtures"


class OverEagerEngine:
    """Loads every skill it is offered on every prompt, the decoy included."""

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


def _plan(tmp_path: Path, **options):
    return build_plan(
        project=FIXTURES / "project",
        sources=[str(FIXTURES / "skills" / "testing"), str(FIXTURES / "skills" / "frontend")],
        run_root=tmp_path / "runs",
        include_anthropic=False,
        **options,
    )


def _run(plan: RunPlan, run_dir: Path, engine) -> list[SessionResult]:
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
    return results


def test_the_decoy_is_off_by_default_and_a_candidate_with_two_control_configs(
    tmp_path: Path,
) -> None:
    plain, _ = _plan(tmp_path / "plain")
    assert not any(item.decoy for item in plain.candidates)
    assert not any(item.kind == "decoy" for item in plain.configurations)

    plan, _ = _plan(tmp_path / "decoy", decoy=True)
    decoy = next(item for item in plan.candidates if item.decoy)
    assert decoy.name == DECOY_NAME and decoy.body.strip().endswith("normal judgment.")
    controls = {item.id: item for item in plan.configurations if item.kind == "decoy"}
    assert controls["decoy-only"].skill_ids == [decoy.id]
    real = [item.id for item in plan.candidates if not item.decoy]
    assert controls["decoy-full"].skill_ids == [*real, decoy.id]
    # No prompt is written for it: it is never expected to fire.
    assert not any(DECOY_NAME in item.id for item in plan.prompts)
    assert plan.planned_sessions == plain.planned_sessions + 2 * len(plan.prompts)


def test_an_ideal_run_leaves_the_decoy_silent_and_does_not_recommend_it(tmp_path: Path) -> None:
    plan, run_dir = _plan(tmp_path, decoy=True)
    results = _run(plan, run_dir, FakeEngine())
    recommendation = analyze(load_plan(run_dir), results)

    assert all(DECOY_NAME != item.name for item in recommendation.verdicts)
    decoy = recommendation.decoy
    assert decoy is not None and decoy.name == DECOY_NAME
    assert decoy.sessions == 2 * len(plan.prompts)
    assert decoy.fired == 0 and decoy.fire_rate == 0.0
    assert decoy.advantage == 0.0 and decoy.flagged_configs == []
    assert build_decoy().id not in recommendation.recommended_skill_ids
    assert {item.id for item in recommendation.config_scores} >= {"decoy-only", "decoy-full"}

    report = markdown_report(plan, recommendation)
    assert "## Decoy control" in report
    assert "Fired in **0 of" in report and "Decoy warning" not in report


def test_a_decoy_that_fires_is_reported_and_every_firing_counts_against_it(
    tmp_path: Path,
) -> None:
    plan, run_dir = _plan(tmp_path, decoy=True)
    results = _run(plan, run_dir, OverEagerEngine())
    recommendation = analyze(load_plan(run_dir), results)

    decoy = recommendation.decoy
    assert decoy is not None
    assert decoy.fired == decoy.sessions == 2 * len(plan.prompts)
    assert decoy.fire_rate == 1.0
    assert decoy.fired_on_negative > 0 and decoy.fired_on_positive > 0
    only = next(item for item in recommendation.config_scores if item.id == "decoy-only")
    assert only.negative_score == 0.0 and only.positive_score == 0.0  # always wrong
    assert any("decoy skill" in item and "100%" in item for item in recommendation.limitations)
    report = markdown_report(plan, recommendation)
    assert "Fired in **" in report and "(100.0%)" in report


def _synthetic(hits_alone: int, hits_with_decoy: int) -> tuple[RunPlan, list[SessionResult]]:
    """One real skill and the decoy; 5 positives that expect the skill, 5 controls.

    The skill answers `hits_alone` positives by itself and `hits_with_decoy` when the decoy is
    listed too. Nothing ever fires wrongly, and the decoy never fires.
    """
    decoy = build_decoy()
    skill = SkillCandidate(
        id="skill-1",
        name="skill-1",
        description="x",
        source="test",
        source_path="/x",
        content_hash="h",
        body="",
        user_requested=True,
    )
    prompts = [
        EvalPrompt(id=f"want-{index}", text="x", expected_skill="skill-1", origin="user")
        for index in range(5)
    ] + [EvalPrompt(id=f"quiet-{index}", text="x", origin="control") for index in range(5)]
    configs = [
        ExperimentConfig(id="baseline", skill_ids=[], kind="baseline"),
        ExperimentConfig(id="singleton-01", skill_ids=["skill-1"], kind="singleton"),
        ExperimentConfig(id="decoy-only", skill_ids=[decoy.id], kind="decoy"),
        ExperimentConfig(id="decoy-full", skill_ids=["skill-1", decoy.id], kind="decoy"),
    ]
    plan = RunPlan(
        run_id="run",
        project=ProjectProfile(root="/p", name="p", content_hash="h"),
        candidates=[skill, decoy],
        prompts=prompts,
        configurations=configs,
        model="claude-haiku-4-5-20251001",
        seed=1,
        max_sessions=1000,
        max_cost_usd=5,
        estimated_max_cost_usd=1,
        planned_sessions=len(prompts) * len(configs),
    )
    hits = {"singleton-01": hits_alone, "decoy-full": hits_with_decoy}
    results = []
    for config in configs:
        for prompt in prompts:
            wanted = prompt.id.startswith("want-")
            fires = (
                wanted
                and "skill-1" in config.skill_ids
                and int(prompt.id.split("-")[1]) < hits[config.id]
            )
            results.append(
                SessionResult(
                    session_key=f"{config.id}::{prompt.id}",
                    config_id=config.id,
                    prompt_id=prompt.id,
                    prompt_origin=prompt.origin,
                    expected_skill=prompt.expected_skill,
                    available_skills=config.skill_ids,
                    selected_skills=["skill-1"] if fires else [],
                    outcome="completed",
                )
            )
    return plan, results


def test_a_bundle_whose_lead_only_matches_the_decoys_is_flagged(tmp_path: Path) -> None:
    # skill-1 answers 2 of 5 positives alone (0.7 against 0.5), and 4 of 5 when a useless
    # decoy is listed beside it: merely listing another skill lifted the score by 0.2.
    plan, results = _synthetic(hits_alone=2, hits_with_decoy=4)
    recommendation = analyze(plan, results)

    assert recommendation.recommended_skill_ids == ["skill-1"]
    decoy = recommendation.decoy
    assert decoy is not None and decoy.advantage == 0.2
    assert decoy.flagged_configs == ["singleton-01"] and decoy.recommended_matches is True
    scores = {item.id: item for item in recommendation.config_scores}
    assert scores["singleton-01"].matches_decoy

    report = markdown_report(plan, recommendation)
    assert "> **Decoy warning.**" in report and "matches the decoy's lead" in report
    assert any("may not come from what the skills contain" in x for x in recommendation.limitations)
    write_reports(tmp_path, plan, recommendation)
    saved = (tmp_path / "recommendation.json").read_text(encoding="utf-8")
    assert '"recommended_matches": true' in saved


def test_a_bundle_that_clearly_outdoes_the_decoy_is_not_flagged() -> None:
    plan, results = _synthetic(hits_alone=4, hits_with_decoy=4)  # the decoy lifts nothing
    recommendation = analyze(plan, results)
    assert recommendation.recommended_skill_ids == ["skill-1"]
    assert recommendation.decoy is not None
    assert recommendation.decoy.advantage == 0.0
    assert recommendation.decoy.flagged_configs == []
    assert "Decoy warning" not in markdown_report(plan, recommendation)


def test_a_run_with_the_decoy_executes_without_reloading_it_from_disk(tmp_path: Path) -> None:
    plan, run_dir = _plan(tmp_path, decoy=True)
    recommendation = cli._execute(run_dir, fake=True)
    assert recommendation.decoy is not None
    assert recommendation.decoy.sessions == 2 * len(plan.prompts)
    assert "## Decoy control" in (run_dir / "report.md").read_text(encoding="utf-8")
