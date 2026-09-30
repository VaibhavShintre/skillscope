from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path

import yaml

from skills_evaluator.costs import estimate_session
from skills_evaluator.decoy import build_decoy
from skills_evaluator.labels import stamp_labels
from skills_evaluator.models import (
    EvalPrompt,
    ExperimentConfig,
    ProjectProfile,
    RunPlan,
    SkillCandidate,
)
from skills_evaluator.profiler import profile_project
from skills_evaluator.prompts import apply_label_overrides, generate_prompts, load_prompt_file
from skills_evaluator.skills import discover_candidates


def _configurations(
    skill_ids: list[str], decoy_id: str | None = None
) -> tuple[list[ExperimentConfig], int]:
    """Configurations in priority order, and how many of them are required.

    Required, in this order: the baseline, every singleton, the full bundle, then every
    leave-one-out (the full bundle minus one skill). Those are what every verdict needs, so a
    tight budget must never cut them. Greedy prefixes and pairs come after. Duplicate skill
    sets are dropped, keeping the earliest, which is why a leave-one-out that equals a
    singleton (2 skills) is not planned twice.
    """
    required = [ExperimentConfig(id="baseline", skill_ids=[], kind="baseline")]
    required.extend(
        ExperimentConfig(id=f"singleton-{index:02d}", skill_ids=[skill_id], kind="singleton")
        for index, skill_id in enumerate(skill_ids, start=1)
    )
    extras: list[ExperimentConfig] = []
    if skill_ids:
        required.append(ExperimentConfig(id="full", skill_ids=skill_ids, kind="full"))
        if len(skill_ids) > 1:
            required.extend(
                ExperimentConfig(
                    id=f"without-{index:02d}",
                    skill_ids=[item for item in skill_ids if item != skill_id],
                    kind="leave-one-out",
                )
                for index, skill_id in enumerate(skill_ids, start=1)
            )
        for size in range(2, min(8, len(skill_ids)) + 1):
            extras.append(
                ExperimentConfig(
                    id=f"greedy-prefix-{size:02d}", skill_ids=skill_ids[:size], kind="greedy"
                )
            )
        pair_limit = min(4, len(skill_ids))
        if len(skill_ids) > 1:
            extras.extend(
                ExperimentConfig(
                    id=f"pair-{left + 1:02d}-{right + 1:02d}",
                    skill_ids=[skill_ids[left], skill_ids[right]],
                    kind="pair",
                )
                for left in range(pair_limit)
                for right in range(left + 1, pair_limit)
            )
    if decoy_id:
        # Two extra configurations measure the control: the decoy alone against the baseline,
        # and the full bundle with the decoy listed against the full bundle without it.
        required.append(ExperimentConfig(id="decoy-only", skill_ids=[decoy_id], kind="decoy"))
        if skill_ids:
            required.append(
                ExperimentConfig(id="decoy-full", skill_ids=[*skill_ids, decoy_id], kind="decoy")
            )
    unique: dict[tuple[str, ...], ExperimentConfig] = {}
    for config in required:
        unique.setdefault(tuple(config.skill_ids), config)
    required_count = len(unique)
    for config in extras:
        unique.setdefault(tuple(config.skill_ids), config)
    return list(unique.values()), required_count


def _config_costs(
    model: str,
    profile: ProjectProfile,
    config: ExperimentConfig,
    prompts: list[EvalPrompt],
    by_id: dict[str, SkillCandidate],
) -> tuple[float, float]:
    """(worst-case, expected) cost of running every prompt under one configuration."""
    skills = [by_id[item] for item in config.skill_ids]
    estimates = [estimate_session(model, profile, prompt, skills) for prompt in prompts]
    return sum(item.worst_usd for item in estimates), sum(item.expected_usd for item in estimates)


