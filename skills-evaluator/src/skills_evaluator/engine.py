from __future__ import annotations

import json
import os
import time
from typing import Protocol

from skills_evaluator.labels import expected_ids
from skills_evaluator.models import EvalPrompt, ProjectProfile, SessionResult, SkillCandidate


class EvaluationEngine(Protocol):
    def run(
        self,
        session_key: str,
        config_id: str,
        prompt: EvalPrompt,
        profile: ProjectProfile,
        skills: list[SkillCandidate],
    ) -> SessionResult: ...


def token_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    lowered = model.lower()
    if "haiku" in lowered:
        input_rate, output_rate = 1.0, 5.0
    elif "sonnet" in lowered:
        input_rate, output_rate = 3.0, 15.0
    elif "opus" in lowered:
        input_rate, output_rate = 15.0, 75.0
    else:
        raise ValueError(f"No pricing configured for model {model!r}")
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000


def session_reservation(model: str, max_output_tokens: int = 800) -> float:
    return token_cost(model, input_tokens=30_000, output_tokens=max_output_tokens)


class FakeEngine:
    """Deterministic engine for tests and the no-key demonstration."""

    def run(
        self,
        session_key: str,
        config_id: str,
        prompt: EvalPrompt,
        profile: ProjectProfile,
        skills: list[SkillCandidate],
    ) -> SessionResult:
        available = [item.id for item in skills]
        selected = [item for item in sorted(expected_ids(prompt)) if item in available]
        return SessionResult(
            session_key=session_key,
            config_id=config_id,
            prompt_id=prompt.id,
            prompt_origin=prompt.origin,
            expected_skill=prompt.expected_skill,
            available_skills=available,
            selected_skills=selected,
            answer=f"Fixture response for {profile.name}",
            input_tokens=100,
            output_tokens=20,
            duration_ms=1,
            outcome="completed",
        )


def system_prompt(profile: ProjectProfile, skills: list[SkillCandidate]) -> str:
    """The exact system prompt the API harness sends, including the skill listing."""
    metadata = [
        {"id": item.id, "name": item.name, "description": item.description} for item in skills
    ]
    return (
        "You are evaluating Agent Skills for a software project. Use load_skill only when "
        "a listed skill is materially relevant. Do not load a skill for unrelated or simple "
        "control requests. After loading any useful skill, answer the task concisely.\n\n"
        f"Project profile: {profile.summary}\nAvailable skill metadata: "
        f"{json.dumps(metadata, ensure_ascii=False)}"
    )


def tool_definitions(skills: list[SkillCandidate]) -> list[dict[str, object]]:
    if not skills:
        return []
    return [
        {
            "name": "load_skill",
            "description": "Load one available Agent Skill by its exact id.",
            "input_schema": {
                "type": "object",
                "properties": {"skill_id": {"type": "string"}},
                "required": ["skill_id"],
            },
        }
    ]


class AnthropicApiEngine:
    def __init__(self, model: str, max_tokens: int = 800) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as error:  # pragma: no cover - installation path
            raise RuntimeError("Install the Skills Evaluator dependencies first.") from error
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set.")
        self.client = Anthropic(api_key=key)
        self.model = model
        self.max_tokens = max_tokens

    def run(
        self,
        session_key: str,
        config_id: str,
        prompt: EvalPrompt,
        profile: ProjectProfile,
        skills: list[SkillCandidate],
    ) -> SessionResult:
        started = time.perf_counter()
        selected: list[str] = []
        input_tokens = 0
        output_tokens = 0
        skill_by_id = {item.id: item for item in skills}
        system = system_prompt(profile, skills)
        tools = tool_definitions(skills)
        messages: list[dict[str, object]] = [{"role": "user", "content": prompt.text}]
        answer_parts: list[str] = []
        try:
            for _ in range(3):
                response = self.client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    tools=tools,
                    messages=messages,
                )
                input_tokens += int(response.usage.input_tokens)
                output_tokens += int(response.usage.output_tokens)
                tool_results: list[dict[str, object]] = []
                for block in response.content:
                    if block.type == "text":
                        answer_parts.append(block.text)
                    elif block.type == "tool_use" and block.name == "load_skill":
                        skill_id = str(block.input.get("skill_id", ""))
                        skill = skill_by_id.get(skill_id)
                        if skill and skill_id not in selected:
                            selected.append(skill_id)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": (
                                    skill.body if skill else "Unknown skill id; do not use it."
                                ),
                                "is_error": skill is None,
                            }
                        )
                if not tool_results:
                    break
                content = [block.model_dump() for block in response.content]
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": tool_results})
            return SessionResult(
                session_key=session_key,
                config_id=config_id,
                prompt_id=prompt.id,
                prompt_origin=prompt.origin,
                expected_skill=prompt.expected_skill,
                available_skills=list(skill_by_id),
                selected_skills=selected,
                answer="\n".join(answer_parts),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=token_cost(self.model, input_tokens, output_tokens),
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome="completed",
            )
        except Exception as error:  # API errors are persisted for resume/reporting
            return SessionResult(
                session_key=session_key,
                config_id=config_id,
                prompt_id=prompt.id,
                prompt_origin=prompt.origin,
                expected_skill=prompt.expected_skill,
                available_skills=list(skill_by_id),
                selected_skills=selected,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=token_cost(self.model, input_tokens, output_tokens),
                duration_ms=int((time.perf_counter() - started) * 1000),
                outcome="error",
                error=f"{type(error).__name__}: {error}",
            )
