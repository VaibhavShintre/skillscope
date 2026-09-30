from pathlib import Path

import pytest

from skills_evaluator import planner
from skills_evaluator.analysis import analyze
from skills_evaluator.engine import FakeEngine
from skills_evaluator.planner import _config_costs, _configurations, build_plan
from skills_evaluator.profiler import profile_project
from skills_evaluator.skills import load_skill
from skills_evaluator.storage import append_result, session_order

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 6])
def test_required_configurations_include_every_leave_one_out(count: int) -> None:
    ids = [f"s{index}" for index in range(1, count + 1)]
    configs, required = _configurations(ids)
    required_sets = [frozenset(item.skill_ids) for item in configs[:required]]

    assert required_sets[0] == frozenset()  # baseline first
    assert {frozenset([item]) for item in ids} <= set(required_sets)  # every singleton
    assert frozenset(ids) in required_sets  # the full bundle
    for skill in ids:  # and the full bundle minus each skill
        assert frozenset(ids) - {skill} in required_sets
    # Everything after the required block is an extra; nothing is planned twice.
    sets = [frozenset(item.skill_ids) for item in configs]
    assert len(sets) == len(set(sets))
    assert [item.kind for item in configs[:required]][:1] == ["baseline"]


def test_required_count_for_four_skills_is_ten_and_extras_follow() -> None:
    configs, required = _configurations(["a", "b", "c", "d"])
    assert required == 10  # baseline, 4 singletons, full, 4 leave-one-outs
    assert [item.kind for item in configs[:required]].count("leave-one-out") == 4
    assert {item.kind for item in configs[required:]} <= {"greedy", "pair"}
    assert len(configs) == 16


def _four_skills(tmp_path: Path):
    profile = profile_project(FIXTURES / "project")
    skills = [
        load_skill(FIXTURES / "skills" / "testing", profile, "fixture", True),
        load_skill(FIXTURES / "skills" / "frontend", profile, "fixture", True),
    ]
    for name, category in (("data-tools", "backend"), ("doc-tools", "docs")):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Work on {category}.\ncategory: {category}\n---\n"
            f"# {name}\nHelp with {category} tasks.\n",
            encoding="utf-8",
        )
        skills.append(load_skill(folder, profile, str(folder), True))
    return skills


def test_a_tight_budget_keeps_every_leave_one_out_before_any_other_combination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = _four_skills(tmp_path)
    monkeypatch.setattr(planner, "discover_candidates", lambda *args, **kwargs: list(skills))

    def plan_with(cap: float, name: str):
        return build_plan(
            project=FIXTURES / "project",
            sources=[],
            run_root=tmp_path / name / "runs",
            include_anthropic=False,
            max_cost_usd=cap,
        )

    full_plan, _ = plan_with(10.0, "full")
    assert len(full_plan.configurations) == 16

    # A cap that pays for the required ten configurations and not one more.
    by_id = {item.id: item for item in full_plan.candidates}
    worst = [
        _config_costs(full_plan.model, full_plan.project, config, full_plan.prompts, by_id)[0]
        for config in full_plan.configurations
    ]
    tight, run_dir = plan_with(sum(worst[:10]) * 1.001, "tight")

    kinds = [item.kind for item in tight.configurations]
    assert len(kinds) == 10
    assert kinds.count("leave-one-out") == 4
    assert not {"greedy", "pair"} & set(kinds)

    # Run it with an ideal engine: no verdict is left unverified for want of a leave-one-out.
    configs = {item.id: item for item in tight.configurations}
    prompts = {item.id: item for item in tight.prompts}
    candidates = {item.id: item for item in tight.candidates}
    results = []
    for config_id, prompt_id in session_order(run_dir):
        available = [candidates[item] for item in configs[config_id].skill_ids]
        result = FakeEngine().run(
            f"{config_id}::{prompt_id}", config_id, prompts[prompt_id], tight.project, available
        )
        append_result(run_dir, result)
        results.append(result)
    recommendation = analyze(tight, results)
    assert all(item.leave_one_out_delta is not None for item in recommendation.verdicts)


def test_auto_discovered_skills_are_dropped_before_the_required_tests_are_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skills = _four_skills(tmp_path)
    requested = skills[:2]
    auto = [item.model_copy(update={"user_requested": False}) for item in skills[2:]]
    monkeypatch.setattr(planner, "discover_candidates", lambda *args, **kwargs: requested + auto)

    def plan_with(cap: float, name: str):
        return build_plan(
            project=FIXTURES / "project",
            sources=[],
            run_root=tmp_path / name / "runs",
            include_anthropic=False,
            max_cost_usd=cap,
        )

    everything, _ = plan_with(10.0, "all")
    assert everything.dropped_candidates == []

    monkeypatch.setattr(planner, "discover_candidates", lambda *args, **kwargs: list(requested))
    reference, _ = plan_with(10.0, "reference")

    monkeypatch.setattr(planner, "discover_candidates", lambda *args, **kwargs: requested + auto)
    squeezed, _ = plan_with(reference.estimated_max_cost_usd * 1.05, "squeezed")
    assert {item.name for item in squeezed.candidates} == {item.name for item in requested}
    assert set(squeezed.dropped_candidates) == {item.name for item in auto}
    # Explicitly requested skills are never dropped: too small a cap is an error instead.
    with pytest.raises(ValueError, match="leave-one-out"):
        plan_with(0.05, "too-small")
