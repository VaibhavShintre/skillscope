from __future__ import annotations

import html
from pathlib import Path

from skills_evaluator.models import Recommendation, RunPlan


def markdown_report(plan: RunPlan, recommendation: Recommendation) -> str:
    names = {item.id: item.name for item in plan.candidates}
    bundle = [names.get(item, item) for item in recommendation.recommended_skill_ids]
    lines = [
        f"# Skills Evaluator report: {plan.project.name}",
        "",
        f"- Recommended number of skills: **{recommendation.recommended_count}**",
        f"- Recommended bundle: **{', '.join(bundle) if bundle else 'No skills'}**",
        f"- Recommended score: **{recommendation.recommended_score:.1%}**",
        f"- Best observed score: **{recommendation.best_score:.1%}**",
        f"- Completed sessions: **{recommendation.completed_sessions}/{plan.planned_sessions}**",
        f"- Recorded cost: **${recommendation.total_cost_usd:.4f}**",
        "",
        "## Skill verdicts",
        "",
        "| Skill | Verdict | Precision | Recall | Singleton score | Leave-one-out delta |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for item in recommendation.verdicts:
        lines.append(
            f"| {item.name} | {item.verdict} | {item.precision:.1%} | {item.recall:.1%} | "
            f"{item.singleton_score:.1%} | {item.leave_one_out_delta:+.1%} |"
        )
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
