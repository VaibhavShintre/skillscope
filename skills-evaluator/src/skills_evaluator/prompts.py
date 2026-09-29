from __future__ import annotations

import re
from pathlib import Path

import yaml

from skills_evaluator.models import EvalPrompt, ProjectProfile, SkillCandidate


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:54]


def generate_prompts(
    profile: ProjectProfile, candidates: list[SkillCandidate]
) -> list[EvalPrompt]:
    prompts: list[EvalPrompt] = []
    for index, skill in enumerate(candidates, start=1):
        prompts.append(
            EvalPrompt(
                id=f"skill-{index:02d}-{_slug(skill.name)}",
                text=(
                    f"For the {profile.name} project ({profile.summary}), propose a concrete, "
                    f"project-specific approach for this task: {skill.description}"
                ),
                expected_skill=skill.id,
                origin="skill-positive",
            )
        )
    for index, capability in enumerate(profile.capabilities[:8], start=1):
        prompts.append(
            EvalPrompt(
                id=f"project-{index:02d}-{_slug(capability)}",
                text=(
                    f"Review the {profile.name} project and recommend the next implementation "
                    f"step for its {capability} requirements. Be specific to the detected stack."
                ),
                origin="project",
                capability=capability,
            )
        )
    prompts.extend(
        [
            EvalPrompt(
                id="control-general-summary",
                text=(
                    "Summarize the project profile in three concise bullets without using a skill."
                ),
                origin="control",
            ),
            EvalPrompt(
                id="control-unrelated-recipe",
                text="Give a short recipe for vegetable soup. Do not use a development skill.",
                origin="control",
            ),
        ]
    )
    return prompts


def load_user_prompts(path: Path, candidates: list[SkillCandidate]) -> list[EvalPrompt]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("User prompt file must contain a YAML list.")
    by_name = {item.name: item.id for item in candidates}
    by_id = {item.id: item.id for item in candidates}
    prompts: list[EvalPrompt] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"User prompt {index} must be an object.")
        data = dict(item)
        expected = data.get("expected_skill")
        if expected:
            resolved = by_id.get(str(expected)) or by_name.get(str(expected))
            if not resolved:
                raise ValueError(f"User prompt {index} names unknown skill {expected!r}.")
            data["expected_skill"] = resolved
        data["origin"] = "user"
        prompts.append(EvalPrompt.model_validate(data))
    ids = [item.id for item in prompts]
    if len(ids) != len(set(ids)):
        raise ValueError("User prompt IDs must be unique.")
    return prompts