def build_plan(
    project: Path,
    sources: list[str],
    run_root: Path,
    include_anthropic: bool = True,
    maximum_candidates: int = 12,
    max_sessions: int = 300,
    max_cost_usd: float = 5.0,
    model: str = "claude-haiku-4-5-20251001",
    seed: int = 1729,
    prompt_file: Path | None = None,
    decoy: bool = False,
    repeats: int = 1,
) -> tuple[RunPlan, Path]:
    if not 1 <= repeats <= 9:
        raise ValueError("--repeats must be between 1 and 9.")
    profile = profile_project(project)
    candidates = discover_candidates(
        profile,
        sources,
        run_root.parent / "cache",
        include_anthropic,
        maximum_candidates,
    )
    decoy_skill = build_decoy() if decoy else None
    extra = [decoy_skill] if decoy_skill else []
    decoy_id = decoy_skill.id if decoy_skill else None
    dropped: list[str] = []
    while candidates:
        active = [item for item in candidates if not item.blocked]
        by_id = {item.id: item for item in [*candidates, *extra]}
        drafts = generate_prompts(profile, candidates)
        if prompt_file:
            drafts += load_prompt_file(prompt_file, candidates)[0]
        ordered, required_count = _configurations([item.id for item in active], decoy_id)
        core = ordered[:required_count]
        core_worst = repeats * sum(
            _config_costs(model, profile, item, drafts, by_id)[0] for item in core
        )
        if len(drafts) * len(core) * repeats <= max_sessions and core_worst <= max_cost_usd:
            break
        removable = next(
            (item for item in reversed(candidates) if not item.user_requested), None
        )
        if removable is None:
            raise ValueError(
                "The session/cost cap cannot cover the baseline, singleton, full, and "
                "leave-one-out tests for all explicitly requested skills. Increase "
                "--cost-cap/--max-sessions, send fewer skills, or lower --repeats or drop "
                "--decoy."
            )
        candidates.remove(removable)
        dropped.append(removable.name)

    prompts = generate_prompts(profile, candidates)
    if prompt_file:
        user_prompts, overrides = load_prompt_file(prompt_file, candidates)
        existing_ids = {item.id for item in prompts}
        duplicate_ids = existing_ids & {item.id for item in user_prompts}
        if duplicate_ids:
            raise ValueError(
                f"User prompt IDs conflict with generated IDs: {sorted(duplicate_ids)}"
            )
        prompts = apply_label_overrides(prompts, overrides)
        prompts.extend(user_prompts)
    prompts = stamp_labels(prompts, candidates)
    plan_candidates = [*candidates, *extra]
    by_id = {item.id: item for item in plan_candidates}
    active_ids = [item.id for item in candidates if not item.blocked]
    all_configs, core_count = _configurations(active_ids, decoy_id)
    # Keep configurations in priority order until the cost cap (on the worst case) or the
    # session cap would be exceeded, but never fewer than the required set (baseline,
    # singletons, full, leave-one-outs).
    configs: list[ExperimentConfig] = []
    worst_total = expected_total = 0.0
    for index, config in enumerate(all_configs):
        worst, expected = _config_costs(model, profile, config, prompts, by_id)
        worst, expected = worst * repeats, expected * repeats
        if index >= core_count and (
            worst_total + worst > max_cost_usd
            or (len(configs) + 1) * len(prompts) * repeats > max_sessions
        ):
            break
        configs.append(config)
        worst_total += worst
        expected_total += expected
    planned = len(prompts) * len(configs) * repeats
    if worst_total > max_cost_usd or planned > max_sessions:
        raise ValueError(
            f"The ${max_cost_usd:.2f} cap cannot cover even the baseline sessions for {model} "
            f"(worst case ${worst_total:.2f}, {planned} sessions)."
        )
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{profile.content_hash[:8]}"
    plan = RunPlan(
        run_id=run_id,
        project=profile,
        candidates=plan_candidates,
        prompts=prompts,
        configurations=configs,
        model=model,
        seed=seed,
        max_sessions=max_sessions,
        max_cost_usd=max_cost_usd,
        estimated_max_cost_usd=round(worst_total, 6),
        estimated_expected_cost_usd=round(expected_total, 6),
        planned_sessions=planned,
        repeats=repeats,
        dropped_candidates=dropped,
    )
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    # One shuffled round per repeat, run in turn: a run stopped by the cost cap holds complete
    # rounds, not one lucky cell repeated. With one repeat this is the old order and file shape.
    order: list[list[object]] = []
    for repeat in range(repeats):
        round_ = [(config.id, prompt.id) for config in configs for prompt in prompts]
        random.Random(seed + repeat).shuffle(round_)
        for config_id, prompt_id in round_:
            order.append([config_id, prompt_id, repeat] if repeats > 1 else [config_id, prompt_id])
    (run_dir / "session-order.json").write_text(json.dumps(order, indent=2), encoding="utf-8")
    (run_dir / "run-manifest.json").write_text(plan.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "project-profile.json").write_text(
        profile.model_dump_json(indent=2), encoding="utf-8"
    )
    (run_dir / "project-files.json").write_text(
        json.dumps(
            {
                "included": profile.context_files,
                "excluded_patterns": profile.excluded_patterns,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "catalog.json").write_text(
        json.dumps(
            [item.model_dump(mode="json", exclude={"body"}) for item in plan_candidates],
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "security.json").write_text(
        json.dumps(
            {
                item.id: [finding.model_dump(mode="json") for finding in item.findings]
                for item in plan_candidates
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (run_dir / "prompts.yaml").write_text(
        yaml.safe_dump([item.model_dump(mode="json") for item in prompts], sort_keys=False),
        encoding="utf-8",
    )
    (run_dir / "experiment-plan.json").write_text(
        json.dumps(
            {
                "configurations": [item.model_dump(mode="json") for item in configs],
                "session_order": order,
                "planned_sessions": planned,
                "repeats": repeats,
                "model": model,
                "seed": seed,
                "max_cost_usd": max_cost_usd,
                "estimated_max_cost_usd": round(worst_total, 6),
                "estimated_expected_cost_usd": round(expected_total, 6),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return plan, run_dir
