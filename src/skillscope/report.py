from __future__ import annotations

from skillscope.models import AnalysisResult


def markdown_report(result: AnalysisResult) -> str:
    selected = [item for item in result.recommendations if item.selected]
    lines = [
        f"# SkillScope recommendation: {result.project.name}",
        "",
        f"Generated: {result.created_at.isoformat()}",
        f"Posture: {result.project.posture.value}",
        f"Selected: {result.selected_count} of {result.candidate_count} candidates",
        f"Weighted capability coverage: {result.weighted_coverage:.1f}%",
        "",
        f"> {result.evidence_notice}",
        "",
        "## Recommended catalog",
        "",
        "| Rank | Skill | Tier | Importance | Confidence |",
        "| ---: | --- | --- | ---: | --- |",
    ]
    for item in selected:
        lines.append(
            f"| {item.rank} | {item.name} | {item.tier} | "
            f"{item.importance_score:.1f} | {item.confidence} |"
        )
    lines.extend(("", "## Reasoning", ""))
    for item in selected:
        lines.extend((f"### {item.rank}. {item.name}", ""))
        if item.source_url:
            lines.append(f"Source: [{item.publisher or 'Publisher'}]({item.source_url})")
            lines.append("")
        lines.extend(f"- {reason}" for reason in item.reasons)
        lines.extend(f"- Caution: {caution}" for caution in item.cautions)
        lines.append("")
    lines.extend(("## Detected capabilities", ""))
    for capability in result.capabilities:
        lines.append(f"- **{capability.name}** (weight {capability.weight}/5)")
    if result.gaps:
        lines.extend(("", "## Coverage gaps", ""))
        lines.extend(f"- {gap}" for gap in result.gaps)
    return "\n".join(lines) + "\n"
