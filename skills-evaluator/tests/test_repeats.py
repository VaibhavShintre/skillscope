import json
from pathlib import Path

import pytest

from skills_evaluator import cli
from skills_evaluator.advice import build_advice
from skills_evaluator.analysis import analyze
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
from skills_evaluator.storage import (
    append_result,
    load_plan,
    load_results,
    session_key,
    session_order,
    session_schedule,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------------------ storage


def test_the_first_run_of_a_cell_keeps_the_plain_session_key() -> None:
    assert session_key("cfg", "prompt") == "cfg::prompt"
    assert session_key("cfg", "prompt", 0) == "cfg::prompt"
    assert session_key("cfg", "prompt", 2) == "cfg::prompt::r2"


def test_older_session_order_files_read_as_the_first_repeat(tmp_path: Path) -> None:
    (tmp_path / "session-order.json").write_text('[["a", "p"], ["b", "q"]]', encoding="utf-8")
    assert session_schedule(tmp_path) == [("a", "p", 0), ("b", "q", 0)]
    (tmp_path / "session-order.json").write_text(
        '[["a", "p", 0], ["a", "p", 1], ["b", "q", 0]]', encoding="utf-8"
    )
    assert session_schedule(tmp_path) == [("a", "p", 0), ("a", "p", 1), ("b", "q", 0)]
    assert session_order(tmp_path) == [("a", "p"), ("b", "q")]  # the first repeat only


def test_older_results_and_plans_load_with_one_repeat() -> None:
    result = SessionResult.model_validate_json(
        '{"session_key": "a::b", "config_id": "a", "prompt_id": "b", "expected_skill": null,'
        ' "available_skills": [], "outcome": "completed"}'
    )
    assert result.repeat == 0
    plan, _ = _fixture_plan_dir(None)
    stripped = plan.model_dump(mode="json")
    stripped.pop("repeats")
    assert RunPlan.model_validate(stripped).repeats == 1


# ------------------------------------------------------------------------------ planner


def _fixture_plan_dir(tmp_path: Path | None, **options):
    root = tmp_path or Path(__file__).parent / ".scratch-unused"
    if tmp_path is None:
        import tempfile

        root = Path(tempfile.mkdtemp())
    return build_plan(
        project=FIXTURES / "project",
        sources=[str(FIXTURES / "skills" / "testing"), str(FIXTURES / "skills" / "frontend")],
        run_root=root / "runs",
        include_anthropic=False,
        **options,
    )


def test_a_single_repeat_writes_the_older_session_order(tmp_path: Path) -> None:
    plan, run_dir = _fixture_plan_dir(tmp_path)
    raw = json.loads((run_dir / "session-order.json").read_text(encoding="utf-8"))
    assert plan.repeats == 1 and len(raw) == plan.planned_sessions
    assert all(len(item) == 2 for item in raw)


def test_repeats_schedule_whole_rounds_one_after_another(tmp_path: Path) -> None:
    single, _ = _fixture_plan_dir(tmp_path / "one")
    plan, run_dir = _fixture_plan_dir(tmp_path / "three", repeats=3)
    schedule = session_schedule(run_dir)
    cells = len(plan.configurations) * len(plan.prompts)

    assert plan.repeats == 3 and plan.planned_sessions == 3 * single.planned_sessions
    assert len(schedule) == plan.planned_sessions == 3 * cells
    for repeat in range(3):
        round_ = schedule[repeat * cells : (repeat + 1) * cells]
        assert {item[2] for item in round_} == {repeat}
        assert len({(config, prompt) for config, prompt, _ in round_}) == cells
    # The first round is the same shuffle a single-repeat run uses.
    assert [item[:2] for item in schedule[:cells]] == session_order(run_dir)[:cells]


def test_cost_estimates_and_the_cap_scale_with_the_number_of_repeats(tmp_path: Path) -> None:
    single, _ = _fixture_plan_dir(tmp_path / "one")
    triple, _ = _fixture_plan_dir(tmp_path / "three", repeats=3)
    assert triple.estimated_max_cost_usd == pytest.approx(
        3 * single.estimated_max_cost_usd, rel=1e-4
    )
    assert triple.estimated_expected_cost_usd == pytest.approx(
        3 * single.estimated_expected_cost_usd, rel=1e-4
    )

    # A cap that covers one repeat's worst case does not cover three.
    cap = single.estimated_max_cost_usd * 1.01
    _fixture_plan_dir(tmp_path / "fits", max_cost_usd=cap)
    with pytest.raises(ValueError, match="repeats"):
        _fixture_plan_dir(tmp_path / "over", max_cost_usd=cap, repeats=3)
    # So does the session cap.
    with pytest.raises(ValueError, match="repeats"):
        _fixture_plan_dir(tmp_path / "sessions", max_sessions=single.planned_sessions, repeats=2)


@pytest.mark.parametrize("repeats", [0, 10, -1])
def test_repeats_must_be_between_one_and_nine(tmp_path: Path, repeats: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 9"):
        _fixture_plan_dir(tmp_path, repeats=repeats)


# ------------------------------------------------------------------------------ execution


class FlakyEngine:
    """Answers differently on repeats: the ideal answer, except silence on the second repeat."""

    def run(self, session_key_, config_id, prompt, profile, skills) -> SessionResult:
        repeat = int(session_key_.rsplit("::r", 1)[1]) if "::r" in session_key_ else 0
        result = FakeEngine().run(session_key_, config_id, prompt, profile, skills)
        if repeat == 1:
            result = result.model_copy(update={"selected_skills": []})
        return result


def test_a_run_executes_every_repeat_and_resumes_by_repeat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, run_dir = _fixture_plan_dir(tmp_path, repeats=3)
    monkeypatch.setattr(cli, "FakeEngine", FlakyEngine)

    # Part of the first round is already done, as after an interrupted run.
    configs = {item.id: item for item in plan.configurations}
    prompts = {item.id: item for item in plan.prompts}
    candidates = {item.id: item for item in plan.candidates}
    for config_id, prompt_id, repeat in session_schedule(run_dir)[:5]:
        available = [candidates[item] for item in configs[config_id].skill_ids]
        key = session_key(config_id, prompt_id, repeat)
        result = FlakyEngine().run(key, config_id, prompts[prompt_id], plan.project, available)
        append_result(run_dir, result.model_copy(update={"repeat": repeat}))

    recommendation = cli._execute(run_dir, fake=True)
    results = load_results(run_dir)  # also proves that no session key was written twice

    assert len(results) == plan.planned_sessions
    assert {item.repeat for item in results} == {0, 1, 2}
    assert recommendation.repeats == 3
    # The second repeat is silent, so every bundle's score swings between repeats.
    assert recommendation.measured_spread > 0
    assert recommendation.effective_tolerance >= recommendation.measured_spread
    assert recommendation.unstable_cells > 0
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "## Most unstable prompts" in report and "3 repeats" in report


# ------------------------------------------------------------------------------ analysis


def _synthetic(fires, repeats: int, positives: int = 3, negatives: int = 3):
    """One skill and the baseline; `fires(prompt_id, repeat)` says when the skill answers."""
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
        for index in range(positives)
    ] + [EvalPrompt(id=f"quiet-{index}", text="x", origin="control") for index in range(negatives)]
    configs = [
        ExperimentConfig(id="baseline", skill_ids=[], kind="baseline"),
        ExperimentConfig(id="singleton-01", skill_ids=["skill-1"], kind="singleton"),
    ]
    plan = RunPlan(
        run_id="run",
        project=ProjectProfile(root="/p", name="p", content_hash="h"),
        candidates=[skill],
        prompts=prompts,
        configurations=configs,
        model="claude-haiku-4-5-20251001",
        seed=1,
        max_sessions=1000,
        max_cost_usd=5,
        estimated_max_cost_usd=1,
        planned_sessions=len(prompts) * len(configs) * repeats,
        repeats=repeats,
    )
    results = []
    for repeat in range(repeats):
        for config in configs:
            for prompt in prompts:
                fired = (
                    "skill-1" in config.skill_ids
                    and prompt.id.startswith("want-")
                    and fires(prompt.id, repeat)
                )
                results.append(
                    SessionResult(
                        session_key=session_key(config.id, prompt.id, repeat),
                        config_id=config.id,
                        prompt_id=prompt.id,
                        prompt_origin=prompt.origin,
                        expected_skill=prompt.expected_skill,
                        available_skills=config.skill_ids,
                        selected_skills=["skill-1"] if fired else [],
                        outcome="completed",
                        repeat=repeat,
                    )
                )
    return plan, results


def _hits(table: dict[str, set[int]]):
    return lambda prompt_id, repeat: repeat in table.get(prompt_id, set())


# want-0 answers on repeats 0 and 1, want-1 on repeat 0 only, want-2 on repeat 1 only.
SWINGING = {"want-0": {0, 1}, "want-1": {0}, "want-2": {1}}


def _score(recommendation, config_id: str):
    return next(item for item in recommendation.config_scores if item.id == config_id)


def test_the_score_is_pooled_over_repeats_and_shown_with_its_range() -> None:
    plan, results = _synthetic(_hits(SWINGING), repeats=3)
    recommendation = analyze(plan, results)
    only = _score(recommendation, "singleton-01")

    # 4 of 9 positive sessions were answered, and every negative session stayed silent.
    assert only.positive_score == round(4 / 9, 4) and only.negative_score == 1.0
    assert only.score == round((4 / 9 + 1) / 2, 4)
    # Single repeats scored 83.3%, 83.3% and 50.0%.
    assert (only.score_min, only.score_max) == (0.5, round(5 / 6, 4))
    assert recommendation.repeats == 3


def test_each_cell_is_right_by_majority_vote_and_unstable_when_repeats_disagree() -> None:
    plan, results = _synthetic(_hits(SWINGING), repeats=3)
    recommendation = analyze(plan, results)
    only = _score(recommendation, "singleton-01")

    # Only want-0 was right in most of its repeats (2 of 3), so it is the one cell that is
    # right by majority: (1/3 + 1) / 2.
    assert only.majority_score == round((1 / 3 + 1) / 2, 4)
    assert (only.cells, only.unstable_cells) == (6, 3)  # the three controls never wavered
    assert (recommendation.total_cells, recommendation.unstable_cells) == (6, 3)
    assert [item.prompt_id for item in recommendation.unstable_prompts] == [
        "want-0",
        "want-1",
        "want-2",
    ]
    assert all(item.worst_agreement == round(2 / 3, 4) for item in recommendation.unstable_prompts)
    assert all(
        item.unstable_configs == item.configs == 1 for item in recommendation.unstable_prompts
    )


def test_a_tie_between_repeats_is_not_a_majority() -> None:
    plan, results = _synthetic(_hits({"want-0": {0}}), repeats=2)  # right once in two repeats
    recommendation = analyze(plan, results)
    only = _score(recommendation, "singleton-01")

    assert only.majority_score == 0.5  # the tied cell does not count as correct
    assert recommendation.unstable_prompts[0].prompt_id == "want-0"
    assert recommendation.unstable_prompts[0].worst_agreement == 0.5
    assert any("can tie on a cell" in item for item in recommendation.limitations)


def test_the_most_unstable_prompts_are_ranked_and_capped() -> None:
    table = {"want-0": {0, 1}, **{f"want-{index}": {0, 1, 2} for index in range(1, 7)}}
    plan, results = _synthetic(_hits(table), repeats=4, positives=7)
    recommendation = analyze(plan, results)

    assert len(recommendation.unstable_prompts) == 5
    assert recommendation.unstable_prompts[0].prompt_id == "want-0"  # only half agreed
    assert recommendation.unstable_prompts[0].worst_agreement == 0.5
    assert recommendation.unstable_prompts[1].worst_agreement == 0.75
    assert recommendation.unstable_cells == 7


def test_the_noise_floor_is_the_larger_of_one_prompt_and_the_measured_spread() -> None:
    plan, results = _synthetic(_hits(SWINGING), repeats=3)
    swinging = analyze(plan, results)
    one_prompt = round(0.5 / 3, 4)
    assert swinging.noise_step == one_prompt
    assert swinging.measured_spread == round(1 / 3, 4)  # 50.0% to 83.3% across repeats
    assert swinging.effective_tolerance == swinging.measured_spread > one_prompt

    steady, steady_results = _synthetic(_hits({"want-0": {0, 1, 2}}), repeats=3)
    stable = analyze(steady, steady_results)
    assert stable.measured_spread == 0.0
    assert stable.effective_tolerance == one_prompt  # the one-prompt floor still applies

    one, one_results = _synthetic(_hits({"want-0": {0}}), repeats=1)
    assert analyze(one, one_results).measured_spread == 0.0


def test_a_lead_smaller_than_the_measured_spread_is_not_recommended() -> None:
    plan, results = _synthetic(_hits(SWINGING), repeats=3)
    swinging = analyze(plan, results)
    lead = swinging.best_score - swinging.baseline_score
    assert lead == pytest.approx((4 / 9 + 1) / 2 - 0.5, abs=1e-3)  # 22 points, under the spread
    assert swinging.no_skills_recommended and swinging.inconclusive
    assert swinging.recommended_count == 0

    # Answering two of the three prompts every time leads by 33.3 points, with no spread to beat.
    table = {"want-0": {0, 1, 2}, "want-1": {0, 1, 2}}
    steady, steady_results = _synthetic(_hits(table), repeats=3)
    stable = analyze(steady, steady_results)
    assert stable.recommended_skill_ids == ["skill-1"] and not stable.inconclusive


def test_a_partly_finished_repeat_does_not_count_toward_the_spread() -> None:
    plan, results = _synthetic(_hits({"want-0": {0, 1}, "want-1": {0, 1}}), repeats=3)
    # Repeat 2 got as far as one session for the skill, so it is not a full round.
    results = [
        item
        for item in results
        if not (
            item.repeat == 2 and item.config_id == "singleton-01" and item.prompt_id != "want-2"
        )
    ]
    recommendation = analyze(plan, results)
    assert recommendation.measured_spread == 0.0


def test_many_unstable_results_add_a_caution_to_the_advice() -> None:
    # Each cell is right in two of three repeats, so every repeat scores the same but every cell
    # is unstable.
    table = {"want-0": {0, 2}, "want-1": {0, 1}, "want-2": {1, 2}}
    plan, results = _synthetic(_hits(table), repeats=3)
    recommendation = analyze(plan, results)
    assert recommendation.measured_spread == 0.0 and recommendation.recommended_skill_ids

    advice = build_advice(plan, recommendation)
    assert advice.status == "install"
    assert any(
        "3 of 6 repeated results changed between repeats" in item for item in advice.cautions
    )


def test_the_report_shows_repeats_only_when_there_are_some(tmp_path: Path) -> None:
    plan, results = _synthetic(_hits(SWINGING), repeats=3)
    recommendation = analyze(plan, results)
    report = markdown_report(plan, recommendation)

    assert "the larger of one prompt and the measured run-to-run spread, 33.3%; 3 repeats" in report
    assert "| Range | Unstable cells |" in report
    assert "| 50.0%–83.3% | 3/6 |" in report
    assert "## Most unstable prompts" in report
    assert "- want-0 (positive): repeats disagreed in 1 of 1 configurations" in report
    assert any(
        "3 of 6 repeated cells changed outcome" in item for item in recommendation.limitations
    )

    write_reports(tmp_path, plan, recommendation)
    saved = json.loads((tmp_path / "recommendation.json").read_text(encoding="utf-8"))
    assert saved["repeats"] == 3 and saved["measured_spread"] == round(1 / 3, 4)
    assert saved["unstable_prompts"][0]["prompt_id"] == "want-0"

    single, single_results = _synthetic(_hits({"want-0": {0}}), repeats=1)
    plain = markdown_report(single, analyze(single, single_results))
    assert "Range" not in plain and "Most unstable" not in plain and "repeats)" not in plain


def test_plan_output_names_the_repeats_and_warns_about_an_even_number(
    capsys, tmp_path: Path
) -> None:
    plan, run_dir = _fixture_plan_dir(tmp_path, repeats=2)
    cli._print_plan(plan, run_dir)
    printed = capsys.readouterr().out
    assert "Repeats:          2 per configuration and prompt" in printed
    assert "an odd number of repeats avoids majority ties" in printed
    assert load_plan(run_dir).repeats == 2
