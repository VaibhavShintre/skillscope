import json
from pathlib import Path

import pytest

from skills_evaluator.advice import MAX_LINES, build_advice
from skills_evaluator.analysis import analyze
from skills_evaluator.engine import FakeEngine
from skills_evaluator.models import (
    DecoyReport,
    ProjectProfile,
    Recommendation,
    RunPlan,
    SessionResult,
    SkillCandidate,
    SkillVerdict,
)
from skills_evaluator.planner import build_plan
from skills_evaluator.report import markdown_report, write_reports
from skills_evaluator.storage import append_result, load_plan, session_order

FIXTURES = Path(__file__).parent / "fixtures"


def _candidate(name: str, source: str = "somewhere") -> SkillCandidate:
    return SkillCandidate(
        id=name,
        name=name,
        description="x",
        source=source,
        source_path=f"/skills/{name}",
        content_hash=name,
        body="",
        user_requested=True,
    )


def _plan(candidates: list[SkillCandidate]) -> RunPlan:
    return RunPlan(
        run_id="run",
        project=ProjectProfile(root="/project", name="project", content_hash="h"),
        candidates=candidates,
        prompts=[],
        configurations=[],
        model="claude-haiku-4-5-20251001",
        seed=1,
        max_sessions=10,
        max_cost_usd=1,
        estimated_max_cost_usd=0,
        planned_sessions=0,
    )


def _verdict(name: str, verdict: str, selected: bool = False, **extra) -> SkillVerdict:
    return SkillVerdict(skill_id=name, name=name, verdict=verdict, selected=selected, **extra)


def _recommendation(verdicts: list[SkillVerdict], **flags) -> Recommendation:
    return Recommendation(
        run_id="run",
        recommended_config="x",
        recommended_skill_ids=[item.skill_id for item in verdicts if item.selected],
        recommended_count=sum(item.selected for item in verdicts),
        tolerance=0.02,
        total_cost_usd=0,
        completed_sessions=0,
        verdicts=verdicts,
        limitations=[],
        **flags,
    )


def _advice(verdicts: list[SkillVerdict], installed: tuple[str, ...] = (), **flags):
    candidates = [
        _candidate(item.skill_id, "project-installed" if item.skill_id in installed else "web")
        for item in verdicts
    ]
    return build_advice(_plan(candidates), _recommendation(verdicts, **flags), Path("/runs/r1"))


def test_kept_skills_say_which_kinds_of_task_they_helped_with() -> None:
    advice = _advice(
        [
            _verdict("alpha", "essential", True, helped_with=["frontend", "testing"]),
            _verdict("beta", "useful", True),
            _verdict("gamma", "marginal", True, recall=1 / 3),
            _verdict("delta", "contested", True),
        ]
    )
    assert advice.status == "install"
    assert advice.headline == "Install alpha, beta, gamma and delta."
    assert advice.keep[0] == "alpha — helped with frontend and testing"
    assert advice.keep[1] == "beta — part of the smallest bundle that beat no skills"
    assert advice.keep[2] == (
        "gamma — weak evidence, fired on only 33% of the prompts that expected it"
    )
    assert advice.keep[3] == "delta — the full bundle scores better without it"


@pytest.mark.parametrize(
    ("verdict", "extra", "expected"),
    [
        ("harmful", {"over_fires_on": ["database", "security"]},
         "harmful: over-fires on database and security"),
        ("harmful", {}, "harmful: it lowers the score when listed with the others"),
        ("irrelevant", {"fired": 0, "sessions": 8}, "never fires"),
        ("irrelevant", {"fired": 2, "sessions": 8}, "never fires when it should"),
        ("conflicting", {"fired": 3, "sessions": 8, "over_fires_on": ["api"]},
         "over-fires: mostly fires on the wrong prompts (api)"),
        ("redundant", {"fired": 3, "sessions": 8}, "redundant with kept"),
        ("no_lift", {"fired": 3, "sessions": 8}, "no better than using no skills"),
        ("contested", {"fired": 3, "sessions": 8},
         "contested: it helps the full bundle, but a smaller bundle did as well"),
        ("unverified", {"fired": 3, "sessions": 8}, "not enough evidence to judge it"),
        ("unsafe", {}, "blocked by the safety check"),
    ],
)
def test_each_rejected_skill_gets_a_one_line_reason(
    verdict: str, extra: dict, expected: str
) -> None:
    advice = _advice([_verdict("kept", "useful", True), _verdict("dropped", verdict, **extra)])
    assert advice.drop == [f"dropped — {expected}"]


def test_a_skill_that_never_fired_is_said_to_never_fire_whatever_its_verdict() -> None:
    advice = _advice(
        [_verdict("kept", "useful", True), _verdict("quiet", "redundant", fired=0, sessions=12)]
    )
    assert advice.drop == ["quiet — never fires"]


