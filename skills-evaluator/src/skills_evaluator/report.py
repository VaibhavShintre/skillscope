from __future__ import annotations

import html
from pathlib import Path

from skills_evaluator.advice import build_advice
from skills_evaluator.models import Advice, ConfigScore, Recommendation, RunPlan


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _cost_line(recommendation: Recommendation) -> str:
    actual = recommendation.total_cost_usd
    line = (
        f"- Cost: **actual ${actual:.4f}**; expected ${recommendation.expected_cost_usd:.4f}; "
        f"worst-case bound ${recommendation.worst_case_cost_usd:.4f}"
    )
    if actual and recommendation.expected_cost_usd and recommendation.worst_case_cost_usd:
        line += (
            f" (actual was {actual / recommendation.expected_cost_usd:.0%} of expected and "
            f"{actual / recommendation.worst_case_cost_usd:.0%} of the bound)"
        )
    return line


def _breakdown(scores: dict[str, ConfigScore], config_id: str) -> str:
    """' (positive 33.3%, negative 100.0%)' for a configuration, or '' when it has none."""
    entry = scores.get(config_id)
    if entry is None:
        return ""
    return f" (positive {_pct(entry.positive_score)}, negative {_pct(entry.negative_score)})"


def _decoy_lines(recommendation: Recommendation) -> list[str]:
    decoy = recommendation.decoy
    if decoy is None:
        return []
    lines = ["", "## Decoy control", ""]
    lines.append(
        f"- Decoy skill: **{decoy.name}** (a plausible description over a body that says "
        "nothing useful). It is never recommended; it only measures how much listing a skill "
        "can matter."
    )
    if decoy.sessions:
        lines.append(
            f"- Fired in **{decoy.fired} of {decoy.sessions}** sessions where it was listed "
            f"({_pct(decoy.fire_rate)}): {decoy.fired_on_negative} on negative prompts and "
            f"{decoy.fired_on_positive} on positive prompts. Every firing is a false positive."
        )
    else:
        lines.append("- It was not listed in any completed session.")
    if decoy.advantage is None:
        lines.append(
            "- Score lift from listing it: **not measured** (its configurations did not run)."
        )
    else:
        lines.append(
            f"- Most that listing a useless skill lifted a score: **{decoy.advantage:+.1%}** "
            "(the decoy alone against no skills, or the full bundle with the decoy against the "
            "full bundle)."
        )
    if decoy.flagged_configs:
        lines.append(
            "- Bundles whose lead over no skills is no larger than that: "
            + ", ".join(decoy.flagged_configs)
            + "."
        )
    else:
        lines.append("- No bundle's lead over no skills is within the decoy's.")
    return lines


def _advice_markdown(advice: Advice) -> list[str]:
    lines = ["## What to do", "", f"**{advice.headline}**", ""]
    for title, items in (("Keep", advice.keep), ("Drop", advice.drop)):
        if items:
            lines += [f"{title}:", *[f"- {item}" for item in items], ""]
    for item in advice.cautions:
        lines += [f"Caution: {item}", ""]
    if advice.steps:
        lines += ["Steps:", *[f"{index}. {item}" for index, item in enumerate(advice.steps, 1)], ""]
    return lines


def _advice_html(advice: Advice) -> str:
    def items(values: list[str]) -> str:
        return "".join(f"<li>{html.escape(value)}</li>" for value in values)

    parts = [
        "<section class='todo'><h2>What to do</h2>",
        f"<p class='headline'>{html.escape(advice.headline)}</p>",
    ]
    for title, values in (("Keep", advice.keep), ("Drop", advice.drop)):
        if values:
            parts.append(f"<h3>{title}</h3><ul>{items(values)}</ul>")
    parts.extend(f"<p class='caution'>Caution: {html.escape(text)}</p>" for text in advice.cautions)
    if advice.steps:
        parts.append(f"<h3>Steps</h3><ol>{items(advice.steps)}</ol>")
    parts.append("</section>")
    return "".join(parts)


