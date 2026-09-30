import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from skills_evaluator.analysis import analyze
from skills_evaluator.cli import _print_plan, app
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


def analyze_exact(plan, results):
    """The analysis with no minimum tolerance, for tests with only a handful of prompts."""
    return analyze(plan, results, noise_prompts=0)


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
    configs: list[ExperimentConfig] = _configurations([item.id for item in candidates])[0]
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
        # The planner plans the leave-one-outs right after the full bundle and drops duplicate
        # skill sets, so with two skills they collapse into the singletons already planned.
        (2, set()),
        (3, {"leave-one-out"}),
        (4, {"leave-one-out"}),
    ],
)
def test_leave_one_out_delta_is_measured_however_the_planner_deduplicated_it(
    skill_count: int, surviving_kinds: set[str]
) -> None:
    plan = _plan(skill_count)
    assert {item.kind for item in plan.configurations} & {"leave-one-out"} == surviving_kinds

    recommendation = analyze_exact(plan, _results(plan))

    # Dropping any one skill loses exactly one of the skill_count + 1 prompts.
    expected = round(1 / (skill_count + 1), 4)
    assert recommendation.recommended_count == skill_count
    for verdict in recommendation.verdicts:
        assert verdict.leave_one_out_delta == expected
        assert verdict.verdict == "essential"


def test_single_skill_delta_is_measured_against_the_baseline() -> None:
    plan = _plan(1)
    recommendation = analyze_exact(plan, _results(plan))
    # Baseline gets only the control right (1/2); the skill's own bundle gets both.
    assert recommendation.verdicts[0].leave_one_out_delta == 0.5


def test_unmeasured_leave_one_out_is_unknown_not_zero(tmp_path: Path) -> None:
    # Keep baseline, three singletons and the full bundle, like the planner does when a
    # session cap forces it to truncate its configuration list.
    plan = _plan(3, keep_configs=5)
    assert {item.kind for item in plan.configurations} == {"baseline", "singleton", "full"}

    recommendation = analyze_exact(plan, _results(plan))

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

    first = analyze_exact(plan, quiet)
    second = analyze_exact(plan, loud)

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
    recommendation = analyze_exact(plan, _results(plan))

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
    ideal = analyze_exact(plan, _results(plan))
    assert ideal.best_score == 1.0
    assert [item.precision for item in ideal.verdicts] == [1.0, 1.0]

    # 5 labeled prompts (2 positive, control, 2 negative); the skill fires on all of them.
    noisy = analyze_exact(plan, [_fire_everything(item) for item in _results(plan)])
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
    recommendation = analyze_exact(plan, [_fire_everything(item) for item in _results(plan)])

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
    recommendation = analyze_exact(plan, silent)

    # Every configuration scores exactly like the baseline, and the smaller bundle wins ties.
    assert recommendation.no_skills_recommended is True
    assert recommendation.recommended_count == 0
    assert {item.verdict for item in recommendation.verdicts} == {"irrelevant"}


def test_a_bundle_that_beats_the_baseline_is_still_recommended() -> None:
    plan = _plan(2)
    recommendation = analyze_exact(plan, _results(plan))
    assert recommendation.no_skills_recommended is False
    assert recommendation.recommended_count == 2
    assert recommendation.baseline_score == round(1 / 3, 4)


def test_report_warns_how_few_prompts_back_each_score() -> None:
    plan = _plan(2)  # two skill prompts plus one control: 3 labeled prompts per configuration
    recommendation = analyze_exact(plan, _results(plan))
    assert recommendation.scored_prompts_per_config == 3
    assert any(
        "only 3 labeled prompts (1 prompt = 33.3 points)" in item
        and "not statistically significant" in item
        for item in recommendation.limitations
    )
    assert "1 prompt = 33.3 points" in markdown_report(plan, recommendation)


def _with_categories(plan: RunPlan, categories: dict[int, list[str]]) -> RunPlan:
    candidates = [
        item.model_copy(update={"categories": categories.get(index, [])})
        for index, item in enumerate(plan.candidates, start=1)
    ]
    return plan.model_copy(update={"candidates": candidates})


