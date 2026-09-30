"""Cost estimates for one API-harness session.

The hard cost cap is enforced on a worst-case bound built from the actual prompt, tool and skill
text. A separate expected figure, calibrated on observed runs, shows what a session typically
costs. Token counts are estimated from character counts (no tokenizer, no API call), so both are
estimates: the worst case deliberately assumes short tokens.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

from skills_evaluator.engine import (
    DEFAULT_MAX_TOKENS,
    MAX_TURNS,
    system_prompt,
    token_cost,
    tool_definitions,
)
from skills_evaluator.models import EvalPrompt, ProjectProfile, SkillCandidate

# Characters per token used for every estimate. English prose is closer to 4; code and JSON are
# closer to 3, so 3 keeps the worst case on the safe side.
CHARS_PER_TOKEN = 3.0
# Request framing (roles, tool_use wrappers) that the character counts miss.
REQUEST_OVERHEAD_TOKENS = 100
# Tool results for repeated load_skill calls ("already loaded"), which the engine does not expand
# into another copy of the skill body.
TOOL_RESULT_SLACK_TOKENS = 1_000

# Expected case, calibrated on runs 20260929T230611Z-95d4be2f and 20260930T011332Z-505489ed:
# an average of about 520 output tokens per session, and skills loaded in about 60% of sessions
# that had any skill available.
EXPECTED_OUTPUT_TOKENS = 520
EXPECTED_LOAD_RATE = 0.6


@dataclass(frozen=True)
class SessionEstimate:
    worst_input_tokens: int
    worst_output_tokens: int
    worst_usd: float
    expected_input_tokens: int
    expected_output_tokens: int
    expected_usd: float


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def estimate_session(
    model: str,
    profile: ProjectProfile,
    prompt: EvalPrompt,
    skills: list[SkillCandidate],
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> SessionEstimate:
    base = (
        estimate_tokens(system_prompt(profile, skills))
        + estimate_tokens(json.dumps(tool_definitions(skills)))
        + estimate_tokens(prompt.text)
        + REQUEST_OVERHEAD_TOKENS
    )
    if not skills:
        # No tool is offered, so the harness makes exactly one request.
        worst_input, worst_output = base, max_tokens
        expected_input, expected_output = base, min(EXPECTED_OUTPUT_TOKENS, max_tokens)
    else:
        # The engine sends at most MAX_TURNS requests and returns each skill body only the first
        # time it is loaded, so every body enters the context at most once. Each earlier reply
        # (up to max_tokens) is sent back as input on the next request.
        bodies = sum(estimate_tokens(item.body) for item in skills)
        worst_input = 0
        for turn in range(MAX_TURNS):
            worst_input += base + turn * max_tokens
            if turn:
                worst_input += bodies + TOOL_RESULT_SLACK_TOKENS
        worst_output = MAX_TURNS * max_tokens
        average_body = bodies / len(skills)
        expected_input = round(
            base + EXPECTED_LOAD_RATE * (base + REQUEST_OVERHEAD_TOKENS + average_body)
        )
        expected_output = min(EXPECTED_OUTPUT_TOKENS, worst_output)
    return SessionEstimate(
        worst_input_tokens=worst_input,
        worst_output_tokens=worst_output,
        worst_usd=token_cost(model, worst_input, worst_output),
        expected_input_tokens=expected_input,
        expected_output_tokens=expected_output,
        expected_usd=token_cost(model, expected_input, expected_output),
    )
