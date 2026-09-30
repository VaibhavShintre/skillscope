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

    # Dropping any one skill loses one of the skill_count positive prompts: the positive score
    # falls by 1 / skill_count, and the balanced score by half of that.
    expected = round(1 / (2 * skill_count), 4)
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
    # Balanced score: singleton-01 gets its own positive right (1 of 2) and no negative, so
    # (0.5 + 0) / 2; full gets nothing right.
    assert noisy.best_score == 0.25


def test_no_skills_is_recommended_when_the_baseline_beats_every_bundle(tmp_path: Path) -> None:
    negatives = [
        EvalPrompt(id=f"neg-{index}", text="x", origin="project", label="negative")
        for index in range(2)
    ]
    plan = _plan(2, extra_prompts=negatives)
    # An over-eager model: the baseline is right on every negative (balanced 50%), every bundle
    # scores at most 25%.
    recommendation = analyze_exact(plan, [_fire_everything(item) for item in _results(plan)])

    assert recommendation.no_skills_recommended is True
    assert recommendation.recommended_config == "baseline"
    assert recommendation.recommended_skill_ids == []
    assert recommendation.recommended_count == 0
    assert recommendation.baseline_score == 0.5
    assert recommendation.best_score == 0.25
    assert recommendation.recommended_score == 0.5
    assert not any(item.selected for item in recommendation.verdicts)
    # Every skill fires on everything, so dropping any one from the full bundle helps it.
    assert {item.verdict for item in recommendation.verdicts} == {"harmful"}

    report = markdown_report(plan, recommendation)
    assert "**Recommendation: use no skills.**" in report
    assert "No-skill baseline score: **50.0%** (positive 0.0%, negative 100.0%)" in report
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
    assert recommendation.baseline_score == 0.5  # silent: no positive, every negative


def test_report_warns_how_few_prompts_back_each_score() -> None:
    plan = _plan(2)  # two skill prompts (positive) plus one control (negative) per configuration
    recommendation = analyze_exact(plan, _results(plan))
    assert recommendation.scored_prompts_per_config == 3
    assert recommendation.positive_prompts_per_config == 2
    assert recommendation.negative_prompts_per_config == 1
    assert any(
        "2 positive and 1 negative labeled prompts" in item
        and "25.0 points" in item
        and "50.0" in item
        and "treated as a tie" in item
        for item in recommendation.limitations
    )
    assert "2 positive and 1 negative labeled prompts" in markdown_report(plan, recommendation)


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


def _grid(positives: int, negatives: int, expected: str = "skill-1", skills: int = 1) -> RunPlan:
    """`positives` prompts that expect one skill, and `negatives` controls that expect none."""
    prompts = [
        EvalPrompt(id=f"want-{index}", text="x", expected_skill=expected, origin="user")
        for index in range(positives)
    ] + [EvalPrompt(id=f"quiet-{index}", text="x", origin="control") for index in range(negatives)]
    return _plan(skills).model_copy(update={"prompts": prompts})


def _lead(hits: int, size: int) -> tuple[RunPlan, list[SessionResult]]:
    """One skill that answers only its first `hits` of `size` positives and never fires wrongly."""
    plan = _grid(size, size)
    results = [
        item.model_copy(update={"selected_skills": []})
        if item.prompt_id.startswith("want-") and int(item.prompt_id.split("-")[1]) >= hits
        else item
        for item in _results(plan)
    ]
    return plan, results


def test_a_lead_equal_to_the_noise_floor_is_a_tie_not_a_win(tmp_path: Path) -> None:
    plan, results = _lead(hits=1, size=5)  # positive score 1/5 -> balanced 0.6 against 0.5
    recommendation = analyze(plan, results)

    assert recommendation.noise_step == 0.1
    assert recommendation.baseline_score == 0.5 and recommendation.best_score == 0.6
    assert recommendation.effective_tolerance == 0.1  # exactly the lead: 0.6 - 0.5 is a tie
    assert recommendation.no_skills_recommended is True
    assert recommendation.inconclusive is True
    assert recommendation.recommended_count == 0

    report = markdown_report(plan, recommendation)
    assert "**Inconclusive — gap within noise.**" in report
    assert "a gap of 10.0 points" in report and "one prompt = up to 10.0 points" in report
    assert "Recommended bundle: **No skills (inconclusive — gap within noise)**" in report
    write_reports(tmp_path, plan, recommendation)
    saved = json.loads((tmp_path / "recommendation.json").read_text(encoding="utf-8"))
    assert saved["inconclusive"] is True and saved["noise_step"] == 0.1
    assert "Inconclusive" in (tmp_path / "report.html").read_text(encoding="utf-8")

    # With the noise floor switched off the same run would have recommended the skill.
    assert analyze(plan, results, noise_prompts=0).recommended_count == 1


