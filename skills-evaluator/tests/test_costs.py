from pathlib import Path

import pytest

from skills_evaluator import cli
from skills_evaluator.costs import CHARS_PER_TOKEN, estimate_session
from skills_evaluator.engine import AnthropicApiEngine
from skills_evaluator.models import EvalPrompt, SessionResult
from skills_evaluator.planner import build_plan
from skills_evaluator.profiler import profile_project
from skills_evaluator.skills import load_skill
from skills_evaluator.storage import load_results

FIXTURES = Path(__file__).parent / "fixtures"
MODEL = "claude-haiku-4-5-20251001"
PROMPT = EvalPrompt(id="p", text="Test the checkout flow.", origin="project")


def _skills():
    profile = profile_project(FIXTURES / "project")
    return profile, [
        load_skill(FIXTURES / "skills" / "testing", profile, "fixture", True),
        load_skill(FIXTURES / "skills" / "frontend", profile, "fixture", True),
    ]


def test_expected_cost_is_below_the_worst_case_bound() -> None:
    profile, skills = _skills()
    for available in ([], skills[:1], skills):
        estimate = estimate_session(MODEL, profile, PROMPT, available)
        assert 0 < estimate.expected_usd < estimate.worst_usd


def test_no_skills_means_a_single_bounded_request() -> None:
    profile, _ = _skills()
    estimate = estimate_session(MODEL, profile, PROMPT, [], max_tokens=800)
    assert estimate.worst_output_tokens == 800  # no tool is offered, so one request


def test_worst_case_grows_with_skill_body_size_and_is_counted_once_per_later_request() -> None:
    profile, skills = _skills()
    small = estimate_session(MODEL, profile, PROMPT, skills[:1])
    big_body = "x" * 30_000
    big = estimate_session(
        MODEL, profile, PROMPT, [skills[0].model_copy(update={"body": skills[0].body + big_body})]
    )
    # The body reaches the context on requests 2 and 3, so the input grows by about twice its size.
    grown = big.worst_input_tokens - small.worst_input_tokens
    assert grown == pytest.approx(2 * len(big_body) / CHARS_PER_TOKEN, rel=0.01)
    assert big.worst_usd > small.worst_usd
    # Output is bounded by the turn limit times max_tokens, not by a single reply.
    assert small.worst_output_tokens == 3 * 800


def test_the_engine_returns_each_skill_body_only_once() -> None:
    profile, skills = _skills()
    skill = skills[0]

    class Block:
        def __init__(self, kind, **values):
            self.type = kind
            self.__dict__.update(values)

        def model_dump(self):
            return {"type": self.type}

    def reply(text_or_tool):
        blocks = (
            [Block("text", text="done")]
            if text_or_tool is None
            else [
                Block("tool_use", id=text_or_tool, name="load_skill", input={"skill_id": skill.id})
            ]
        )
        usage = type("Usage", (), {"input_tokens": 10, "output_tokens": 5})()
        return type("Response", (), {"content": blocks, "usage": usage})()

    class Messages:
        def __init__(self):
            self.replies = [reply("t1"), reply("t2"), reply(None)]
            self.conversation = None

        def create(self, **kwargs):
            self.conversation = kwargs["messages"]  # the engine keeps appending to this list
            return self.replies.pop(0)

    engine = AnthropicApiEngine.__new__(AnthropicApiEngine)
    engine.model, engine.max_tokens = MODEL, 800
    engine.client = type("Client", (), {"messages": Messages()})()
    result = engine.run("k", "c", PROMPT, profile, [skill])

    assert result.selected_skills == [skill.id]
    # The model asked for the same skill twice; only the first request returned the body.
    tool_results = [
        message["content"][0]["content"]
        for message in engine.client.messages.conversation
        if isinstance(message["content"], list)
        and message["content"][0].get("type") == "tool_result"
    ]
    assert tool_results == [skill.body, "This skill is already loaded above."]


def _plan(tmp_path: Path, **options):
    return build_plan(
        project=FIXTURES / "project",
        sources=[str(FIXTURES / "skills" / "testing"), str(FIXTURES / "skills" / "frontend")],
        run_root=tmp_path / "runs",
        include_anthropic=False,
        **options,
    )


def test_plan_reports_expected_and_worst_case_and_holds_the_cap_to_the_worst_case(
    tmp_path: Path,
) -> None:
    plan, _ = _plan(tmp_path, max_cost_usd=5.0)
    assert 0 < plan.estimated_expected_cost_usd < plan.estimated_max_cost_usd <= 5.0

    # The cap is held to the worst case: just above it the plan fits, just below it does not.
    worst = plan.estimated_max_cost_usd
    fits, _ = _plan(tmp_path / "fits", max_cost_usd=worst * 1.001)
    assert fits.planned_sessions == plan.planned_sessions
    with pytest.raises(ValueError, match="cap"):
        _plan(tmp_path / "over", max_cost_usd=worst * 0.999)


def test_a_cap_too_small_for_the_baseline_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cap"):
        _plan(tmp_path, max_cost_usd=0.01)


def test_a_three_skill_plan_fits_every_unique_configuration_under_three_dollars(
    tmp_path: Path,
) -> None:
    third = tmp_path / "third"
    third.mkdir()
    (third / "SKILL.md").write_text(
        "---\nname: web-artifacts-builder\ndescription: Build multi-component HTML artifacts.\n"
        "---\n# Artifacts\nBuild artifacts with React.\n",
        encoding="utf-8",
    )
    plan, _ = build_plan(
        project=FIXTURES / "project",
        sources=[
            str(FIXTURES / "skills" / "testing"),
            str(FIXTURES / "skills" / "frontend"),
            str(third),
        ],
        run_root=tmp_path / "runs",
        include_anthropic=False,
        max_cost_usd=3.0,
    )
    assert len(plan.candidates) == 3
    # baseline, 3 singletons, full, greedy-02, pair-01-03 and pair-02-03: the planner drops the
    # duplicate skill sets, so the "without-N" configs are the pairs and there are 8, not 10.
    assert len(plan.configurations) == 8
    assert {item.kind for item in plan.configurations} >= {"baseline", "full", "greedy", "pair"}
    assert plan.estimated_max_cost_usd <= 3.0


def test_the_run_stops_when_the_next_session_could_exceed_the_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, run_dir = _plan(tmp_path, max_cost_usd=5.0)

    class ExpensiveEngine:
        def __init__(self, model: str) -> None:
            pass

        def run(self, session_key, config_id, prompt, profile, skills) -> SessionResult:
            return SessionResult(
                session_key=session_key,
                config_id=config_id,
                prompt_id=prompt.id,
                prompt_origin=prompt.origin,
                expected_skill=prompt.expected_skill,
                available_skills=[item.id for item in skills],
                cost_usd=2.0,
                outcome="completed",
            )

    monkeypatch.setattr(cli, "AnthropicApiEngine", ExpensiveEngine)
    cli._execute(run_dir, fake=False)
    # $2 sessions against a $5 cap: a session starts only if spent + its worst case fits, so the
    # third starts (at $4) and the fourth is refused (at $6).
    results = load_results(run_dir)
    assert len(results) == 3 < plan.planned_sessions
    assert sum(item.cost_usd for item in results) == 6.0
