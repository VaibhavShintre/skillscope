from __future__ import annotations

import html
from pathlib import Path

from skills_evaluator.models import Recommendation, RunPlan


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def markdown_report(plan: RunPlan, recommendation: Recommendation) -> str:
    names = {item.id: item.name for item in plan.candidates}
    bundle = [names.get(item, item) for item in recommendation.recommended_skill_ids]
    lines = [f"# Skills Evaluator report: {plan.project.name}", ""]
    if recommendation.no_skills_recommended:
        lines.extend(
            [
                "> **Recommendation: use no skills.** The no-skill baseline scored "
                f"{_pct(recommendation.baseline_score)}, at least as high as the best skill "
                f"bundle ({_pct(recommendation.best_score)}, tolerance "
                f"{recommendation.tolerance:.0%}). None of the tested bundles improved on "
                "having no skills.",
                "",
            ]
        )
    lines += [
        f"- Recommended number of skills: **{recommendation.recommended_count}**",
        f"- Recommended bundle: **{', '.join(bundle) if bundle else 'No skills'}**",
        f"- Recommended score: **{_pct(recommendation.recommended_score)}**",
        f"- No-skill baseline score: **{_pct(recommendation.baseline_score)}**",
        f"- Best observed score: **{_pct(recommendation.best_score)}**",
        f"- Completed sessions: **{recommendation.completed_sessions}/{plan.planned_sessions}**",
        f"- Recorded cost: **${recommendation.total_cost_usd:.4f}**",
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
        "| Skill | Verdict | Precision | Recall | Singleton score | Leave-one-out delta |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in recommendation.verdicts:
        delta = "n/a" if item.leave_one_out_delta is None else f"{item.leave_one_out_delta:+.1%}"
        lines.append(
            f"| {item.name} | {item.verdict} | {_pct(item.precision)} | {_pct(item.recall)} | "
            f"{_pct(item.singleton_score)} | {delta} |"
        )
    if recommendation.labeling.unlabeled_prompt_ids:
        lines.extend(["", "## Unlabeled prompts", ""])
        lines.extend(f"- {item}" for item in recommendation.labeling.unlabeled_prompt_ids)
    lines.extend(["", "## Limitations", ""])
    lines.extend(f"- {item}" for item in recommendation.limitations)
    return "\n".join(lines) + "\n"


def write_reports(run_dir: Path, plan: RunPlan, recommendation: Recommendation) -> None:
    markdown = markdown_report(plan, recommendation)
    (run_dir / "recommendation.json").write_text(
        recommendation.model_dump_json(indent=2), encoding="utf-8"
    )
    (run_dir / "report.md").write_text(markdown, encoding="utf-8")
    escaped = html.escape(markdown)
    document = (
        "<!doctype html><meta charset='utf-8'><title>Skills Evaluator report</title>"
        "<style>body{font:15px/1.55 system-ui;max-width:1000px;margin:40px auto;padding:0 20px;"
        "color:#17231f;background:#f7f5ee}pre{white-space:pre-wrap;background:white;padding:24px;"
        "border:1px solid #d9ddd8;border-radius:14px}</style>"
        f"<pre>{escaped}</pre>"
    )
    (run_dir / "report.html").write_text(document, encoding="utf-8")
