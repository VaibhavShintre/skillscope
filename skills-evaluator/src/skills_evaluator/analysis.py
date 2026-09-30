from __future__ import annotations

from collections import defaultdict

from skills_evaluator.labels import (
    NEGATIVE,
    POSITIVE,
    UNLABELED,
    Label,
    resolve_label,
    summarize_labels,
)
from skills_evaluator.models import (
    Recommendation,
    RunPlan,
    SessionResult,
    SkillVerdict,
)

_UNKNOWN = Label(UNLABELED, frozenset(), "heuristic")


def _correct(result: SessionResult, label: Label) -> bool | None:
    """True/False for labeled prompts; None when the prompt is unlabeled (excluded)."""
    if label.kind == UNLABELED:
        return None
    fired = set(result.selected_skills)
    if label.kind == NEGATIVE:
        return not fired
    # Positive: something fired, and everything that fired was an expected skill.
    return bool(fired) and fired <= label.expected


def analyze(plan: RunPlan, results: list[SessionResult], tolerance: float = 0.02) -> Recommendation:
    completed = [item for item in results if item.outcome == "completed"]
    labels = {prompt.id: resolve_label(prompt, plan.candidates) for prompt in plan.prompts}
    # (result, label, correct) for labeled sessions only; unlabeled ones are counted, not scored.
    judged: dict[str, list[tuple[SessionResult, Label, bool]]] = defaultdict(list)
    unlabeled_sessions = 0
    for result in completed:
        label = labels.get(result.prompt_id, _UNKNOWN)
        correct = _correct(result, label)
        if correct is None:
            unlabeled_sessions += 1
        else:
            judged[result.config_id].append((result, label, correct))

    scores: dict[str, float | None] = {
        config.id: (
            sum(correct for _, _, correct in judged[config.id]) / len(judged[config.id])
            if judged[config.id]
            else None
        )
        for config in plan.configurations
    }
    score_by_skills = {
        frozenset(config.skill_ids): scores[config.id]
        for config in plan.configurations
        if scores[config.id] is not None
    }
    full_ids = frozenset(item.id for item in plan.candidates if not item.blocked)
    non_baseline = [config for config in plan.configurations if config.skill_ids]
    measured = [config for config in non_baseline if scores[config.id] is not None]
    best_score = max((scores[config.id] for config in measured), default=None)
    eligible = (
        [config for config in measured if scores[config.id] >= best_score - tolerance]
        if best_score is not None
        else []
    )
    baseline = next((item for item in plan.configurations if not item.skill_ids), None)
    baseline_score = scores.get(baseline.id) if baseline else None
    # If no bundle beats having no skills (within tolerance), the honest answer is "no skills".
    no_skills_wins = (
        baseline is not None
        and baseline_score is not None
        and best_score is not None
        and baseline_score >= best_score - tolerance
    )
    if no_skills_wins:
        recommended = baseline
    elif eligible:
        recommended = min(
            eligible, key=lambda item: (len(item.skill_ids), -scores[item.id], item.id)
        )
    else:
        recommended = plan.configurations[0]

    selected_ids = set(recommended.skill_ids)
    verdicts: list[SkillVerdict] = []
    for skill in plan.candidates:
        # Precision is per configuration: what fraction of labeled activations in that
        # configuration were expected. An activation on a negative prompt, or on a prompt
        # that expected a different skill, is a false positive.
        precision_by_config: dict[str, float | None] = {}
        for config in plan.configurations:
            if skill.id not in config.skill_ids:
                continue
            fired = [
                label
                for result, label, _ in judged[config.id]
                if skill.id in result.selected_skills
            ]
            precision_by_config[config.id] = (
                sum(skill.id in label.expected for label in fired) / len(fired) if fired else None
            )
        full_config = next(
            (item for item in plan.configurations if frozenset(item.skill_ids) == full_ids), None
        )
        precision = precision_by_config.get(full_config.id) if full_config else None

        relevant = [
            result
            for entries in judged.values()
            for result, label, _ in entries
            if label.kind == POSITIVE
            and skill.id in label.expected
            and skill.id in result.available_skills
        ]
        recall = (
            sum(skill.id in result.selected_skills for result in relevant) / len(relevant)
            if relevant
            else None
        )
        singleton = next(
            (
                scores[config.id]
                for config in plan.configurations
                if config.kind == "singleton" and config.skill_ids == [skill.id]
            ),
            None,
        )
        # Leave-one-out is "the full bundle minus this skill". The planner drops duplicate
        # skill sets, so that config is often a singleton or the baseline; look it up by its
        # skills, not by kind. None means it was not planned or has no scored sessions.
        full_score = score_by_skills.get(full_ids)
        without = (
            score_by_skills.get(full_ids - {skill.id}) if skill.id in full_ids else None
        )
        delta = full_score - without if full_score is not None and without is not None else None
        reasons: list[str] = []
        if skill.blocked:
            verdict = "unsafe"
            reasons.append("Blocked by the static safety gate.")
        elif skill.id in selected_ids and delta is None:
            verdict = "unverified"
            reasons.append(
                "The full bundle without this skill was not measured, so its marginal "
                "value is unknown."
            )
        elif skill.id in selected_ids and delta >= 0.05:
            verdict = "essential"
            reasons.append(f"Removing it reduced the full-bundle score by {delta:.1%}.")
        elif skill.id in selected_ids:
            verdict = "useful"
            reasons.append("Included in the smallest bundle within the score tolerance.")
        elif recall is None:
            verdict = "unverified"
            reasons.append("No labeled prompt expected this skill, so its routing is unmeasured.")
        elif recall == 0:
            verdict = "irrelevant"
            reasons.append("It was never selected on an expected prompt.")
        elif precision is not None and precision < 0.5:
            verdict = "conflicting"
            reasons.append("Most observed selections were outside its expected prompts.")
        elif no_skills_wins:
            verdict = "no_lift"
            reasons.append("No bundle beat the no-skill baseline, so this skill added no lift.")
        else:
            verdict = "redundant"
            reasons.append("It showed value but was unnecessary in the smallest near-best bundle.")
        verdicts.append(
            SkillVerdict(
                skill_id=skill.id,
                name=skill.name,
                verdict=verdict,
                precision=None if precision is None else round(precision, 4),
                precision_by_config={
                    key: None if value is None else round(value, 4)
                    for key, value in precision_by_config.items()
                },
                recall=None if recall is None else round(recall, 4),
                singleton_score=None if singleton is None else round(singleton, 4),
                leave_one_out_delta=None if delta is None else round(delta, 4),
                selected=skill.id in selected_ids,
                reasons=reasons,
            )
        )

    labeling = summarize_labels(plan.prompts, plan.candidates)
    labeling.unlabeled_sessions = unlabeled_sessions
    limitations = [
        "This run measures routing in the Skills Evaluator API harness, not native Claude Code.",
        "Candidate scripts were inventoried but not executed.",
        "The initial MVP scores prompt-level routing; deterministic task "
        "outcome adapters are next.",
    ]
    if labeling.unlabeled_prompts:
        limitations.append(
            f"{labeling.unlabeled_prompts} of {len(plan.prompts)} prompts are unlabeled and "
            "excluded from score and precision; label them in the prompts YAML to include them."
        )
    if labeling.uncategorized_skills:
        limitations.append(
            "Skills without a category disable the capability heuristic for every prompt: "
            + ", ".join(labeling.uncategorized_skills)
            + ". Add a `category:` field to their SKILL.md frontmatter."
        )
    if best_score is None:
        limitations.append("No labeled sessions were scored, so no bundle could be recommended.")
    scored = [len(entries) for entries in judged.values() if entries]
    scored_per_config = min(scored, default=0)
    if scored_per_config:
        limitations.append(
            f"Each configuration was scored on only {scored_per_config} labeled prompts "
            f"(1 prompt = {100 / scored_per_config:.1f} points), so score differences smaller "
            "than that are not statistically significant."
        )
    recommended_score = scores.get(recommended.id)
    return Recommendation(
        run_id=plan.run_id,
        recommended_config=recommended.id,
        recommended_skill_ids=recommended.skill_ids,
        recommended_count=len(recommended.skill_ids),
        best_score=None if best_score is None else round(best_score, 4),
        recommended_score=None if recommended_score is None else round(recommended_score, 4),
        no_skills_recommended=no_skills_wins,
        baseline_score=None if baseline_score is None else round(baseline_score, 4),
        scored_prompts_per_config=scored_per_config,
        tolerance=tolerance,
        total_cost_usd=round(sum(item.cost_usd for item in results), 6),
        completed_sessions=len(completed),
        verdicts=verdicts,
        labeling=labeling,
        limitations=limitations,
    )
