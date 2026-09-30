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


def _plan(
    skill_count: int,
    keep_configs: int | None = None,
    extra_prompts: list[EvalPrompt] | None = None,
) -> RunPlan:
    candidates = [_skill(index) for index in range(1, skill_count + 1)]
    prompts = [
        EvalPrompt(
            id=f"positive-{item.id}", text="x", expected_skill=item.id, origin="skill-positive"
        )
        for item in candidates
    ]
    prompts.append(EvalPrompt(id="control", text="x", origin="control"))
    prompts.extend(extra_prompts or [])
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


def _fire_everything(result: SessionResult) -> SessionResult:
    return result.model_copy(update={"selected_skills": list(result.available_skills)})


def test_unlabeled_prompts_are_excluded_never_counted_as_correct(tmp_path: Path) -> None:
    mystery = EvalPrompt(id="mystery", text="x", origin="project", capability="accessibility")
    plan = _plan(2, extra_prompts=[mystery])
    quiet = _results(plan)
    loud = [_fire_everything(item) if item.prompt_id == "mystery" else item for item in quiet]

    first = analyze(plan, quiet)
    second = analyze(plan, loud)

    # What the model does on an unlabeled prompt cannot move any score or precision.
    assert first.best_score == second.best_score == 1.0
    assert [item.precision for item in first.verdicts] == [1.0, 1.0]
    assert first.verdicts == second.verdicts
    # It is counted and reported instead.
    assert first.labeling.unlabeled_prompts == 1
    assert first.labeling.unlabeled_prompt_ids == ["mystery"]
    assert first.labeling.unlabeled_sessions == len(plan.configurations)
    report = markdown_report(plan, first)
    assert "1 unlabeled" in report
    assert "- mystery" in report
    assert any("unlabeled and excluded" in item for item in first.limitations)


def test_nothing_labeled_means_no_score_and_no_recommendation(tmp_path: Path) -> None:
    unlabeled = [
        EvalPrompt(id=f"u{index}", text="x", origin="project", capability="accessibility")
        for index in range(2)
    ]
    plan = _plan(2)
    plan = plan.model_copy(update={"prompts": unlabeled})
    recommendation = analyze(plan, _results(plan))

    assert recommendation.best_score is None
    assert recommendation.recommended_score is None
    assert recommendation.recommended_count == 0
    assert all(item.verdict == "unverified" for item in recommendation.verdicts)
    assert all(item.precision is None and item.recall is None for item in recommendation.verdicts)
    report = markdown_report(plan, recommendation)
    assert "Best observed score: **n/a**" in report
    write_reports(tmp_path, plan, recommendation)
    saved = json.loads((tmp_path / "recommendation.json").read_text(encoding="utf-8"))
    assert saved["best_score"] is None
    assert saved["verdicts"][0]["precision"] is None


def test_precision_is_per_configuration_and_counts_wrong_and_negative_firings() -> None:
    negatives = [
        EvalPrompt(id=f"neg-{index}", text="x", origin="project", label="negative")
        for index in range(2)
    ]
    plan = _plan(2, extra_prompts=negatives)
    ideal = analyze(plan, _results(plan))
    assert ideal.best_score == 1.0
    assert [item.precision for item in ideal.verdicts] == [1.0, 1.0]

    # 5 labeled prompts (2 positive, control, 2 negative); the skill fires on all of them.
    noisy = analyze(plan, [_fire_everything(item) for item in _results(plan)])
    first = noisy.verdicts[0]
    assert set(first.precision_by_config) == {"singleton-01", "full"}
    assert first.precision_by_config == {"singleton-01": 0.2, "full": 0.2}
    assert first.precision == 0.2  # the full-bundle figure is the headline one
    # Score: singleton-01 gets only its own positive right (1/5); full gets none (0/5).
    assert noisy.best_score == 0.2


def test_no_skills_is_recommended_when_the_baseline_beats_every_bundle(tmp_path: Path) -> None:
    negatives = [
        EvalPrompt(id=f"neg-{index}", text="x", origin="project", label="negative")
        for index in range(2)
    ]
    plan = _plan(2, extra_prompts=negatives)
    # An over-eager model: baseline gets 3/5 (silent on negatives), every bundle scores <= 1/5.
    recommendation = analyze(plan, [_fire_everything(item) for item in _results(plan)])

    assert recommendation.no_skills_recommended is True
    assert recommendation.recommended_config == "baseline"
    assert recommendation.recommended_skill_ids == []
    assert recommendation.recommended_count == 0
    assert recommendation.baseline_score == 0.6
    assert recommendation.best_score == 0.2
    assert recommendation.recommended_score == 0.6
    assert not any(item.selected for item in recommendation.verdicts)
    assert all(item.verdict == "conflicting" for item in recommendation.verdicts)

    report = markdown_report(plan, recommendation)
    assert "**Recommendation: use no skills.**" in report
    assert "No-skill baseline score: **60.0%**" in report
    assert "Recommended bundle: **No skills**" in report
    write_reports(tmp_path, plan, recommendation)
    assert "use no skills" in (tmp_path / "report.html").read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "recommendation.json").read_text(encoding="utf-8"))
    assert saved["no_skills_recommended"] is True


def test_skills_that_never_fire_do_not_earn_a_recommendation() -> None:
    plan = _plan(2)
    silent = [item.model_copy(update={"selected_skills": []}) for item in _results(plan)]
    recommendation = analyze(plan, silent)

    # Every configuration scores exactly like the baseline, and the smaller bundle wins ties.
    assert recommendation.no_skills_recommended is True
    assert recommendation.recommended_count == 0
    assert {item.verdict for item in recommendation.verdicts} == {"irrelevant"}


def test_a_bundle_that_beats_the_baseline_is_still_recommended() -> None:
    plan = _plan(2)
    recommendation = analyze(plan, _results(plan))
    assert recommendation.no_skills_recommended is False
    assert recommendation.recommended_count == 2
    assert recommendation.baseline_score == round(1 / 3, 4)


def test_report_warns_how_few_prompts_back_each_score() -> None:
    plan = _plan(2)  # two skill prompts plus one control: 3 labeled prompts per configuration
    recommendation = analyze(plan, _results(plan))
    assert recommendation.scored_prompts_per_config == 3
    assert any(
        "only 3 labeled prompts (1 prompt = 33.3 points)" in item
        and "not statistically significant" in item
        for item in recommendation.limitations
    )
    assert "1 prompt = 33.3 points" in markdown_report(plan, recommendation)