def test_uncategorized_skill_activations_are_ignored_on_capability_prompts() -> None:
    # skill-1 is a frontend skill; skill-2 declares no category.
    heuristic = [
        EvalPrompt(id="ui", text="x", origin="project", capability="frontend"),
        EvalPrompt(id="db", text="x", origin="project", capability="database"),
    ]
    plan = _with_categories(_plan(2, extra_prompts=heuristic), {1: ["frontend"]})

    def results(skill_2_fires_on_db: bool) -> list[SessionResult]:
        out = []
        for item in _results(plan):
            fired = item.selected_skills
            if item.prompt_id == "ui" and "skill-1" in item.available_skills:
                fired = ["skill-1"]  # correct: the frontend skill handles the UI prompt
            if item.prompt_id == "db" and skill_2_fires_on_db:
                fired = ["skill-2"] if "skill-2" in item.available_skills else fired
            out.append(item.model_copy(update={"selected_skills": fired}))
        return out

    quiet = analyze_exact(plan, results(False))
    loud = analyze_exact(plan, results(True))

    # Every prompt is labeled: the missing category did not blank out the project prompts.
    assert quiet.labeling.unlabeled_prompts == 0
    assert quiet.labeling.positive_prompts == 3 and quiet.labeling.negative_prompts == 2
    # What skill-2 does on a capability prompt is neither right nor wrong.
    assert quiet.best_score == loud.best_score == 1.0
    assert [item.precision for item in quiet.verdicts] == [1.0, 1.0]
    assert loud.verdicts == quiet.verdicts
    # ...but it is counted: skill-2 is available in singleton-02 and full.
    assert quiet.labeling.unjudged_activations == 0
    assert loud.labeling.unjudged_activations == 2
    assert any("not judged on capability-derived prompts" in item for item in loud.limitations)

    # The categorized skill is still judged: firing on the negative database prompt is wrong.
    wrong = [
        item.model_copy(update={"selected_skills": ["skill-1"]})
        if item.prompt_id == "db" and "skill-1" in item.available_skills
        else item
        for item in results(False)
    ]
    penalized = analyze_exact(plan, wrong)
    assert penalized.best_score is not None and penalized.best_score < 1.0
    assert penalized.verdicts[0].precision is not None and penalized.verdicts[0].precision < 1.0


def _mostly_unlabeled_plan(unlabeled: int) -> RunPlan:
    prompts = [
        EvalPrompt(id=f"u{index}", text="x", origin="project", capability="accessibility")
        for index in range(unlabeled)
    ]
    return _plan(2, extra_prompts=prompts)  # 3 labeled prompts plus the unlabeled ones


def test_more_than_half_unlabeled_means_insufficient_evidence(tmp_path: Path) -> None:
    plan = _mostly_unlabeled_plan(4)  # 4 of 7 prompts unlabeled
    recommendation = analyze_exact(plan, _results(plan))

    assert recommendation.insufficient_evidence is True
    assert recommendation.no_skills_recommended is False
    assert recommendation.recommended_config == ""
    assert recommendation.recommended_skill_ids == []
    assert recommendation.recommended_count == 0
    # The labeled minority scores perfectly, and that is exactly what must not be reported.
    assert recommendation.best_score is None
    assert recommendation.recommended_score is None
    assert recommendation.baseline_score is None
    assert all(item.verdict == "unverified" for item in recommendation.verdicts)
    for item in recommendation.verdicts:
        assert item.precision is None and item.recall is None
        assert item.singleton_score is None and item.leave_one_out_delta is None
        assert item.precision_by_config == {}
    assert recommendation.limitations[0].startswith("Insufficient labeled evidence: 4 of 7")

    report = markdown_report(plan, recommendation)
    assert "**Insufficient labeled evidence: no recommendation is issued.**" in report
    assert "Recommended bundle: **none (insufficient labeled evidence)**" in report
    assert "100.0%" not in report
    write_reports(tmp_path, plan, recommendation)
    assert "Insufficient labeled evidence" in (tmp_path / "report.html").read_text(encoding="utf-8")
    saved = json.loads((tmp_path / "recommendation.json").read_text(encoding="utf-8"))
    assert saved["insufficient_evidence"] is True
    assert saved["best_score"] is None

    # The installer refuses to act on it.
    (tmp_path / "run-manifest.json").write_text(plan.model_dump_json(), encoding="utf-8")
    result = CliRunner().invoke(app, ["install", str(tmp_path), "--project", str(tmp_path)])
    assert result.exit_code != 0
    assert "insufficient" in result.output.lower()


def test_exactly_half_unlabeled_is_still_enough_evidence() -> None:
    plan = _mostly_unlabeled_plan(3)  # 3 of 6 prompts unlabeled: not more than half
    recommendation = analyze_exact(plan, _results(plan))
    assert recommendation.insufficient_evidence is False
    assert recommendation.recommended_count == 2
    assert recommendation.best_score == 1.0


