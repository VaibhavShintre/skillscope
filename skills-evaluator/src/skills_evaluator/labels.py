"""Decide what each prompt should make a skill do, so scoring never treats unknown as correct."""

from __future__ import annotations

from dataclasses import dataclass

from skills_evaluator.models import EvalPrompt, LabelSummary, SkillCandidate

POSITIVE, NEGATIVE, UNLABELED = "positive", "negative", "unlabeled"

# Project capability (from the profiler) -> the skill categories that should handle it.
# A capability that is missing here is one we cannot judge confidently, so it stays
# unlabeled. "accessibility" is left out on purpose: it straddles frontend and testing.
CAPABILITY_CATEGORIES: dict[str, frozenset[str]] = {
    "testing": frozenset({"testing"}),
    "frontend": frozenset({"frontend"}),
    "api": frozenset({"backend"}),
    "database": frozenset({"backend"}),
    "authentication": frozenset({"backend", "security"}),
    "security": frozenset({"security"}),
    "deployment": frozenset({"devops"}),
    "documentation": frozenset({"docs"}),
}

# Categories for well-known skills that do not declare `category:` in their frontmatter: the
# 19 packages in Anthropic's public skills repository (anthropics/skills). Assigned by hand from
# what each package is for. Only frontend, testing and docs line up with a project capability;
# the rest never match one, so on capability prompts they are judged as "should not fire".
# Frontmatter `category:` overrides this table, and it is keyed by skill name alone.
SKILL_CATEGORIES_BY_NAME: dict[str, frozenset[str]] = {
    "academy-guide": frozenset({"learning"}),
    "algorithmic-art": frozenset({"creative"}),
    "brand-guidelines": frozenset({"creative"}),
    "canvas-design": frozenset({"creative"}),
    "claude-api": frozenset({"ai"}),
    "discernment-nudge": frozenset({"meta"}),
    "doc-coauthoring": frozenset({"docs"}),
    "docx": frozenset({"office"}),
    "frontend-design": frozenset({"frontend"}),
    "internal-comms": frozenset({"comms"}),
    "mcp-builder": frozenset({"mcp"}),
    "pdf": frozenset({"office"}),
    "pptx": frozenset({"office"}),
    "skill-creator": frozenset({"meta"}),
    "slack-gif-creator": frozenset({"creative"}),
    "theme-factory": frozenset({"creative"}),
    "web-artifacts-builder": frozenset({"frontend"}),
    "webapp-testing": frozenset({"testing"}),
    "xlsx": frozenset({"office"}),
    # Not from that repository: the test fixture skill.
    "browser-testing": frozenset({"testing"}),
}


@dataclass(frozen=True)
class Label:
    kind: str
    expected: frozenset[str]
    source: str  # "user", "rule" or "heuristic"
    # Skills whose behavior on this prompt is not judged: they declare no category, so the
    # capability heuristic cannot say whether they should fire. Other skills are still judged.
    unknown: frozenset[str] = frozenset()


def skill_categories(skill: SkillCandidate) -> frozenset[str]:
    if skill.categories:
        return frozenset(skill.categories)
    return SKILL_CATEGORIES_BY_NAME.get(skill.name, frozenset())


def expected_ids(prompt: EvalPrompt) -> frozenset[str]:
    ids = set(prompt.expected_skills)
    if prompt.expected_skill:
        ids.add(prompt.expected_skill)
    return frozenset(ids)


def _heuristic(prompt: EvalPrompt, candidates: list[SkillCandidate]) -> Label:
    wanted = CAPABILITY_CATEGORIES.get(prompt.capability or "")
    categories = {item.id: skill_categories(item) for item in candidates if not item.blocked}
    known = {skill_id for skill_id, value in categories.items() if value}
    if not wanted or not known:
        return Label(UNLABELED, frozenset(), "heuristic")
    # A skill without a category only loses its own label: it is left out of judgment for
    # this prompt, while every skill with a category is still judged.
    unknown = frozenset(categories) - known
    matching = frozenset(skill_id for skill_id in known if categories[skill_id] & wanted)
    return Label(POSITIVE if matching else NEGATIVE, matching, "heuristic", unknown)


def resolve_label(prompt: EvalPrompt, candidates: list[SkillCandidate]) -> Label:
    if prompt.label_source != "heuristic":
        if prompt.label:
            expected = expected_ids(prompt) if prompt.label == POSITIVE else frozenset()
            return Label(prompt.label, expected, prompt.label_source or "user")
        expected = expected_ids(prompt)
        if expected:
            return Label(POSITIVE, expected, "rule")
        if prompt.origin in {"control", "near-miss"}:
            return Label(NEGATIVE, frozenset(), "rule")
    # A stored heuristic label is only a cache of an earlier rule set, so derive it again.
    return _heuristic(prompt, candidates)


def stamp_labels(prompts: list[EvalPrompt], candidates: list[SkillCandidate]) -> list[EvalPrompt]:
    """Record the resolved label on each prompt so a run is self-describing."""
    stamped: list[EvalPrompt] = []
    for prompt in prompts:
        if prompt.label:
            stamped.append(prompt)
            continue
        label = resolve_label(prompt, candidates)
        update: dict[str, object] = {"label": label.kind, "label_source": label.source}
        if label.kind == POSITIVE and not prompt.expected_skill:
            update["expected_skills"] = sorted(label.expected)
        stamped.append(prompt.model_copy(update=update))
    return stamped


def summarize_labels(prompts: list[EvalPrompt], candidates: list[SkillCandidate]) -> LabelSummary:
    labels = {prompt.id: resolve_label(prompt, candidates) for prompt in prompts}
    unlabeled = [prompt_id for prompt_id, label in labels.items() if label.kind == UNLABELED]
    return LabelSummary(
        positive_prompts=sum(label.kind == POSITIVE for label in labels.values()),
        negative_prompts=sum(label.kind == NEGATIVE for label in labels.values()),
        unlabeled_prompts=len(unlabeled),
        unlabeled_prompt_ids=unlabeled,
        uncategorized_skills=[
            item.name for item in candidates if not item.blocked and not skill_categories(item)
        ],
    )
