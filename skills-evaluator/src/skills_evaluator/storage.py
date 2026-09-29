from __future__ import annotations

import json
import os
from pathlib import Path

from skills_evaluator.models import RunPlan, SessionResult


def load_plan(run_dir: Path) -> RunPlan:
    return RunPlan.model_validate_json((run_dir / "run-manifest.json").read_text(encoding="utf-8"))


def load_results(run_dir: Path) -> list[SessionResult]:
    path = run_dir / "events.jsonl"
    if not path.exists():
        return []
    results: list[SessionResult] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            results.append(SessionResult.model_validate_json(line))
        except ValueError as error:
            raise ValueError(f"Invalid events.jsonl line {line_number}: {error}") from error
    keys = [item.session_key for item in results]
    if len(keys) != len(set(keys)):
        raise ValueError("events.jsonl contains duplicate session keys")
    return results


def append_result(run_dir: Path, result: SessionResult) -> None:
    path = run_dir / "events.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(result.model_dump_json() + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def session_order(run_dir: Path) -> list[tuple[str, str]]:
    raw = json.loads((run_dir / "session-order.json").read_text(encoding="utf-8"))
    return [(str(config), str(prompt)) for config, prompt in raw]
