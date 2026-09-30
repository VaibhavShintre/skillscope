"""The short, plain-language "What to do" section of a report.

Built only from facts the analysis already measured. When the result is inconclusive, "use no
skills", or there is too little labeled evidence, it says so and gives no advice.
"""

from __future__ import annotations

from pathlib import Path

from skills_evaluator.models import Advice, Recommendation, RunPlan, SkillCandidate, SkillVerdict

MAX_LINES = 5


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _cap(lines: list[str]) -> list[str]:
    if len(lines) <= MAX_LINES:
        return lines
    return [*lines[:MAX_LINES], f"...and {len(lines) - MAX_LINES} more (see the table below)"]


def _keep_line(verdict: SkillVerdict) -> str:
    helped = f"helped with {_join(verdict.helped_with)}" if verdict.helped_with else ""
    if verdict.verdict == "marginal":
        recall = f"{verdict.recall:.0%}" if verdict.recall is not None else "few"
        weak = f"weak evidence, fired on only {recall} of the prompts that expected it"
        return f"{verdict.name} — {helped + '; ' if helped else ''}{weak}"
    if verdict.verdict == "contested":
        conflict = "the full bundle scores better without it"
        return f"{verdict.name} — {helped + ', but ' if helped else ''}{conflict}"
    return f"{verdict.name} — {helped or 'part of the smallest bundle that beat no skills'}"


def _drop_line(verdict: SkillVerdict, kept: list[str]) -> str:
    if verdict.verdict == "unsafe":
        reason = "blocked by the safety check"
    elif verdict.verdict == "harmful":
        reason = (
            f"harmful: over-fires on {_join(verdict.over_fires_on)}"
            if verdict.over_fires_on
            else "harmful: it lowers the score when listed with the others"
        )
    elif verdict.sessions and not verdict.fired:
        reason = "never fires"
    elif verdict.verdict == "conflicting":
        reason = "over-fires: mostly fires on the wrong prompts" + (
            f" ({_join(verdict.over_fires_on)})" if verdict.over_fires_on else ""
        )
    elif verdict.verdict == "irrelevant":
        reason = "never fires when it should"
    elif verdict.verdict == "redundant":
        reason = f"redundant with {_join(kept)}" if kept else "redundant"
    elif verdict.verdict == "no_lift":
        reason = "no better than using no skills"
    elif verdict.verdict == "contested":
        reason = "contested: it helps the full bundle, but a smaller bundle did as well"
    elif verdict.verdict == "unverified":
        reason = "not enough evidence to judge it"
    else:
        reason = verdict.verdict
    return f"{verdict.name} — {reason}"


def _steps(
    plan: RunPlan,
    recommendation: Recommendation,
    rejected: list[SkillVerdict],
    run_dir: Path | None,
) -> list[str]:
    by_id: dict[str, SkillCandidate] = {item.id: item for item in plan.candidates}
    root = plan.project.root
    chosen = [by_id[item] for item in recommendation.recommended_skill_ids if item in by_id]
    missing = [item for item in chosen if item.source != "project-installed"]
    steps: list[str] = []
    if missing and len(missing) == len(chosen):
        folder = f'"{run_dir}"' if run_dir else "<this run's folder>"
        steps.append(
            f'Install: skills-evaluator install {folder} --project "{root}" --apply '
            "(leave off --apply to preview)"
        )
    else:
        # The install command refuses to overwrite, so copy only what is missing.
        steps.extend(
            f'Install {item.name}: copy the folder "{item.source_path}" to '
            f'"{root}/.claude/skills/{item.name}"'
            for item in missing
        )
    for verdict in rejected:
        candidate = by_id.get(verdict.skill_id)
        if candidate and candidate.source == "project-installed":
            steps.append(f'Remove {candidate.name}: delete the folder "{candidate.source_path}"')
    return steps or ["No changes needed: the project already has exactly these skills."]


def build_advice(
    plan: RunPlan, recommendation: Recommendation, run_dir: Path | None = None
) -> Advice:
    if recommendation.insufficient_evidence:
        return Advice(
            status="insufficient",
            headline="No recommendation: too few prompts are labeled to judge the skills.",
        )
    if recommendation.no_skills_recommended and recommendation.inconclusive:
        return Advice(
            status="inconclusive",
            headline=(
                "Inconclusive: no skill bundle beat using no skills by more than the noise, "
                "so this run cannot say whether skills help or hurt."
            ),
        )
    if recommendation.no_skills_recommended:
        return Advice(
            status="no_skills",
            headline="Use no skills: none of the tested bundles beat having none.",
        )

    kept = [item for item in recommendation.verdicts if item.selected]
    rejected = [item for item in recommendation.verdicts if not item.selected]
    names = [item.name for item in kept]
    cautions = []
    decoy = recommendation.decoy
    if decoy and decoy.recommended_matches:
        cautions.append(
            "This bundle's lead over no skills is no larger than a useless decoy skill's, so it "
            "may not come from what these skills contain."
        )
    unstable, total = recommendation.unstable_cells, recommendation.total_cells
    if total and unstable * 4 >= total:
        cautions.append(
            f"{unstable} of {total} repeated results "
            "changed between repeats, so treat this as tentative or raise --repeats."
        )
    return Advice(
        status="install",
        headline=f"Install {_join(names)}." if names else "No skills to install.",
        keep=_cap([_keep_line(item) for item in kept]),
        drop=_cap([_drop_line(item, names) for item in rejected]),
        cautions=cautions,
        steps=_steps(plan, recommendation, rejected, run_dir),
    )
