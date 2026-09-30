from __future__ import annotations

from collections import defaultdict

from skills_evaluator.costs import estimate_session
from skills_evaluator.labels import (
    NEGATIVE,
    POSITIVE,
    UNLABELED,
    Label,
    resolve_label,
    summarize_labels,
)
from skills_evaluator.models import (
    ConfigScore,
    DecoyReport,
    Recommendation,
    RunPlan,
    SessionResult,
    SkillVerdict,
)

_UNKNOWN = Label(UNLABELED, frozenset(), "heuristic")
# Scores are ratios of small counts; compare them with a little slack, so a gap that equals the
# tolerance is a tie however the floating-point arithmetic happened to round it.
EPSILON = 1e-9
# The smallest leave-one-out change that can make a skill "essential", and the recall below
# which a skill in the recommended bundle is only "marginal".
ESSENTIAL_DELTA = 0.05
MARGINAL_RECALL = 0.5

Judged = tuple[SessionResult, Label, bool]


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


def _rate(entries: list[Judged], kind: str) -> tuple[float | None, int]:
    matching = [correct for _, label, correct in entries if label.kind == kind]
    return (sum(matching) / len(matching) if matching else None), len(matching)


def _balanced(positive: float | None, negative: float | None) -> float | None:
    """Average of the positive-prompt and negative-prompt scores.

    A configuration that never fires gets every negative prompt right and every positive one
    wrong, so it scores 50% however many negative prompts there are: staying silent cannot win
    by default. With only one kind of prompt labeled the score is that kind's rate.
    """
    present = [value for value in (positive, negative) if value is not None]
    return sum(present) / len(present) if present else None


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def analyze(
    plan: RunPlan,
    results: list[SessionResult],
    tolerance: float = 0.02,
    noise_prompts: float = 1.0,
) -> Recommendation:
    completed = [item for item in results if item.outcome == "completed"]
    labels = {prompt.id: resolve_label(prompt, plan.candidates) for prompt in plan.prompts}
    labeling = summarize_labels(plan.prompts, plan.candidates)
    # With most prompts unlabeled, any score describes a minority of the prompts; say so
    # instead of recommending.
    insufficient = bool(plan.prompts) and labeling.unlabeled_prompts * 2 > len(plan.prompts)
    # (result, label, correct) for labeled sessions only; unlabeled ones are counted, not scored.
    judged: dict[str, list[Judged]] = defaultdict(list)
    unlabeled_sessions = 0
    for result in completed:
        label = labels.get(result.prompt_id, _UNKNOWN)
        correct = _correct(result, label)
        if correct is None:
            unlabeled_sessions += 1
        else:
            judged[result.config_id].append((result, label, correct))

    decoy_skill = next((item for item in plan.candidates if item.decoy), None)
    decoy_ids = {decoy_skill.id} if decoy_skill else set()

    positive_scores: dict[str, float | None] = {}
    negative_scores: dict[str, float | None] = {}
    positive_counts: dict[str, int] = {}
    negative_counts: dict[str, int] = {}
    scores: dict[str, float | None] = {}
    for config in plan.configurations:
        entries = judged[config.id]
        positive_scores[config.id], positive_counts[config.id] = _rate(entries, POSITIVE)
        negative_scores[config.id], negative_counts[config.id] = _rate(entries, NEGATIVE)
        scores[config.id] = _balanced(positive_scores[config.id], negative_scores[config.id])
    score_by_skills = {
        frozenset(config.skill_ids): scores[config.id]
        for config in plan.configurations
        if scores[config.id] is not None
    }

    scored_per_config = min((len(entries) for entries in judged.values() if entries), default=0)
    positive_n = min((count for count in positive_counts.values() if count), default=0)
    negative_n = min((count for count in negative_counts.values() if count), default=0)
    # One prompt is the smallest difference a run can show. In a balanced score a positive
    # prompt moves it by 0.5 / positive_n and a negative one by 0.5 / negative_n; the larger
    # step is the noise floor, and a difference of one prompt is never treated as a real gap.
    if positive_n and negative_n:
        noise_step = 0.5 / min(positive_n, negative_n)
    elif positive_n or negative_n:
        noise_step = 1 / max(positive_n, negative_n)
    else:
        noise_step = 0.0
    effective_tolerance = max(tolerance, noise_prompts * noise_step)

    full_ids = frozenset(
        item.id for item in plan.candidates if not item.blocked and not item.decoy
    )
    full_score = score_by_skills.get(full_ids)
    baseline = next((item for item in plan.configurations if not item.skill_ids), None)
    baseline_score = scores.get(baseline.id) if baseline else None
    # Only real skill bundles compete; the decoy's configurations are controls.
    bundles = [
        config
        for config in plan.configurations
        if config.skill_ids and not decoy_ids & set(config.skill_ids)
    ]
    measured = [config for config in bundles if scores[config.id] is not None]
    best_score = max((scores[config.id] for config in measured), default=None)
    eligible = (
        [
            config
            for config in measured
            if scores[config.id] >= best_score - effective_tolerance - EPSILON
        ]
        if best_score is not None
        else []
    )

    def beats_baseline(score: float) -> bool:
        """Ahead of no skills by more than the noise floor; a gap equal to it is a tie."""
        return (
            baseline_score is not None and score - baseline_score > effective_tolerance + EPSILON
        )

    # A recommended bundle must itself beat no skills beyond the noise floor. Choosing the
    # smallest bundle near the best is not enough: it can lead the baseline by only one prompt.
    winners = [config for config in eligible if beats_baseline(scores[config.id])]
    no_skills_wins = (
        not insufficient
        and baseline is not None
        and baseline_score is not None
        and best_score is not None
        and not winners
    )
    inconclusive = no_skills_wins and (
        abs(best_score - baseline_score) <= effective_tolerance + EPSILON
    )
    if insufficient:
        recommended = None
    elif no_skills_wins:
        recommended = baseline
    elif winners:
        recommended = min(
            winners, key=lambda item: (len(item.skill_ids), -scores[item.id], item.id)
        )
    elif eligible:  # no baseline to compare with
        recommended = min(
            eligible, key=lambda item: (len(item.skill_ids), -scores[item.id], item.id)
        )
    else:
        recommended = plan.configurations[0]

    selected_ids = set(recommended.skill_ids) if recommended else set()

    def meaningful(delta: float | None) -> bool:
        """A leave-one-out change larger than the noise floor and the essential threshold."""
        return (
            delta is not None
            and delta > effective_tolerance + EPSILON
            and delta >= ESSENTIAL_DELTA - EPSILON
        )

    verdicts: list[SkillVerdict] = []
    for skill in plan.candidates:
        if skill.decoy:
            continue
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
        elif skill.id in selected_ids and recall is not None and recall < MARGINAL_RECALL:
            # Its score share cannot be credited to its own activations: a skill that rarely
            # fires costs nothing on negative prompts, and a bundle can score well because
            # another skill fires less when more skills are listed.
            verdict = "marginal"
            reasons.append(
                f"In the recommended bundle, but it fired on only {recall:.0%} of the prompts "
                "that expected it, so the bundle's score may owe more to other skills firing "
                "less than to this one."
            )
        elif skill.id in selected_ids and delta is None:
            verdict = "unverified"
            reasons.append(
                "The full bundle without this skill was not measured, so its marginal "
                "value is unknown."
            )
        elif skill.id in selected_ids and meaningful(delta):
            verdict = "essential"
            reasons.append(f"Removing it reduced the full-bundle score by {delta:.1%}.")
        elif skill.id in selected_ids:
            verdict = "useful"
            reasons.append("Included in the smallest bundle within the score tolerance.")
        elif meaningful(delta):
            # Evidence conflicts: dropping it from the full bundle cost more than the noise
            # floor, yet a smaller bundle without it scored within noise of the best one.
            verdict = "contested"
            reasons.append(
                f"Removing it from the full bundle cost {delta:.1%}, but a smaller bundle "
                "without it scored within the noise floor of the best, so the evidence conflicts."
            )
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
            reasons.append(
                "No bundle beat the no-skill baseline beyond the noise floor, so this skill "
                "has not been shown to add lift."
            )
        elif delta is None:
            verdict = "unverified"
            reasons.append(
                "The full bundle without this skill was not measured, so it cannot be called "
                "redundant."
            )
        else:
            verdict = "redundant"
            reasons.append(
                f"Removing it from the full bundle changed the score by {delta:+.1%}, within "
                "the noise floor, and the smallest near-best bundle does not need it."
            )
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

    decoy_report = None
    flagged: list[str] = []
    if decoy_skill:
        sessions = [item for item in completed if decoy_skill.id in item.available_skills]
        fired = [item for item in sessions if decoy_skill.id in item.selected_skills]
        kinds = [labels.get(item.prompt_id, _UNKNOWN).kind for item in fired]
        lifts = []
        alone = score_by_skills.get(frozenset({decoy_skill.id}))
        if alone is not None and baseline_score is not None:
            lifts.append(alone - baseline_score)
        with_full = score_by_skills.get(full_ids | {decoy_skill.id})
        if with_full is not None and full_score is not None:
            lifts.append(with_full - full_score)
        advantage = max(lifts) if lifts else None
        if advantage is not None and baseline_score is not None:
            # A bundle "matches" the decoy when it leads no skills by more than the noise
            # floor (so it would be recommended) but by no more than a useless skill did.
            floor = effective_tolerance + EPSILON
            for config in measured:
                lead = scores[config.id] - baseline_score
                if floor < lead <= advantage + floor:
                    flagged.append(config.id)
        decoy_report = DecoyReport(
            name=decoy_skill.name,
            sessions=len(sessions),
            fired=len(fired),
            fire_rate=round(len(fired) / len(sessions), 4) if sessions else None,
            fired_on_negative=kinds.count(NEGATIVE),
            fired_on_positive=kinds.count(POSITIVE),
            advantage=_round(advantage),
            flagged_configs=flagged,
            recommended_matches=bool(recommended and recommended.id in flagged),
        )

    names = {item.id: item.name for item in plan.candidates}
    config_scores = (
        []
        if insufficient
        else [
            ConfigScore(
                id=config.id,
                kind=config.kind,
                skill_names=[names.get(item, item) for item in config.skill_ids],
                positive_score=_round(positive_scores[config.id]),
                negative_score=_round(negative_scores[config.id]),
                score=_round(scores[config.id]),
                accuracy=_round(
                    sum(correct for _, _, correct in judged[config.id]) / len(judged[config.id])
                    if judged[config.id]
                    else None
                ),
                positive_prompts=positive_counts[config.id],
                negative_prompts=negative_counts[config.id],
                matches_decoy=config.id in flagged,
            )
            for config in plan.configurations
        ]
    )
    best_config = (
        min(
            (item for item in measured if scores[item.id] >= best_score - EPSILON),
            key=lambda item: (len(item.skill_ids), item.id),
        ).id
        if best_score is not None and not insufficient
        else ""
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
    marginal = [item.name for item in verdicts if item.verdict == "marginal"]
    if marginal:
        limitations.append(
            "Marginal skills in the recommended bundle (" + ", ".join(marginal) + ") fired on "
            f"under {MARGINAL_RECALL:.0%} of the prompts that expected them; the bundle's score "
            "may come from other skills firing less rather than from their own activations."
        )
    if decoy_report and decoy_report.fired:
        limitations.append(
            f"The decoy skill ({decoy_report.name}) has a useless body, yet it was loaded in "
            f"{decoy_report.fired} of {decoy_report.sessions} sessions where it was listed "
            f"({decoy_report.fire_rate:.0%}): a plausible description alone can pull a model in."
        )
    if decoy_report and decoy_report.recommended_matches:
        limitations.append(
            "The recommended bundle's lead over no skills is no larger than the "
            f"{_pct(decoy_report.advantage)} a useless decoy skill produced, so it may not "
            "come from what the skills contain."
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
    if positive_n and negative_n:
        limitations.append(
            f"Each configuration was scored on {positive_n} positive and {negative_n} negative "
            f"labeled prompts. One positive prompt moves the balanced score by "
            f"{50 / positive_n:.1f} points and one negative prompt by {50 / negative_n:.1f}; a "
            "difference of one prompt is treated as a tie, and smaller differences are not "
            "statistically significant."
        )
    elif positive_n or negative_n:
        kind = "positive" if positive_n else "negative"
        limitations.append(
            f"Only {kind} prompts are labeled, so the score is not balanced and silence "
            "cannot be told apart from being right."
        )
    prompts_by_id = {item.id: item for item in plan.prompts}
    configs_by_id = {item.id: item for item in plan.configurations}
    candidates_by_id = {item.id: item for item in plan.candidates}
    expected_cost = worst_cost = 0.0
    try:
        for result in results:
            prompt = prompts_by_id.get(result.prompt_id)
            config = configs_by_id.get(result.config_id)
            if prompt is None or config is None:
                continue
            estimate = estimate_session(
                plan.model,
                plan.project,
                prompt,
                [candidates_by_id[item] for item in config.skill_ids if item in candidates_by_id],
            )
            expected_cost += estimate.expected_usd
            worst_cost += estimate.worst_usd
    except ValueError:  # a model with no configured pricing
        expected_cost = worst_cost = 0.0
    recommended_score = scores.get(recommended.id) if recommended else None
    headline = None if insufficient else best_score
    return Recommendation(
        run_id=plan.run_id,
        recommended_config=recommended.id if recommended else "",
        recommended_skill_ids=recommended.skill_ids if recommended else [],
        recommended_count=len(recommended.skill_ids) if recommended else 0,
        best_score=_round(headline),
        best_config=best_config,
        recommended_score=_round(recommended_score),
        no_skills_recommended=no_skills_wins,
        insufficient_evidence=insufficient,
        inconclusive=inconclusive,
        effective_tolerance=round(effective_tolerance, 4),
        noise_step=None if insufficient or not noise_step else round(noise_step, 4),
        expected_cost_usd=round(expected_cost, 6),
        worst_case_cost_usd=round(worst_cost, 6),
        baseline_score=None if insufficient else _round(baseline_score),
        scored_prompts_per_config=scored_per_config,
        positive_prompts_per_config=positive_n,
        negative_prompts_per_config=negative_n,
        tolerance=tolerance,
        total_cost_usd=round(sum(item.cost_usd for item in results), 6),
        completed_sessions=len(completed),
        verdicts=verdicts,
        config_scores=config_scores,
        decoy=decoy_report,
        labeling=labeling,
        limitations=limitations,
    )
