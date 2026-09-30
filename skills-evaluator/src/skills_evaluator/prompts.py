from __future__ import annotations

import re
from pathlib import Path

import yaml

from skills_evaluator.labels import skill_categories
from skills_evaluator.models import EvalPrompt, ProjectProfile, SkillCandidate

# Questions that share a skill category's vocabulary but need no skill: an answer from
# general knowledge. They never mention skills, so a skill that fires is a real false positive.
NEAR_MISS_PROMPTS = {
    "testing": "In two sentences, explain the difference between a unit test and an "
    "integration test. Answer from general knowledge; nothing needs to be run or changed.",
    "frontend": "In two sentences, explain the difference between CSS grid and flexbox. "
    "Answer from general knowledge; nothing needs to be designed or built.",
    "backend": "In two sentences, explain what an idempotent HTTP endpoint is. "
    "Answer from general knowledge; nothing in the project needs to change.",
    "devops": "In two sentences, explain what a blue-green deployment is. "
    "Answer from general knowledge; nothing needs to be deployed.",
    "docs": "In two sentences, explain the difference between a tutorial and a how-to "
    "guide. Answer from general knowledge; no document needs to be written.",
    "security": "In two sentences, explain what a CSRF attack is. "
    "Answer from general knowledge; nothing needs to be reviewed or fixed.",
}


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
    categories = sorted({item for skill in candidates for item in skill_categories(skill)})
    for category in categories:
        if category in NEAR_MISS_PROMPTS:
            prompts.append(
                EvalPrompt(
                    id=f"near-miss-{category}",
                    text=NEAR_MISS_PROMPTS[category],
                    origin="near-miss",
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


LABEL_FIELDS = ("label", "expected_skill", "expected_skills")


def load_prompt_file(
    path: Path, candidates: list[SkillCandidate]
) -> tuple[list[EvalPrompt], dict[str, dict[str, object]]]:
    """Read the user prompt YAML.

    Entries with `text` are new prompts. An entry with only an `id` of a generated prompt plus
    label fields overrides that prompt's label, and beats the capability heuristic.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("User prompt file must contain a YAML list.")
    by_name = {item.name: item.id for item in candidates}
    by_id = {item.id: item.id for item in candidates}

    def resolve(value: object, index: int) -> str:
        resolved = by_id.get(str(value)) or by_name.get(str(value))
        if not resolved:
            raise ValueError(f"User prompt {index} names unknown skill {value!r}.")
        return resolved

    prompts: list[EvalPrompt] = []
    overrides: dict[str, dict[str, object]] = {}
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"User prompt {index} must be an object.")
        data = dict(item)
        if data.get("expected_skill"):
            data["expected_skill"] = resolve(data["expected_skill"], index)
        if data.get("expected_skills"):
            data["expected_skills"] = [resolve(value, index) for value in data["expected_skills"]]
        if "text" not in data:
            fields = {key: data[key] for key in LABEL_FIELDS if data.get(key)}
            if not data.get("id") or not fields:
                raise ValueError(
                    f"User prompt {index} needs `text`, or the `id` of a generated prompt "
                    "plus label fields."
                )
            overrides[str(data["id"])] = fields
            continue
        data["origin"] = "user"
        if data.get("label"):
            data["label_source"] = "user"
        prompts.append(EvalPrompt.model_validate(data))
    ids = [item.id for item in prompts]
    if len(ids) != len(set(ids)):
        raise ValueError("User prompt IDs must be unique.")
    return prompts, overrides


def apply_label_overrides(
    prompts: list[EvalPrompt], overrides: dict[str, dict[str, object]]
) -> list[EvalPrompt]:
    by_id = {item.id: item for item in prompts}
    for prompt_id, fields in overrides.items():
        target = by_id.get(prompt_id)
        if target is None:
            raise ValueError(f"Label override names unknown prompt {prompt_id!r}.")
        data = target.model_dump()
        data.update({"expected_skill": None, "expected_skills": []})
        data.update(fields)
        if not data.get("label"):
            data["label"] = "positive"
        data["label_source"] = "user"
        by_id[prompt_id] = EvalPrompt.model_validate(data)
    return [by_id[item.id] for item in prompts]