@pytest.mark.parametrize(
    ("flags", "status", "phrase"),
    [
        ({"no_skills_recommended": True, "inconclusive": True}, "inconclusive", "Inconclusive"),
        ({"no_skills_recommended": True}, "no_skills", "Use no skills"),
        ({"insufficient_evidence": True}, "insufficient", "too few prompts are labeled"),
    ],
)
def test_inconclusive_no_skills_and_thin_evidence_are_stated_without_advice(
    flags: dict, status: str, phrase: str
) -> None:
    verdicts = [_verdict("alpha", "no_lift"), _verdict("beta", "harmful")]
    advice = _advice(verdicts, **flags)
    assert advice.status == status and phrase in advice.headline
    assert advice.keep == [] and advice.drop == [] and advice.steps == []

    plan = _plan([_candidate("alpha"), _candidate("beta")])
    report = markdown_report(plan, _recommendation(verdicts, **flags))
    report_lines = report.splitlines()
    start = report_lines.index("## What to do")
    # Just the one plain statement, then the rest of the report begins.
    assert report_lines[start + 2].startswith("**") and report_lines[start + 3] == ""
    assert report_lines[start + 4].startswith((">", "- Recommended number"))
    assert "Steps:" not in report and "Keep:" not in report and "Drop:" not in report


def test_a_recommended_bundle_no_better_than_the_decoy_is_called_out() -> None:
    decoy = DecoyReport(name="project-conventions", advantage=0.2, recommended_matches=True)
    advice = _advice([_verdict("alpha", "essential", True)], decoy=decoy)
    assert len(advice.cautions) == 1
    assert "no larger than a useless decoy skill's" in advice.cautions[0]
    quiet = _advice(
        [_verdict("alpha", "essential", True)],
        decoy=DecoyReport(name="project-conventions", recommended_matches=False),
    )
    assert quiet.cautions == []


def test_steps_are_exact_for_the_project() -> None:
    # Nothing installed yet: one command installs the whole bundle.
    fresh = _advice([_verdict("alpha", "useful", True), _verdict("beta", "useful", True)])
    assert fresh.steps == [
        f'Install: skills-evaluator install "{Path("/runs/r1")}" --project "/project" --apply '
        "(leave off --apply to preview)"
    ]

    # One recommended skill is already in .claude/skills: the command would refuse to overwrite it,
    # so only the missing one is copied, and a rejected installed skill is removed.
    mixed = _advice(
        [
            _verdict("alpha", "useful", True),
            _verdict("beta", "useful", True),
            _verdict("gamma", "harmful"),
        ],
        installed=("alpha", "gamma"),
    )
    assert mixed.steps == [
        'Install beta: copy the folder "/skills/beta" to "/project/.claude/skills/beta"',
        'Remove gamma: delete the folder "/skills/gamma"',
    ]

    settled = _advice([_verdict("alpha", "useful", True)], installed=("alpha",))
    assert settled.steps == ["No changes needed: the project already has exactly these skills."]


def test_long_lists_are_cut_short_enough_to_read_quickly() -> None:
    verdicts = [_verdict("kept", "useful", True)] + [
        _verdict(f"skill-{index}", "harmful", over_fires_on=["api"]) for index in range(8)
    ]
    advice = _advice(verdicts)
    assert len(advice.drop) == MAX_LINES + 1
    assert advice.drop[-1] == "...and 3 more (see the table below)"


def _run(engine, tmp_path: Path):
    plan, run_dir = build_plan(
        project=FIXTURES / "project",
        sources=[str(FIXTURES / "skills" / "testing"), str(FIXTURES / "skills" / "frontend")],
        run_root=tmp_path / "runs",
        include_anthropic=False,
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
    return load_plan(run_dir), run_dir, results


def test_the_analysis_records_where_each_skill_helped(tmp_path: Path) -> None:
    plan, run_dir, results = _run(FakeEngine(), tmp_path)
    recommendation = analyze(plan, results)
    by_name = {item.name: item for item in recommendation.verdicts}

    assert by_name["browser-testing"].helped_with == ["testing", "its described task"]
    assert by_name["frontend-design"].helped_with == [
        "frontend",
        "its described task",
    ]
    assert all(item.over_fires_on == [] and item.fired > 0 for item in by_name.values())

    write_reports(run_dir, plan, recommendation)
    markdown = (run_dir / "report.md").read_text(encoding="utf-8")
    assert markdown.index("## What to do") < markdown.index("- Recommended number of skills")
    assert markdown.startswith("# Skills Evaluator report")
    assert "**Install browser-testing and frontend-design.**" in markdown
    assert f'install "{run_dir.resolve()}" --project "{plan.project.root}" --apply' in markdown

    saved = json.loads((run_dir / "recommendation.json").read_text(encoding="utf-8"))
    assert saved["what_to_do"]["status"] == "install"
    assert saved["what_to_do"]["headline"] == "Install browser-testing and frontend-design."

    page = (run_dir / "report.html").read_text(encoding="utf-8")
    assert page.index("<section class='todo'>") < page.index("<pre>")
    assert "Install browser-testing and frontend-design." in page.split("<pre>")[0]
    assert "## What to do" not in page  # shown once, as the card at the top


class _OverEager:
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


def test_the_analysis_records_where_a_skill_over_fired(tmp_path: Path) -> None:
    plan, _, results = _run(_OverEager(), tmp_path)
    recommendation = analyze(plan, results)
    for item in recommendation.verdicts:
        assert item.fired == item.sessions > 0
        assert "general questions" in item.over_fires_on
        assert len(item.over_fires_on) <= 3
    # Every skill fires on everything, so the plain answer is to use none.
    advice = build_advice(plan, recommendation)
    assert advice.status == "no_skills" and advice.keep == []