@pytest.mark.parametrize("size", range(2, 26))
def test_a_one_prompt_lead_is_a_tie_at_every_class_size(size: int) -> None:
    # The lead 1 / (2 * size) equals the floor 0.5 / size in exact arithmetic, but not always
    # in floating point; the comparison must not depend on how it rounds.
    plan, results = _lead(hits=1, size=size)
    tie = analyze(plan, results)
    assert tie.inconclusive is True and tie.recommended_count == 0

    plan, results = _lead(hits=2, size=size)
    win = analyze(plan, results)
    assert win.inconclusive is False and win.recommended_skill_ids == ["skill-1"]


def test_a_two_prompt_lead_is_a_real_gap() -> None:
    plan, results = _lead(hits=2, size=5)  # 0.7 against 0.5
    recommendation = analyze(plan, results)
    assert recommendation.recommended_skill_ids == ["skill-1"]
    assert recommendation.no_skills_recommended is False and recommendation.inconclusive is False
    report = markdown_report(plan, recommendation)
    assert "Recommended bundle: **skill-1 (auto-discovered)**" in report
    assert "Recommended score: **70.0%** (positive 40.0%, negative 100.0%)" in report
    assert "No-skill baseline score: **50.0%** (positive 0.0%, negative 100.0%)" in report
    assert "| singleton-01 | skill-1 | 40.0% | 100.0% | 70.0% | 70.0% |" in report


def test_the_recommended_bundle_must_itself_beat_no_skills_not_just_the_best_one() -> None:
    # Three positives expect skill-1 and three expect skill-2, with six controls.
    prompts = [
        EvalPrompt(id=f"want-a-{index}", text="x", expected_skill="skill-1", origin="user")
        for index in range(3)
    ] + [
        EvalPrompt(id=f"want-b-{index}", text="x", expected_skill="skill-2", origin="user")
        for index in range(3)
    ] + [EvalPrompt(id=f"quiet-{index}", text="x", origin="control") for index in range(6)]
    plan = _plan(2).model_copy(update={"prompts": prompts})
    answered = {
        frozenset({"skill-1"}): {"want-a-0"},
        frozenset({"skill-1", "skill-2"}): {"want-a-0", "want-b-0"},
        frozenset({"skill-2"}): set(),
    }
    results = [
        item.model_copy(update={"selected_skills": []})
        if item.prompt_id.startswith("want-")
        and item.prompt_id not in answered.get(frozenset(item.available_skills), set())
        else item
        for item in _results(plan)
    ]
    recommendation = analyze(plan, results)
    by_config = {item.id: item for item in recommendation.config_scores}

    # skill-1 alone is within one prompt of the best bundle, and ahead of no skills by exactly
    # one prompt, which is a tie. The smallest near-best bundle would have been recommended
    # on that; the bundle that really beats no skills is the pair.
    assert by_config["baseline"].score == 0.5
    assert by_config["singleton-01"].score == round(7 / 12, 4)
    assert by_config["full"].score == round(2 / 3, 4)
    assert recommendation.effective_tolerance == round(1 / 12, 4)
    assert recommendation.recommended_skill_ids == ["skill-1", "skill-2"]
    assert recommendation.recommended_score - recommendation.baseline_score > 1 / 12


def test_a_clear_baseline_win_is_not_inconclusive() -> None:
    # Six positives expect a skill that is not in the run; skills fire on everything anyway.
    plan = _grid(6, 6, expected="skill-9", skills=2)
    recommendation = analyze(plan, [_fire_everything(item) for item in _results(plan)])
    assert recommendation.no_skills_recommended is True
    assert recommendation.inconclusive is False
    report = markdown_report(plan, recommendation)
    assert "**Recommendation: use no skills.**" in report
    assert "noise tolerance 8.3%" in report
    assert "Inconclusive" not in report


def test_staying_silent_scores_fifty_percent_however_many_negatives_there_are() -> None:
    plan = _grid(2, 30)
    silent = [item.model_copy(update={"selected_skills": []}) for item in _results(plan)]
    recommendation = analyze_exact(plan, silent)
    baseline = next(item for item in recommendation.config_scores if item.id == "baseline")

    assert (baseline.positive_score, baseline.negative_score) == (0.0, 1.0)
    assert baseline.score == 0.5  # not the 94% of prompts a plain accuracy would credit
    assert baseline.accuracy == round(30 / 32, 4)
    assert recommendation.inconclusive is True