def test_report_marks_auto_discovered_skills() -> None:
    plan = _plan(2)
    first, second = plan.candidates
    plan = plan.model_copy(
        update={
            "candidates": [
                first.model_copy(update={"user_requested": True}),
                second.model_copy(update={"user_requested": False}),
            ]
        }
    )
    recommendation = analyze_exact(plan, _results(plan))
    assert [item.auto_discovered for item in recommendation.verdicts] == [False, True]

    report = markdown_report(plan, recommendation)
    assert "| skill-1 | requested |" in report
    assert "| skill-2 | auto-discovered |" in report
    assert "Recommended bundle: **skill-1, skill-2 (auto-discovered)**" in report
    assert "Pass `--offline`" in report

    requested = [item.model_copy(update={"user_requested": True}) for item in plan.candidates]
    everything_requested = plan.model_copy(update={"candidates": requested})
    clean_recommendation = analyze_exact(everything_requested, _results(plan))
    clean = markdown_report(everything_requested, clean_recommendation)
    assert "auto-discovered" not in clean and "--offline" not in clean


def test_plan_output_marks_auto_discovered_skills(capsys, tmp_path: Path) -> None:
    plan = _plan(2)
    plan = plan.model_copy(
        update={
            "candidates": [
                plan.candidates[0].model_copy(update={"user_requested": True}),
                plan.candidates[1].model_copy(update={"user_requested": False}),
            ]
        }
    )
    _print_plan(plan, tmp_path)
    lines = capsys.readouterr().out.splitlines()
    assert any("skill-1" in line and "auto-discovered" not in line for line in lines)
    assert any("skill-2" in line and "auto-discovered" in line for line in lines)


def _padded(skill_count: int, padding: int) -> RunPlan:
    controls = [
        EvalPrompt(id=f"pad-{index}", text="x", origin="control") for index in range(padding)
    ]
    return _plan(skill_count, extra_prompts=controls)


def test_a_one_prompt_lead_over_no_skills_is_inconclusive(tmp_path: Path) -> None:
    plan = _padded(1, 10)  # 12 prompts per config; the skill wins exactly one (its own positive)
    recommendation = analyze(plan, _results(plan))

    assert recommendation.scored_prompts_per_config == 12
    assert recommendation.effective_tolerance == round(1 / 12, 4)
    assert recommendation.baseline_score == round(11 / 12, 4)
    assert recommendation.best_score == 1.0
    assert recommendation.no_skills_recommended is True
    assert recommendation.inconclusive is True
    assert recommendation.recommended_count == 0
    assert recommendation.verdicts[0].verdict == "no_lift"
    assert "beyond the noise floor" in recommendation.verdicts[0].reasons[0]

    report = markdown_report(plan, recommendation)
    assert "**Inconclusive — gap within noise.**" in report
    assert "a gap of 8.3 points" in report
    assert "one prompt = 8.3 points" in report
    assert "Recommended bundle: **No skills (inconclusive — gap within noise)**" in report
    assert "Noise tolerance: **8.3%**" in report
    write_reports(tmp_path, plan, recommendation)
    saved = json.loads((tmp_path / "recommendation.json").read_text(encoding="utf-8"))
    assert saved["inconclusive"] is True and saved["effective_tolerance"] == 0.0833
    assert "Inconclusive" in (tmp_path / "report.html").read_text(encoding="utf-8")

    # The same run with the floor switched off would have recommended the skill.
    assert analyze(plan, _results(plan), noise_prompts=0).recommended_count == 1


def test_a_two_prompt_lead_is_a_real_gap() -> None:
    plan = _padded(2, 10)  # 13 prompts per config; the baseline misses both positives
    recommendation = analyze(plan, _results(plan))
    assert recommendation.baseline_score == round(11 / 13, 4)
    assert recommendation.no_skills_recommended is False
    assert recommendation.inconclusive is False
    # Singletons trail the full bundle by one prompt, which is a tie, so the smaller bundle wins.
    assert recommendation.recommended_count == 1
    assert "Inconclusive" not in markdown_report(plan, recommendation)


def test_baseline_far_ahead_is_a_clear_no_skills_not_inconclusive() -> None:
    negatives = [
        EvalPrompt(id=f"neg-{index}", text="x", origin="project", label="negative")
        for index in range(10)
    ]
    plan = _plan(2, extra_prompts=negatives)
    # Every skill fires on everything: bundles fail all 10 negatives, the baseline passes them.
    recommendation = analyze(plan, [_fire_everything(item) for item in _results(plan)])
    assert recommendation.no_skills_recommended is True
    assert recommendation.inconclusive is False
    report = markdown_report(plan, recommendation)
    assert "**Recommendation: use no skills.**" in report
    assert "noise tolerance 7.7%" in report  # one of 13 prompts, not the configured 2%
    assert "Inconclusive" not in report


