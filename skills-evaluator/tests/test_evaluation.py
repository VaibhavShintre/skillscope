from pathlib import Path
from types import SimpleNamespace

from skills_evaluator.analysis import analyze
from skills_evaluator.engine import AnthropicApiEngine, FakeEngine, token_cost
from skills_evaluator.models import EvalPrompt
from skills_evaluator.planner import build_plan
from skills_evaluator.profiler import profile_project
from skills_evaluator.report import write_reports
from skills_evaluator.skills import load_skill
from skills_evaluator.storage import append_result, load_plan, load_results, session_order

FIXTURES = Path(__file__).parent / "fixtures"


def test_fake_evaluation_runs_end_to_end_without_mutating_project(tmp_path: Path) -> None:
    project = FIXTURES / "project"
    before = (project / "src" / "app.tsx").read_bytes()
    plan, run_dir = build_plan(
        project=project,
        sources=[
            str(FIXTURES / "skills" / "testing"),
            str(FIXTURES / "skills" / "frontend"),
        ],
        run_root=tmp_path / "runs",
        include_anthropic=False,
        max_sessions=100,
    )
    configs = {item.id: item for item in plan.configurations}
    prompts = {item.id: item for item in plan.prompts}
    candidates = {item.id: item for item in plan.candidates}
    engine = FakeEngine()
    for config_id, prompt_id in session_order(run_dir):
        config = configs[config_id]
        result = engine.run(
            f"{config_id}::{prompt_id}",
            config_id,
            prompts[prompt_id],
            plan.project,
            [candidates[item] for item in config.skill_ids],
        )
        append_result(run_dir, result)

    loaded = load_results(run_dir)
    recommendation = analyze(load_plan(run_dir), loaded)
    write_reports(run_dir, plan, recommendation)

    assert recommendation.recommended_count == 2
    assert recommendation.recommended_score == recommendation.best_score
    assert (run_dir / "report.md").is_file()
    assert (run_dir / "report.html").is_file()
    assert (run_dir / "recommendation.json").is_file()
    assert (run_dir / "project-profile.json").is_file()
    assert (run_dir / "catalog.json").is_file()
    assert (run_dir / "security.json").is_file()
    assert (run_dir / "prompts.yaml").is_file()
    assert (run_dir / "experiment-plan.json").is_file()
    assert (project / "src" / "app.tsx").read_bytes() == before


def test_plan_is_bounded_and_has_multiple_combination_types(tmp_path: Path) -> None:
    plan, _ = build_plan(
        project=FIXTURES / "project",
        sources=[
            str(FIXTURES / "skills" / "testing"),
            str(FIXTURES / "skills" / "frontend"),
        ],
        run_root=tmp_path / "runs",
        include_anthropic=False,
        max_sessions=40,
    )
    assert plan.planned_sessions <= 40
    assert {item.kind for item in plan.configurations} >= {"baseline", "singleton", "full"}
    assert plan.estimated_max_cost_usd <= plan.max_cost_usd


def test_user_prompts_resolve_skill_names_to_canonical_ids(tmp_path: Path) -> None:
    plan, _ = build_plan(
        project=FIXTURES / "project",
        sources=[str(FIXTURES / "skills" / "testing")],
        run_root=tmp_path / "runs",
        include_anthropic=False,
        prompt_file=FIXTURES / "prompts.yaml",
    )
    prompt = next(item for item in plan.prompts if item.id == "user-browser-check")
    assert prompt.origin == "user"
    assert prompt.expected_skill == plan.candidates[0].id


class _Block:
    def __init__(self, block_type: str, **values) -> None:
        self.type = block_type
        self.__dict__.update(values)

    def model_dump(self) -> dict[str, object]:
        values = {key: value for key, value in self.__dict__.items() if key != "type"}
        return {"type": self.type, **values}


class _Messages:
    def __init__(self, skill_id: str) -> None:
        self.responses = [
            SimpleNamespace(
                content=[
                    _Block(
                        "tool_use",
                        id="tool-1",
                        name="load_skill",
                        input={"skill_id": skill_id},
                    )
                ],
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            ),
            SimpleNamespace(
                content=[_Block("text", text="Applied the relevant guidance.")],
                usage=SimpleNamespace(input_tokens=200, output_tokens=30),
            ),
        ]

    def create(self, **_kwargs):
        return self.responses.pop(0)


def test_api_engine_tool_loop_loads_exact_skill() -> None:
    profile = profile_project(FIXTURES / "project")
    skill = load_skill(FIXTURES / "skills" / "testing", profile, "fixture", True)
    prompt = EvalPrompt(
        id="test", text="Test the browser flow", expected_skill=skill.id, origin="skill-positive"
    )
    engine = AnthropicApiEngine.__new__(AnthropicApiEngine)
    engine.model = "claude-haiku-4-5-20251001"
    engine.max_tokens = 800
    engine.client = SimpleNamespace(messages=_Messages(skill.id))
    result = engine.run("config::test", "config", prompt, profile, [skill])
    assert result.selected_skills == [skill.id]
    assert result.answer == "Applied the relevant guidance."
    assert result.cost_usd == token_cost(engine.model, 300, 50)