def test_a_skill_that_hurts_the_full_bundle_is_harmful_not_redundant() -> None:
    # Six positives expect skill-1, six controls should stay silent. Skill-3 fires wrongly on
    # three controls, but only when all three skills are listed: the full bundle suffers, and
    # "full minus skill-3" (skills 1 and 2) is perfect.
    plan = _grid(6, 6, skills=3)
    results = _results_where(
        plan, {frozenset({"skill-1", "skill-2", "skill-3"}): ["quiet-0", "quiet-1", "quiet-2"]}
    )
    recommendation = analyze(plan, results)

    assert recommendation.recommended_skill_ids == ["skill-1"]
    third = next(item for item in recommendation.verdicts if item.skill_id == "skill-3")
    assert third.leave_one_out_delta == -0.25  # the full bundle improves by 25 points
    assert third.verdict == "harmful"
    assert "raised the score by 25.0%" in third.reasons[0]
    assert any("Leave-one-out deltas are measured against the full bundle" in x
               for x in recommendation.limitations)


def test_a_recommended_skill_that_hurts_the_full_bundle_is_contested_not_useful() -> None:
    # Two positives expect skill-1 and four expect skill-2, with six controls. Skill-1 fires
    # wrongly on four controls only in the full bundle, so "full minus skill-1" beats the full
    # bundle even though skill-1 is needed in the recommended pair.
    prompts = [
        EvalPrompt(id=f"want-a-{index}", text="x", expected_skill="skill-1", origin="user")
        for index in range(2)
    ] + [
        EvalPrompt(id=f"want-b-{index}", text="x", expected_skill="skill-2", origin="user")
        for index in range(4)
    ] + [EvalPrompt(id=f"quiet-{index}", text="x", origin="control") for index in range(6)]
    plan = _plan(3).model_copy(update={"prompts": prompts})
    full = frozenset({"skill-1", "skill-2", "skill-3"})
    noisy = {f"quiet-{index}" for index in range(4)}
    results = [
        item.model_copy(update={"selected_skills": ["skill-1"]})
        if frozenset(item.available_skills) == full and item.prompt_id in noisy
        else item
        for item in _results(plan)
    ]
    recommendation = analyze(plan, results)

    assert recommendation.recommended_skill_ids == ["skill-1", "skill-2"]
    first = next(item for item in recommendation.verdicts if item.skill_id == "skill-1")
    assert first.leave_one_out_delta < -0.1
    assert first.verdict == "contested"
    assert "In the recommended bundle" in first.reasons[0]
    # Against the recommended bundle itself, skill-1 clearly earns its place.
    assert first.bundle_delta is not None and first.bundle_delta > 0.15


def test_delta_in_the_recommended_bundle_compares_it_with_the_bundle_without_the_skill() -> None:
    plan, results = _lead(hits=3, size=5)  # one skill; the recommended bundle is that skill
    recommendation = analyze(plan, results)
    only = recommendation.verdicts[0]
    # No skills is the bundle without it: the lead over the baseline, (0.6 + 1) / 2 - 0.5.
    assert only.bundle_delta == 0.3
    assert "| +30.0% |" in markdown_report(plan, recommendation)


def test_a_skill_the_full_bundle_does_not_need_is_redundant_with_an_accurate_reason() -> None:
    # Positives accept either skill; every configuration answers with the first one it has.
    prompts = [
        EvalPrompt(
            id=f"want-{index}", text="x", expected_skills=["skill-1", "skill-2"], origin="user"
        )
        for index in range(6)
    ] + [EvalPrompt(id=f"quiet-{index}", text="x", origin="control") for index in range(6)]
    plan = _plan(2).model_copy(update={"prompts": prompts})
    results = []
    for item in _results(plan):
        first = item.available_skills[:1]
        wanted = item.prompt_id.startswith("want-")
        results.append(item.model_copy(update={"selected_skills": first if wanted else []}))
    recommendation = analyze(plan, results)

    assert recommendation.recommended_skill_ids == ["skill-1"]
    second = next(item for item in recommendation.verdicts if item.skill_id == "skill-2")
    assert second.leave_one_out_delta == 0.0
    assert second.verdict == "redundant"
    assert "too small to count as an effect" in second.reasons[0]
