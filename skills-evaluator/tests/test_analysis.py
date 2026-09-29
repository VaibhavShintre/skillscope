import json
from pathlib import Path

import pytest

from skills_evaluator.analysis import analyze
from skills_evaluator.models import (
    EvalPrompt,
    ExperimentConfig,
    ProjectProfile,
    RunPlan,
    SessionResult,
    SkillCandidate,
)
from skills_evaluator.planner import _configurations
from skills_evaluator.report import markdown_report, write_reports


def _skill(index: int) -> SkillCandidate:
    return SkillCandidate(
        id=f"skill-{index}",
        name=f"skill-{index}",
        description=f"Skill {index}",
        source="test",
        source_path=f"/skills/{index}",
        content_hash=f"hash-{index}",
        body="",
    )


def _plan(skill_count: int, keep_configs: int | None = None) -> RunPlan:
    candidates = [_skill(index) for index in range(1, skill_count + 1)]
    prompts = [
        EvalPrompt(
            id=f"positive-{item.id}", text="x", expected_skill=item.id, origin="skill-positive"
        )
        for item in candidates
    ]
    prompts.append(EvalPrompt(id="control", text="x", origin="control"))
    configs: list[ExperimentConfig] = _configurations([item.id for item in candidates])
    if keep_configs is not None:
        configs = configs[:keep_configs]
    return RunPlan(
        run_id="run",
        project=ProjectProfile(root="/project", name="project", content_hash="hash"),
        candidates=candidates,
        prompts=prompts,
        configurations=configs,
        model="claude-haiku-4-5-20251001",
        seed=1,
        max_sessions=1000,
        max_cost_usd=5,
        estimated_max_cost_usd=1,
        planned_sessions=len(prompts) * len(configs),
    )


def _results(plan: RunPlan) -> list[SessionResult]:
    """A perfect router: it loads a skill exactly when that skill is available and expected."""
    return [
        SessionResult(
            session_key=f"{config.id}::{prompt.id}",
            config_id=config.id,
            prompt_id=prompt.id,
            prompt_origin=prompt.origin,
            expected_skill=prompt.expected_skill,
            available_skills=config.skill_ids,
            selected_skills=(
                [prompt.expected_skill] if prompt.expected_skill in config.skill_ids else []
            ),
            outcome="completed",
        )
        for config in plan.configurations
        for prompt in plan.prompts
    ]


@pytest.mark.parametrize(
    ("skill_count", "surviving_kinds"),
    [
        # The planner drops duplicate skill sets, so with few skills every "without-N"
        # config collapses into a singleton, pair or greedy config of the same skills.
        (2, set()),
        (3, set()),
        (4, {"leave-one-out"}),
    ],
)
def test_leave_one_out_delta_is_measured_however_the_planner_deduplicated_it(
    skill_count: int, surviving_kinds: set[str]
) -> None:
    plan = _plan(skill_count)
    assert {item.kind for item in plan.configurations} & {"leave-one-out"} == surviving_kinds

    recommendation = analyze(plan, _results(plan))

    # Dropping any one skill loses exactly one of the skill_count + 1 prompts.
    expected = round(1 / (skill_count + 1), 4)
    assert recommendation.recommended_count == skill_count
    for verdict in recommendation.verdicts:
        assert verdict.leave_one_out_delta == expected
        assert verdict.verdict == "essential"


def test_single_skill_delta_is_measured_against_the_baseline() -> None:
    plan = _plan(1)
    recommendation = analyze(plan, _results(plan))
    # Baseline gets only the control right (1/2); the skill's own bundle gets both.
    assert recommendation.verdicts[0].leave_one_out_delta == 0.5


def test_unmeasured_leave_one_out_is_unknown_not_zero(tmp_path: Path) -> None:
    # Keep baseline, three singletons and the full bundle, like the planner does when a
    # session cap forces it to truncate its configuration list.
    plan = _plan(3, keep_configs=5)
    assert {item.kind for item in plan.configurations} == {"baseline", "singleton", "full"}

    recommendation = analyze(plan, _results(plan))

    assert recommendation.recommended_count == 3
    for verdict in recommendation.verdicts:
        assert verdict.leave_one_out_delta is None
        assert verdict.verdict == "unverified"
        assert "not measured" in verdict.reasons[0]

    report = markdown_report(plan, recommendation)
    assert "| n/a |" in report
    assert "+0.0%" not in report

    write_reports(tmp_path, plan, recommendation)
    assert "n/a" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "n/a" in (tmp_path / "report.html").read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "recommendation.json").read_text(encoding="utf-8"))
    assert [item["leave_one_out_delta"] for item in saved["verdicts"]] == [None, None, None]