def test_report_compares_expected_and_actual_cost() -> None:
    plan = _plan(2)
    results = [item.model_copy(update={"cost_usd": 0.001}) for item in _results(plan)]
    recommendation = analyze_exact(plan, results)
    assert recommendation.total_cost_usd == round(0.001 * len(results), 6)
    assert 0 < recommendation.expected_cost_usd < recommendation.worst_case_cost_usd
    report = markdown_report(plan, recommendation)
    assert f"actual ${recommendation.total_cost_usd:.4f}" in report
    assert f"expected ${recommendation.expected_cost_usd:.4f}" in report
    assert f"worst-case bound ${recommendation.worst_case_cost_usd:.4f}" in report
    assert "of expected and" in report and "of the bound" in report


def _results_where(plan: RunPlan, wrong: dict[frozenset[str], list[str]]) -> list[SessionResult]:
    """An ideal router, except in the named configurations (by skill set), where a skill fires
    on the listed negative prompts. Lets a test dictate each configuration's score."""
    out = []
    for item in _results(plan):
        fired = item.selected_skills
        noise = wrong.get(frozenset(item.available_skills), [])
        if item.prompt_id in noise:
            fired = [item.available_skills[-1]]
        out.append(item.model_copy(update={"selected_skills": fired}))
    return out


def test_a_skill_outside_the_bundle_with_a_large_leave_one_out_is_contested_not_redundant() -> None:
    # Six prompts expect skill-1 and six controls should stay silent; nothing expects 2 or 3.
    prompts = [
        EvalPrompt(id=f"want-{index}", text="x", expected_skill="skill-1", origin="user")
        for index in range(6)
    ] + [EvalPrompt(id=f"quiet-{index}", text="x", origin="control") for index in range(6)]
    plan = _plan(3).model_copy(update={"prompts": prompts})
    # Skill-2 fires on three controls, but only when skills 1 and 2 are listed together. So the
    # full bundle and skill-1 alone are perfect, while "full minus skill-3" is not.
    results = _results_where(
        plan, {frozenset({"skill-1", "skill-2"}): ["quiet-0", "quiet-1", "quiet-2"]}
    )
    recommendation = analyze(plan, results)

    # The smallest bundle within the noise floor of the best leaves skill-3 out...
    assert recommendation.recommended_skill_ids == ["skill-1"]
    third = next(item for item in recommendation.verdicts if item.skill_id == "skill-3")
    # ...yet removing skill-3 from the full bundle costs 3 of 12 prompts.
    assert third.leave_one_out_delta == 0.25
    assert third.verdict == "contested"
    assert "evidence conflicts" in third.reasons[0]
    # Skill-2's removal changes nothing and nothing expects it, so it is not contested.
    second = next(item for item in recommendation.verdicts if item.skill_id == "skill-2")
    assert second.leave_one_out_delta == 0.0
    assert second.verdict == "unverified"


def test_redundant_requires_a_measured_leave_one_out() -> None:
    plan = _plan(3, keep_configs=5)  # baseline, 3 singletons, full: no leave-one-out planned
    recommendation = analyze(plan, _results(plan))
    assert {item.leave_one_out_delta for item in recommendation.verdicts} == {None}
    assert "redundant" not in {item.verdict for item in recommendation.verdicts}


def test_a_rarely_firing_skill_in_the_bundle_is_marginal_and_the_report_says_so() -> None:
    plan = _plan(
        2,
        extra_prompts=[
            EvalPrompt(id=f"more-{index}", text="x", expected_skill="skill-2", origin="user")
            for index in range(3)
        ],
    )
    # Skill-2 fires on its first expected prompt only; skill-1 over-fires on the control when it
    # is alone, so the bundle with the quiet skill-2 scores best.
    results = []
    for item in _results_where(plan, {frozenset({"skill-1"}): ["control"]}):
        if item.prompt_id.startswith("more-"):
            item = item.model_copy(update={"selected_skills": []})
        results.append(item)
    recommendation = analyze_exact(plan, results)

    assert recommendation.recommended_skill_ids == ["skill-1", "skill-2"]
    first, second = recommendation.verdicts
    assert first.verdict != "marginal"
    assert second.verdict == "marginal"
    assert second.recall is not None and second.recall < 0.5
    assert "fired on only" in second.reasons[0]
    assert any("Marginal skills in the recommended bundle (skill-2)" in x
               for x in recommendation.limitations)
