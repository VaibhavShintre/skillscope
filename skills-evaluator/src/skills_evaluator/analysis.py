from __future__ import annotations

from collections import defaultdict

from skills_evaluator.models import (
    Recommendation,
    RunPlan,
    SessionResult,
    SkillVerdict,
)


def _correct(result: SessionResult) -> bool:
    if result.outcome != "completed":
        return False
    if result.expected_skill is None:
        return not result.selected_skills if result.prompt_origin == "control" else True
    return result.expected_skill in result.selected_skills


def analyze(plan: RunPlan, results: list[SessionResult], tolerance: float = 0.02) -> Recommendation:
    completed = [item for item in results if item.outcome == "completed"]
    by_config: dict[str, list[SessionResult]] = defaultdict(list)
    for result in completed:
        by_config[result.config_id].append(result)
    scores = {
        config.id: (
            sum(_correct(item) for item in by_config[config.id]) / len(by_config[config.id])
            if by_config[config.id]
            else 0.0
        )
        for config in plan.configurations
    }
    non_baseline = [config for config in plan.configurations if config.skill_ids]
    best_score = max((scores[config.id] for config in non_baseline), default=0.0)
    eligible = [config for config in non_baseline if scores[config.id] >= best_score - tolerance]
    recommended = (
        min(eligible, key=lambda item: (len(item.skill_ids), -scores[item.id], item.id))
        if eligible
        else plan.configurations[0]
    )

    selected_ids = set(recommended.skill_ids)
    verdicts: list[SkillVerdict] = []
    for skill in plan.candidates:
        relevant = [
            item
            for item in completed
            if item.expected_skill == skill.id and skill.id in item.available_skills
        ]
        selected_events = [item for item in completed if skill.id in item.selected_skills]
        true_selected = [item for item in selected_events if item.expected_skill == skill.id]
        recall = (
            sum(skill.id in item.selected_skills for item in relevant) / len(relevant)
            if relevant
            else 0
        )
        precision = len(true_selected) / len(selected_events) if selected_events else 0
        singleton = next(
            (
                scores[config.id]
                for config in plan.configurations
                if config.kind == "singleton" and config.skill_ids == [skill.id]
            ),
            0.0,
        )
        without = next(
            (
                scores[config.id]
                for config in plan.configurations
                if config.kind == "leave-one-out" and skill.id not in config.skill_ids
            ),
            best_score,
        )
        delta = scores.get("full", best_score) - without
        reasons: list[str] = []
        if skill.blocked:
            verdict = "unsafe"
            reasons.append("Blocked by the static safety gate.")
        elif skill.id in selected_ids and delta >= 0.05:
            verdict = "essential"
            reasons.append(f"Removing it reduced the full-bundle score by {delta:.1%}.")
        elif skill.id in selected_ids:
            verdict = "useful"
            reasons.append("Included in the smallest bundle within the score tolerance.")
        elif recall == 0:
            verdict = "irrelevant"
            reasons.append("It was never selected on an expected prompt.")
        elif precision < 0.5:
            verdict = "conflicting"
            reasons.append("Most observed selections were outside its expected prompts.")
        else:
            verdict = "redundant"
            reasons.append("It showed value but was unnecessary in the smallest near-best bundle.")
        verdicts.append(
            SkillVerdict(
                skill_id=skill.id,
                name=skill.name,
                verdict=verdict,
                precision=round(precision, 4),
                recall=round(recall, 4),
                singleton_score=round(singleton, 4),
                leave_one_out_delta=round(delta, 4),
                selected=skill.id in selected_ids,
                reasons=reasons,
            )
        )
    return Recommendation(
        run_id=plan.run_id,
        recommended_config=recommended.id,
        recommended_skill_ids=recommended.skill_ids,
        recommended_count=len(recommended.skill_ids),
        best_score=round(best_score, 4),
        recommended_score=round(scores.get(recommended.id, 0), 4),
        tolerance=tolerance,
        total_cost_usd=round(sum(item.cost_usd for item in results), 6),
        completed_sessions=len(completed),
        verdicts=verdicts,
        limitations=[
            "This run measures routing in the Skills Evaluator API harness, "
            "not native Claude Code.",
            "Candidate scripts were inventoried but not executed.",
            "The initial MVP scores prompt-level routing; deterministic task "
            "outcome adapters are next.",
        ],
    )
