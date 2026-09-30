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
    # Skills the label cannot judge are ignored: what they do here is neither right nor wrong.
    fired = set(result.selected_skills) - label.unknown
    if label.kind == NEGATIVE:
        return not fired
    # Positive: an expected skill fired, and everything judged that fired was expected.
    return bool(fired & label.expected) and fired <= label.expected


def analyze(plan: RunPlan, results: list[SessionResult], tolerance: float = 0.02) -> Recommendation:
    completed = [item for item in results if item.outcome == "completed"]
    labels = {prompt.id: resolve_label(prompt, plan.candidates) for prompt in plan.prompts}
    labeling = summarize_labels(plan.prompts, plan.candidates)
    # With most prompts unlabeled, any score describes a minority of the prompts; say so
    # instead of recommending.
    insufficient = bool(plan.prompts) and labeling.unlabeled_prompts * 2 > len(plan.prompts)
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
        not insufficient
        and baseline is not None
        and baseline_score is not None
        and best_score is not None
        and baseline_score >= best_score - tolerance
    )
    if insufficient:
        recommended = None
    elif no_skills_wins:
        recommended = baseline
    elif eligible:
        recommended = min(
            eligible, key=lambda item: (len(item.skill_ids), -scores[item.id], item.id)
        )
    else:
        recommended = plan.configurations[0]

    selected_ids = set(recommended.skill_ids) if recommended else set()
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
                if skill.id in result.selected_skills and skill.id not in label.unknown
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
        elif insufficient:
            verdict = "unverified"
            reasons.append(
                f"Insufficient labeled evidence: {labeling.unlabeled_prompts} of "
                f"{len(plan.prompts)} prompts are unlabeled."
            )
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
                auto_discovered=not skill.user_requested,
                # Every per-skill number describes a minority of the prompts when the
                # evidence is insufficient, so all of them are withheld.
                precision=None if precision is None or insufficient else round(precision, 4),
                precision_by_config={
                    key: None if value is None else round(value, 4)
                    for key, value in precision_by_config.items()
                    if not insufficient
                },
                recall=None if recall is None or insufficient else round(recall, 4),
                singleton_score=(
                    None if singleton is None or insufficient else round(singleton, 4)
                ),
                leave_one_out_delta=(
                    None if delta is None or insufficient else round(delta, 4)
                ),
                selected=skill.id in selected_ids,
                reasons=reasons,
            )
        )

    labeling.unlabeled_sessions = unlabeled_sessions
    labeling.unjudged_activations = sum(
        len(set(result.selected_skills) & label.unknown)
        for entries in judged.values()
        for result, label, _ in entries
    )
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
            "Skills without a category are not judged on capability-derived prompts ("
            + ", ".join(labeling.uncategorized_skills)
            + f"); {labeling.unjudged_activations} of their activations there were ignored. "
            "Add a `category:` field to their SKILL.md frontmatter."
        )
    if insufficient:
        limitations.insert(
            0,
            f"Insufficient labeled evidence: {labeling.unlabeled_prompts} of "
            f"{len(plan.prompts)} prompts are unlabeled (more than half), so no recommendation "
            "is issued.",
        )
    if best_score is None and not insufficient:
        limitations.append("No labeled sessions were scored, so no bundle could be recommended.")
    scored = [len(entries) for entries in judged.values() if entries]
    scored_per_config = min(scored, default=0)
    if scored_per_config:
        limitations.append(
            f"Each configuration was scored on only {scored_per_config} labeled prompts "
            f"(1 prompt = {100 / scored_per_config:.1f} points), so score differences smaller "
            "than that are not statistically significant."
        )
    recommended_score = scores.get(recommended.id) if recommended else None
    headline = None if insufficient else best_score
    return Recommendation(
        run_id=plan.run_id,
        recommended_config=recommended.id if recommended else "",
        recommended_skill_ids=recommended.skill_ids if recommended else [],
        recommended_count=len(recommended.skill_ids) if recommended else 0,
        best_score=None if headline is None else round(headline, 4),
        recommended_score=None if recommended_score is None else round(recommended_score, 4),
        no_skills_recommended=no_skills_wins,
        insufficient_evidence=insufficient,
        baseline_score=(
            None if baseline_score is None or insufficient else round(baseline_score, 4)
        ),
        scored_prompts_per_config=scored_per_config,
        tolerance=tolerance,
        total_cost_usd=round(sum(item.cost_usd for item in results), 6),
        completed_sessions=len(completed),
        verdicts=verdicts,
        labeling=labeling,
        limitations=limitations,
    )