def markdown_report(
    plan: RunPlan, recommendation: Recommendation, include_advice: bool = True
) -> str:
    names = {item.id: item.name for item in plan.candidates}
    auto = {item.id for item in plan.candidates if not item.user_requested}
    scores = {item.id: item for item in recommendation.config_scores}
    bundle = [
        names.get(item, item) + (" (auto-discovered)" if item in auto else "")
        for item in recommendation.recommended_skill_ids
    ]
    lines = [f"# Skills Evaluator report: {plan.project.name}", ""]
    if include_advice:
        lines += _advice_markdown(recommendation.what_to_do or build_advice(plan, recommendation))
    if recommendation.insufficient_evidence:
        labeling = recommendation.labeling
        lines.extend(
            [
                "> **Insufficient labeled evidence: no recommendation is issued.** "
                f"{labeling.unlabeled_prompts} of {len(plan.prompts)} prompts are unlabeled, "
                "more than half, so any score would describe a minority of the prompts. "
                "Label them in the prompts YAML or add skill categories, then re-run the "
                "analysis. Scores and per-skill numbers are withheld.",
                "",
            ]
        )
    if recommendation.no_skills_recommended and recommendation.inconclusive:
        step = recommendation.noise_step
        gap = abs((recommendation.best_score or 0) - (recommendation.baseline_score or 0))
        lines.extend(
            [
                "> **Inconclusive — gap within noise.** The best skill bundle scored "
                f"{_pct(recommendation.best_score)} against {_pct(recommendation.baseline_score)} "
                f"for no skills, a gap of {gap * 100:.1f} points, within the noise floor of "
                f"{_pct(recommendation.effective_tolerance)}"
                + (f" (one prompt = up to {step * 100:.1f} points)" if step else "")
                + ". No skills is listed only because it is the simplest option; this run "
                "cannot show that skills help or hurt.",
                "",
            ]
        )
    elif recommendation.no_skills_recommended:
        lines.extend(
            [
                "> **Recommendation: use no skills.** The no-skill baseline scored "
                f"{_pct(recommendation.baseline_score)}, at least as high as the best skill "
                f"bundle ({_pct(recommendation.best_score)}, noise tolerance "
                f"{_pct(recommendation.effective_tolerance)}). None of the tested bundles "
                "improved on having no skills.",
                "",
            ]
        )
    if recommendation.decoy and recommendation.decoy.recommended_matches:
        lines.extend(
            [
                "> **Decoy warning.** The recommended bundle's lead over no skills is no "
                f"larger than the {_pct(recommendation.decoy.advantage)} that listing a useless "
                "decoy skill produced, so it may not come from what the skills contain.",
                "",
            ]
        )
    best_names = (
        ", ".join(scores[recommendation.best_config].skill_names)
        if recommendation.best_config in scores
        else ""
    )
    lines += [
        f"- Recommended number of skills: **{recommendation.recommended_count}**",
        "- Recommended bundle: **"
        + (
            "none (insufficient labeled evidence)"
            if recommendation.insufficient_evidence
            else "No skills (inconclusive — gap within noise)"
            if recommendation.inconclusive
            else ", ".join(bundle) or "No skills"
        )
        + "**",
        "- Scores are balanced: the average of the positive-prompt score and the "
        "negative-prompt score, so a configuration that never fires scores 50%.",
        f"- Recommended score: **{_pct(recommendation.recommended_score)}**"
        + _breakdown(scores, recommendation.recommended_config),
        f"- No-skill baseline score: **{_pct(recommendation.baseline_score)}**"
        + _breakdown(scores, "baseline"),
        f"- Best observed score: **{_pct(recommendation.best_score)}**"
        + _breakdown(scores, recommendation.best_config)
        + (f" [{best_names}]" if best_names else ""),
        f"- Completed sessions: **{recommendation.completed_sessions}/{plan.planned_sessions}**",
        _cost_line(recommendation),
        f"- Noise tolerance: **{_pct(recommendation.effective_tolerance)}**",
        f"- Prompts: **{recommendation.labeling.positive_prompts} positive, "
        f"{recommendation.labeling.negative_prompts} negative, "
        f"{recommendation.labeling.unlabeled_prompts} unlabeled** "
        f"({recommendation.labeling.unlabeled_sessions} sessions excluded from score and "
        "precision)",
        "",
        "## Skill verdicts",
        "",
        "Precision is measured in the full bundle.",
        "",
    ]
    if any(item.auto_discovered for item in recommendation.verdicts):
        lines.extend(
            [
                "Auto-discovered skills were added from a public catalog; you did not pass "
                "them with `--skill`. Pass `--offline` to evaluate only the skills you name.",
                "",
            ]
        )
    lines += [
        "| Skill | Origin | Verdict | Precision | Recall | Singleton score | "
        "Leave-one-out delta (full bundle) | Delta in recommended bundle |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in recommendation.verdicts:
        delta = "n/a" if item.leave_one_out_delta is None else f"{item.leave_one_out_delta:+.1%}"
        inside = "n/a" if item.bundle_delta is None else f"{item.bundle_delta:+.1%}"
        origin = "auto-discovered" if item.auto_discovered else "requested"
        lines.append(
            f"| {item.name} | {origin} | {item.verdict} | {_pct(item.precision)} | "
            f"{_pct(item.recall)} | {_pct(item.singleton_score)} | {delta} | {inside} |"
        )
    if recommendation.config_scores:
        lines += [
            "",
            "## Scores by configuration",
            "",
            "| Configuration | Skills | Positive | Negative | Balanced | Plain accuracy | Note |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for entry in recommendation.config_scores:
            note = "matches the decoy's lead" if entry.matches_decoy else ""
            lines.append(
                f"| {entry.id} | {', '.join(entry.skill_names) or '(none)'} | "
                f"{_pct(entry.positive_score)} | {_pct(entry.negative_score)} | "
                f"{_pct(entry.score)} | {_pct(entry.accuracy)} | {note} |"
            )
    lines.extend(_decoy_lines(recommendation))
    if recommendation.labeling.unlabeled_prompt_ids:
        lines.extend(["", "## Unlabeled prompts", ""])
        lines.extend(f"- {item}" for item in recommendation.labeling.unlabeled_prompt_ids)
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in recommendation.limitations)
    return "\n".join(lines) + "\n"


def write_reports(run_dir: Path, plan: RunPlan, recommendation: Recommendation) -> None:
    advice = build_advice(plan, recommendation, run_dir.resolve())
    recommendation.what_to_do = advice
    markdown = markdown_report(plan, recommendation)
    (run_dir / "recommendation.json").write_text(
        recommendation.model_dump_json(indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    escaped = html.escape(markdown_report(plan, recommendation, include_advice=False))
    document = (
        "<!doctype html><meta charset='utf-8'><title>Skills Evaluator report</title>"
        "<style>body{font:15px/1.55 system-ui;max-width:1000px;margin:40px auto;padding:0 20px;"
        "color:#17231f;background:#f7f5ee}pre{white-space:pre-wrap;background:white;padding:24px;"
        "border:1px solid #d9ddd8;border-radius:14px}"
        ".todo{background:white;border:1px solid #d9ddd8;border-left:6px solid #2f6f4f;"
        "border-radius:14px;padding:8px 24px 16px;margin-bottom:20px}"
        ".todo h2{margin:12px 0 4px}.todo h3{margin:14px 0 2px;font-size:1em}"
        ".todo .headline{font-size:1.25em;font-weight:600;margin:4px 0 8px}"
        ".todo .caution{color:#8a4b00}.todo ul,.todo ol{margin:2px 0}</style>"
        f"{_advice_html(advice)}<pre>{escaped}</pre>"
    )
    (run_dir / "report.html").write_text(document, encoding="utf-8")
