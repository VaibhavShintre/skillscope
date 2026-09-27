# Iteration 1: Runner Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the path from a hand-written `prompts.yaml` and a folder of skills to a fire-count table, then run the week-2 gate on a real Next.js repo with 30 skills.

**Architecture:** A planner turns prompts × configs × repeats into sessions. A pool runs them 4 at a time. Each session spawns `claude -p --bare` in a per-config workspace copy, parses the stream-json output line by line, and kills the process the moment a `Skill` tool_use appears (or the turn ends without one). Every record is appended to `events.jsonl`, and a terminal table summarises fires per skill per config.

**Tech Stack:** Python 3.12 via uv, Pydantic v2, Typer, Rich, PyYAML, pytest + pytest-asyncio, ruff. Claude Code CLI 2.1.280 in headless mode with `ANTHROPIC_API_KEY`.

**Spec:** `docs/superpowers/specs/2026-09-27-iteration-1-runner-spike-design.md`

## Global Constraints

- Python `>=3.12`; install with `uv python install 3.12` if absent (system has 3.11).
- Isolation: every real session runs with `--bare`, `ANTHROPIC_API_KEY`, and `CLAUDE_CONFIG_DIR` set to a scratch directory under the run folder. Never touch `~/.claude`.
- Tools passed on the command line: `--tools Read,Grep,Glob,Skill --allowedTools Read,Grep,Glob,Skill --permission-prompts none`. The day-0 probe may amend this list; if it does, amend `build_argv` and the spec's section 4.1 in the same commit.
- Early stop: kill on the first `Skill` tool_use. `--max-budget-usd` (default 0.05) and a wall-clock timeout (default 120 s) are backstops.
- "Fired" means the first `Skill` call in the session.
- Store: `events.jsonl` only, flush and fsync per record. No SQLite.
- Total API spend for iteration 1: about $20. Pool cost cap default $5 per scan.
- Field names in the stream parser come from recorded fixtures under `tests/fixtures/streams/`, never from memory. If a fixture recorded in Task 1 disagrees with the hand-written fixtures in Task 4, the recorded one wins and Task 4's fixtures are corrected before proceeding.
- Haiku 4.5 list prices for cost estimation: input $1.00, output $5.00, cache write $1.25, cache read $0.10 per million tokens.
- Commit after every task with a message in the form `feat(runner): ...`, `test(...)`, `chore(...)`, ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Gate decision rule: wrong-or-none rate on `full` minus `minimal` ≥ 15 points, `medium` in between, over 40 prompts × 3 repeats.

---

## File structure

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | uv project, deps, entry point `skillscope`, pytest and ruff config |
| `src/skillscope/__init__.py` | version string |
| `src/skillscope/models.py` | Pydantic models (`Prompt`, `Config`, `Session`, `SessionOptions`, `SessionRecord`), enums, YAML loaders |
| `src/skillscope/runner/__init__.py` | empty |
| `src/skillscope/runner/stream.py` | `parse_line` and the event dataclasses. Pure. |
| `src/skillscope/runner/decision.py` | `SessionState` early-stop state machine. Pure. |
| `src/skillscope/runner/session.py` | spawns `claude`, streams, kills, writes raw NDJSON, builds a `SessionRecord` |
| `src/skillscope/runner/workspace.py` | copies the repo once per config and installs that config's skills |
| `src/skillscope/runner/plan.py` | `plan()` pure function |
| `src/skillscope/runner/pool.py` | `Pool` with semaphore, cost cap, auth abort |
| `src/skillscope/store.py` | `EventLog` append-only JSONL |
| `src/skillscope/report/__init__.py` | empty |
| `src/skillscope/report/terminal.py` | fire table and per-config summary |
| `src/skillscope/cli.py` | Typer app: `scan`, `report` |
| `tests/conftest.py` | shared fixtures: fake claude on PATH, tmp skills dir, tmp repo |
| `tests/fake_claude/claude` | replay script on PATH |
| `tests/fixtures/streams/*.ndjson`, `index.json` | recorded and hand-written transcripts |
| `tests/test_*.py` | one test module per source module |
| `gate/prompts.yaml`, `gate/configs.yaml` | week-2 gate inputs |
| `docs/superpowers/gate/2026-10-10-week-2-gate.md` | gate result record |

---

### Task 1: Day-0 probe (throwaway script, recorded fixtures)

**Files:**
- Create: `scratch/probe.sh` (gitignored, never committed)
- Create: `tests/fixtures/streams/probe_fire.ndjson`
- Create: `tests/fixtures/streams/probe_no_fire.ndjson`
- Modify: `docs/superpowers/specs/2026-09-27-iteration-1-runner-spike-design.md` section 3 (append findings)

**Interfaces:**
- Produces: two recorded transcripts that Task 4's parser tests read, and a confirmed argv shape that Task 7's `build_argv` uses.

- [ ] **Step 1: Confirm the API key is available without printing it**

Run: `test -n "$ANTHROPIC_API_KEY" && echo set || echo MISSING`
Expected: `set`. If `MISSING`, stop and ask the user to export it in their shell. Do not ask for the value in chat.

- [ ] **Step 2: Write the probe script**

```bash
#!/usr/bin/env bash
# scratch/probe.sh — throwaway. Builds a temp project with one skill and runs one headless session.
set -euo pipefail
ROOT=$(mktemp -d /tmp/skillscope-probe.XXXXXX)
CFG="$ROOT/config"; PROJ="$ROOT/proj"
mkdir -p "$CFG" "$PROJ/.claude/skills/tomato-recipes"
cat > "$PROJ/.claude/skills/tomato-recipes/SKILL.md" <<'EOF'
---
name: tomato-recipes
description: Use when the user asks for a recipe that features tomatoes, tomato sauce, or salsa. Provides tested tomato recipes.
---
When invoked, reply with one short tomato recipe and nothing else.
EOF
echo "# probe project" > "$PROJ/README.md"
PROMPT="${1:-Give me a quick recipe for a tomato salsa.}"
MODE="${2:-bare}"
cd "$PROJ"
COMMON=(-p "$PROMPT" --model haiku --output-format stream-json --verbose \
  --tools Read,Grep,Glob,Skill --allowedTools Read,Grep,Glob,Skill \
  --permission-prompts none --max-budget-usd 0.05)
if [ "$MODE" = bare ]; then
  CLAUDE_CONFIG_DIR="$CFG" claude --bare "${COMMON[@]}"
else
  CLAUDE_CONFIG_DIR="$CFG" claude --setting-sources project "${COMMON[@]}"
fi
echo "PROBE_ROOT=$ROOT" >&2
```

Run: `mkdir -p scratch && chmod +x scratch/probe.sh`

- [ ] **Step 3: Run the fire probe under --bare and save the output**

Run: `scratch/probe.sh > tests/fixtures/streams/probe_fire.ndjson; echo "exit=$?"; grep -c '"type"' tests/fixtures/streams/probe_fire.ndjson`
Expected: exit 0 and several lines. Then: `grep -o '"name":"Skill"[^}]*}' tests/fixtures/streams/probe_fire.ndjson | head -3`
Expected: a `Skill` tool_use whose `input` names `tomato-recipes`. Note the exact key inside `input` (likely `skill`).

If no `Skill` tool_use appears: run `scratch/probe.sh "" project` and check again. If the project-sources form fires and `--bare` does not, record that in the spec (Step 7) and use the project-sources form in Task 7's `build_argv`.

- [ ] **Step 4: Run the no-fire probe and save the output**

Run: `scratch/probe.sh "What is the capital of France? Answer in one word." > tests/fixtures/streams/probe_no_fire.ndjson; echo "exit=$?"`
Expected: exit 0, no `"name":"Skill"` in the file, and a final line with `"type":"result"`.

- [ ] **Step 5: Read off the field names**

Run: `tail -1 tests/fixtures/streams/probe_fire.ndjson | python3 -m json.tool | head -40` and `head -1 tests/fixtures/streams/probe_fire.ndjson | python3 -m json.tool | head -40`
Record: the `init` line's keys for model and version (expected `model`, `claude_code_version`, and possibly a `skills` list), the assistant line's `message.usage` keys, the `result` line's `total_cost_usd`, `num_turns`, `duration_ms`, `subtype`, `is_error`. Also check whether an assistant message is emitted as one event with all content blocks or split into one event per block (look for two consecutive `"type":"assistant"` lines sharing a `message.id`).

- [ ] **Step 6: Probe tool gating**

Run: `scratch/probe.sh 2>/dev/null | grep -c '"name":"Skill"'` after editing the script to drop `Skill` from `--tools` only. Then restore and drop it from `--allowedTools` only. Record which omission hides or denies the Skill tool (a denial shows as a `permission_denials` entry on the `result` line or a tool_result with `is_error`).

- [ ] **Step 7: Confirm `~/.claude` is untouched and record findings**

Run: `ls -la ~/.claude/projects | tail -3; find "$(grep -o 'PROBE_ROOT=.*' /dev/null || echo /tmp)" -maxdepth 0 >/dev/null; ls /tmp/skillscope-probe.*/config`
Expected: no new entry under `~/.claude/projects` with today's timestamp for the probe path; the scratch config dir contains the session files instead.

Append to the spec's section 3 a subsection `### 3.1 Day-0 probe results (YYYY-MM-DD)` with: isolation form that worked, exact argv, `Skill` input key name, whether assistant events are split per content block, `result` field names, the tool-gating outcome, and where transcripts landed.

- [ ] **Step 8: Commit fixtures and spec amendment**

```bash
git add tests/fixtures/streams/probe_fire.ndjson tests/fixtures/streams/probe_no_fire.ndjson docs/superpowers/specs/2026-09-27-iteration-1-runner-spike-design.md
git commit -m "chore(fixtures): record day-0 probe transcripts and findings

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Package skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `src/skillscope/__init__.py`
- Create: `src/skillscope/runner/__init__.py`
- Create: `src/skillscope/report/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/test_package.py`

**Interfaces:**
- Produces: importable `skillscope` package, `uv run pytest` and `uv run ruff check` working.

- [ ] **Step 1: Write the failing import test**

```python
# tests/test_package.py
import skillscope


def test_version_is_string():
    assert isinstance(skillscope.__version__, str)
```

- [ ] **Step 2: Write pyproject.toml**

```toml
[project]
name = "skillscope"
version = "0.0.1"
description = "Measures which Claude Code skills fire on a project's real prompts."
readme = "README.md"
license = "Apache-2.0"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.7",
    "pyyaml>=6.0",
    "typer>=0.12",
    "rich>=13.7",
]

[project.scripts]
skillscope = "skillscope.cli:app"

[dependency-groups]
dev = [
    "pytest>=8.2",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/skillscope"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

- [ ] **Step 3: Create the package files**

```python
# src/skillscope/__init__.py
__version__ = "0.0.1"
```

`src/skillscope/runner/__init__.py`, `src/skillscope/report/__init__.py`, `tests/__init__.py`: empty files. `README.md`: one line, `# skillscope`.

- [ ] **Step 4: Install and run**

Run: `uv python install 3.12 && uv sync && uv run pytest -q`
Expected: `1 passed`.

Run: `uv run ruff check .`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock README.md src tests
git commit -m "chore: package skeleton with uv, pytest, ruff

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Models and YAML loaders

**Files:**
- Create: `src/skillscope/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces:
  - `class Origin(StrEnum): TASK = "task"; CONTROL = "control"`
  - `class Outcome(StrEnum): FIRED, NO_FIRE, TIMEOUT, BUDGET, ERROR` with lowercase values
  - `class CostSource(StrEnum): RESULT = "result"; ESTIMATED = "estimated"`
  - `class Prompt(BaseModel)`: `id: str`, `text: str`, `expected: list[str]` (empty list means none), `origin: Origin`; `matches(skill: str | None) -> bool | None`
  - `class Config(BaseModel)`: `name: str`, `skills: list[str] | Literal["*"]`; `resolve(skills_dir: Path) -> list[str]`
  - `SessionKey = tuple[str, str, int]`
  - `class Session(BaseModel)`: `config: str`, `prompt_id: str`, `repeat: int`; `key() -> SessionKey`
  - `class SessionOptions(BaseModel)`: `run_id: str`, `model: str = "haiku"`, `budget_usd: float = 0.05`, `timeout_s: float = 120.0`, `config_dir: Path`, `raw_dir: Path`, `claude_bin: str = "claude"`
  - `class SessionRecord(BaseModel)`: fields per spec section 6.3 plus `skills_loaded: int | None`; `key() -> SessionKey`
  - `load_prompts(path: Path) -> list[Prompt]`, `load_configs(path: Path) -> list[Config]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_models.py
from pathlib import Path

import pytest

from skillscope.models import (
    Config,
    Origin,
    Outcome,
    Prompt,
    Session,
    SessionRecord,
    load_configs,
    load_prompts,
)


def test_prompt_expected_none_string_becomes_empty_list():
    p = Prompt(id="c1", text="x", expected="none", origin="control")
    assert p.expected == []
    assert p.origin is Origin.CONTROL


def test_prompt_expected_single_string_becomes_list():
    p = Prompt(id="p1", text="x", expected="nextjs", origin="task")
    assert p.expected == ["nextjs"]


def test_prompt_matches():
    p = Prompt(id="p1", text="x", expected=["a", "b"], origin="task")
    assert p.matches("a") is True
    assert p.matches("c") is False
    assert p.matches(None) is False
    control = Prompt(id="c1", text="x", expected="none", origin="control")
    assert control.matches("a") is None


def test_config_resolve_star_lists_skill_folders(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "not-a-skill.txt").write_text("x")
    c = Config(name="full", skills="*")
    assert c.resolve(tmp_path) == ["a", "b"]


def test_config_resolve_explicit_list_is_returned_as_is(tmp_path: Path):
    c = Config(name="minimal", skills=["b", "a"])
    assert c.resolve(tmp_path) == ["b", "a"]


def test_session_key():
    assert Session(config="full", prompt_id="p1", repeat=2).key() == ("full", "p1", 2)


def test_session_record_roundtrip_and_key():
    r = SessionRecord(
        run_id="r",
        config="full",
        prompt_id="p1",
        repeat=0,
        outcome=Outcome.FIRED,
        skills_invoked=["a"],
        first_skill="a",
        expected=["a"],
        matched=True,
        exploration_calls=0,
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        cost_source="estimated",
        duration_ms=100,
        model="haiku",
        claude_version="2.1.280",
        error=None,
        raw_path=Path("raw/full_p1_0.ndjson"),
        skills_loaded=5,
    )
    again = SessionRecord.model_validate_json(r.model_dump_json())
    assert again == r
    assert again.key() == ("full", "p1", 0)


def test_load_prompts_and_configs(tmp_path: Path):
    (tmp_path / "prompts.yaml").write_text(
        "- id: p1\n  text: hello\n  expected: a\n  origin: task\n"
        "- id: c1\n  text: bye\n  expected: none\n  origin: control\n"
    )
    (tmp_path / "configs.yaml").write_text('minimal: [a]\nfull: "*"\n')
    prompts = load_prompts(tmp_path / "prompts.yaml")
    configs = load_configs(tmp_path / "configs.yaml")
    assert [p.id for p in prompts] == ["p1", "c1"]
    assert [c.name for c in configs] == ["minimal", "full"]
    assert configs[1].skills == "*"


def test_load_prompts_rejects_duplicate_ids(tmp_path: Path):
    (tmp_path / "prompts.yaml").write_text(
        "- id: p1\n  text: a\n  expected: a\n  origin: task\n"
        "- id: p1\n  text: b\n  expected: a\n  origin: task\n"
    )
    with pytest.raises(ValueError, match="duplicate prompt id"):
        load_prompts(tmp_path / "prompts.yaml")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'skillscope.models'`

- [ ] **Step 3: Write models.py**

```python
# src/skillscope/models.py
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, field_validator


class Origin(StrEnum):
    TASK = "task"
    CONTROL = "control"


class Outcome(StrEnum):
    FIRED = "fired"
    NO_FIRE = "no_fire"
    TIMEOUT = "timeout"
    BUDGET = "budget"
    ERROR = "error"


class CostSource(StrEnum):
    RESULT = "result"
    ESTIMATED = "estimated"


class Prompt(BaseModel):
    id: str
    text: str
    expected: list[str]
    origin: Origin

    @field_validator("expected", mode="before")
    @classmethod
    def _normalise_expected(cls, v: object) -> list[str]:
        if v is None or v == "none" or v == []:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list) and all(isinstance(s, str) for s in v):
            return list(v)
        raise ValueError("expected must be a skill name, a list of names, or none")

    def matches(self, skill: str | None) -> bool | None:
        """True/False for task prompts; None for controls (expected is empty)."""
        if not self.expected:
            return None
        return skill is not None and skill in self.expected


class Config(BaseModel):
    name: str
    skills: list[str] | Literal["*"]

    def resolve(self, skills_dir: Path) -> list[str]:
        if self.skills == "*":
            return sorted(p.name for p in skills_dir.iterdir() if p.is_dir())
        return list(self.skills)


SessionKey = tuple[str, str, int]


class Session(BaseModel):
    config: str
    prompt_id: str
    repeat: int

    def key(self) -> SessionKey:
        return (self.config, self.prompt_id, self.repeat)


class SessionOptions(BaseModel):
    run_id: str
    model: str = "haiku"
    budget_usd: float = 0.05
    timeout_s: float = 120.0
    config_dir: Path
    raw_dir: Path
    claude_bin: str = "claude"


class SessionRecord(BaseModel):
    run_id: str
    config: str
    prompt_id: str
    repeat: int
    outcome: Outcome
    skills_invoked: list[str]
    first_skill: str | None
    expected: list[str]
    matched: bool | None
    exploration_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_source: CostSource
    duration_ms: int
    model: str | None
    claude_version: str | None
    error: str | None
    raw_path: Path
    skills_loaded: int | None = None

    def key(self) -> SessionKey:
        return (self.config, self.prompt_id, self.repeat)


def load_prompts(path: Path) -> list[Prompt]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of prompts")
    prompts = [Prompt.model_validate(item) for item in data]
    seen: set[str] = set()
    for p in prompts:
        if p.id in seen:
            raise ValueError(f"{path}: duplicate prompt id {p.id!r}")
        seen.add(p.id)
    return prompts


def load_configs(path: Path) -> list[Config]:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping of config name to skills")
    return [Config(name=name, skills=skills) for name, skills in data.items()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -q && uv run ruff check .`
Expected: `9 passed`, ruff clean.

- [ ] **Step 5: Commit**

```bash
git add src/skillscope/models.py tests/test_models.py
git commit -m "feat(models): prompt, config, session and record models with YAML loaders

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Stream parser

**Files:**
- Create: `src/skillscope/runner/stream.py`
- Create: `tests/fixtures/streams/fire.ndjson`
- Create: `tests/fixtures/streams/no_fire.ndjson`
- Create: `tests/fixtures/streams/explore_then_fire.ndjson`
- Create: `tests/fixtures/streams/budget.ndjson`
- Test: `tests/test_stream.py`

**Interfaces:**
- Produces (all in `stream.py`):
  - `@dataclass class Usage`: `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens` (all int, default 0); `__add__`
  - `@dataclass class ToolUse`: `name: str`, `input: dict`
  - `@dataclass class InitEvent`: `model: str | None`, `claude_version: str | None`, `skills: list[str]`
  - `@dataclass class AssistantEvent`: `tool_uses: list[ToolUse]`, `text: str`, `usage: Usage`, `stop_reason: str | None`
  - `@dataclass class ResultEvent`: `subtype: str`, `is_error: bool`, `total_cost_usd: float | None`, `num_turns: int | None`, `duration_ms: int | None`, `usage: Usage`
  - `@dataclass class RawEvent`: `line: str`
  - `Event = InitEvent | AssistantEvent | ResultEvent | RawEvent`
  - `parse_line(line: str) -> Event`
  - `skill_name(tool_use: ToolUse) -> str | None` — reads `input["skill"]`, falling back to `input["name"]`

**Before writing fixtures:** open `tests/fixtures/streams/probe_fire.ndjson` from Task 1 and copy the real key names and line shapes. The hand-written fixtures below assume the shape observed in Claude Code 2.x; if the probe differs, use the probe's shape.

- [ ] **Step 1: Write the hand-written fixtures**

`tests/fixtures/streams/fire.ndjson` (four lines; one JSON object per line, no wrapping):

```json
{"type":"system","subtype":"init","cwd":"/tmp/ws","session_id":"s1","tools":["Read","Grep","Glob","Skill"],"model":"claude-haiku-4-5-20251001","claude_code_version":"2.1.280","skills":["tomato-recipes","other"]}
{"type":"assistant","message":{"id":"m1","type":"message","role":"assistant","model":"claude-haiku-4-5-20251001","content":[{"type":"text","text":"I'll use the recipe skill."},{"type":"tool_use","id":"t1","name":"Skill","input":{"skill":"tomato-recipes","args":""}}],"stop_reason":"tool_use","usage":{"input_tokens":12,"cache_creation_input_tokens":900,"cache_read_input_tokens":0,"output_tokens":40}},"session_id":"s1"}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"skill loaded"}]},"session_id":"s1"}
{"type":"assistant","message":{"id":"m2","type":"message","role":"assistant","model":"claude-haiku-4-5-20251001","content":[{"type":"text","text":"Here is a salsa recipe."}],"stop_reason":"end_turn","usage":{"input_tokens":5,"cache_creation_input_tokens":0,"cache_read_input_tokens":900,"output_tokens":60}},"session_id":"s1"}
{"type":"result","subtype":"success","is_error":false,"duration_ms":2100,"duration_api_ms":1900,"num_turns":2,"result":"Here is a salsa recipe.","session_id":"s1","total_cost_usd":0.0016,"usage":{"input_tokens":17,"cache_creation_input_tokens":900,"cache_read_input_tokens":900,"output_tokens":100}}
```

`tests/fixtures/streams/no_fire.ndjson`:

```json
{"type":"system","subtype":"init","cwd":"/tmp/ws","session_id":"s2","tools":["Read","Grep","Glob","Skill"],"model":"claude-haiku-4-5-20251001","claude_code_version":"2.1.280","skills":["tomato-recipes"]}
{"type":"assistant","message":{"id":"m1","type":"message","role":"assistant","model":"claude-haiku-4-5-20251001","content":[{"type":"text","text":"Paris."}],"stop_reason":"end_turn","usage":{"input_tokens":10,"cache_creation_input_tokens":900,"cache_read_input_tokens":0,"output_tokens":3}},"session_id":"s2"}
{"type":"result","subtype":"success","is_error":false,"duration_ms":900,"duration_api_ms":800,"num_turns":1,"result":"Paris.","session_id":"s2","total_cost_usd":0.0012,"usage":{"input_tokens":10,"cache_creation_input_tokens":900,"cache_read_input_tokens":0,"output_tokens":3}}
```

`tests/fixtures/streams/explore_then_fire.ndjson`:

```json
{"type":"system","subtype":"init","cwd":"/tmp/ws","session_id":"s3","tools":["Read","Grep","Glob","Skill"],"model":"claude-haiku-4-5-20251001","claude_code_version":"2.1.280","skills":["nextjs-app-router"]}
{"type":"assistant","message":{"id":"m1","type":"message","role":"assistant","model":"claude-haiku-4-5-20251001","content":[{"type":"tool_use","id":"t1","name":"Glob","input":{"pattern":"app/**/page.tsx"}}],"stop_reason":"tool_use","usage":{"input_tokens":12,"cache_creation_input_tokens":900,"cache_read_input_tokens":0,"output_tokens":20}},"session_id":"s3"}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t1","content":"app/page.tsx"}]},"session_id":"s3"}
{"type":"assistant","message":{"id":"m2","type":"message","role":"assistant","model":"claude-haiku-4-5-20251001","content":[{"type":"tool_use","id":"t2","name":"Read","input":{"file_path":"app/page.tsx"}}],"stop_reason":"tool_use","usage":{"input_tokens":30,"cache_creation_input_tokens":0,"cache_read_input_tokens":900,"output_tokens":18}},"session_id":"s3"}
{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"t2","content":"export default function Page() {}"}]},"session_id":"s3"}
{"type":"assistant","message":{"id":"m3","type":"message","role":"assistant","model":"claude-haiku-4-5-20251001","content":[{"type":"tool_use","id":"t3","name":"Skill","input":{"skill":"nextjs-app-router","args":""}}],"stop_reason":"tool_use","usage":{"input_tokens":80,"cache_creation_input_tokens":0,"cache_read_input_tokens":900,"output_tokens":25}},"session_id":"s3"}
{"type":"result","subtype":"success","is_error":false,"duration_ms":5000,"duration_api_ms":4500,"num_turns":3,"result":"done","session_id":"s3","total_cost_usd":0.003,"usage":{"input_tokens":122,"cache_creation_input_tokens":900,"cache_read_input_tokens":1800,"output_tokens":63}}
```

`tests/fixtures/streams/budget.ndjson`:

```json
{"type":"system","subtype":"init","cwd":"/tmp/ws","session_id":"s4","tools":["Read","Grep","Glob","Skill"],"model":"claude-haiku-4-5-20251001","claude_code_version":"2.1.280","skills":[]}
{"type":"assistant","message":{"id":"m1","type":"message","role":"assistant","model":"claude-haiku-4-5-20251001","content":[{"type":"tool_use","id":"t1","name":"Grep","input":{"pattern":"TODO"}}],"stop_reason":"tool_use","usage":{"input_tokens":12,"cache_creation_input_tokens":900,"cache_read_input_tokens":0,"output_tokens":20}},"session_id":"s4"}
{"type":"result","subtype":"error_max_budget_usd","is_error":true,"duration_ms":3000,"duration_api_ms":2800,"num_turns":1,"result":"Budget exceeded","session_id":"s4","total_cost_usd":0.051,"usage":{"input_tokens":12,"cache_creation_input_tokens":900,"cache_read_input_tokens":0,"output_tokens":20}}
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_stream.py
from pathlib import Path

from skillscope.runner.stream import (
    AssistantEvent,
    InitEvent,
    RawEvent,
    ResultEvent,
    Usage,
    parse_line,
    skill_name,
)

FIXTURES = Path(__file__).parent / "fixtures" / "streams"


def lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


def test_init_event_fields():
    ev = parse_line(lines("fire.ndjson")[0])
    assert isinstance(ev, InitEvent)
    assert ev.model == "claude-haiku-4-5-20251001"
    assert ev.claude_version == "2.1.280"
    assert "tomato-recipes" in ev.skills


def test_assistant_event_with_skill_tool_use():
    ev = parse_line(lines("fire.ndjson")[1])
    assert isinstance(ev, AssistantEvent)
    assert [t.name for t in ev.tool_uses] == ["Skill"]
    assert skill_name(ev.tool_uses[0]) == "tomato-recipes"
    assert ev.text == "I'll use the recipe skill."
    assert ev.stop_reason == "tool_use"
    assert ev.usage.cache_creation_input_tokens == 900
    assert ev.usage.output_tokens == 40


def test_assistant_event_text_only():
    ev = parse_line(lines("no_fire.ndjson")[1])
    assert isinstance(ev, AssistantEvent)
    assert ev.tool_uses == []
    assert ev.stop_reason == "end_turn"


def test_user_line_is_raw():
    ev = parse_line(lines("fire.ndjson")[2])
    assert isinstance(ev, RawEvent)


def test_result_event_fields():
    ev = parse_line(lines("fire.ndjson")[-1])
    assert isinstance(ev, ResultEvent)
    assert ev.subtype == "success"
    assert ev.is_error is False
    assert ev.total_cost_usd == 0.0016
    assert ev.num_turns == 2
    assert ev.duration_ms == 2100
    assert ev.usage.output_tokens == 100


def test_budget_result_is_error_subtype():
    ev = parse_line(lines("budget.ndjson")[-1])
    assert isinstance(ev, ResultEvent)
    assert ev.subtype == "error_max_budget_usd"
    assert ev.is_error is True


def test_malformed_line_is_raw():
    ev = parse_line("{not json")
    assert isinstance(ev, RawEvent)
    assert ev.line == "{not json"


def test_unknown_type_is_raw():
    ev = parse_line('{"type":"something_new","x":1}')
    assert isinstance(ev, RawEvent)


def test_usage_add():
    a = Usage(input_tokens=1, output_tokens=2)
    b = Usage(input_tokens=3, cache_read_input_tokens=4)
    c = a + b
    assert (c.input_tokens, c.output_tokens, c.cache_read_input_tokens) == (4, 2, 4)


def test_probe_fixture_parses_without_raw_assistant_or_result():
    """The recorded day-0 transcript must parse with the same code as the hand-written ones."""
    evs = [parse_line(line) for line in lines("probe_fire.ndjson") if line.strip()]
    assert any(isinstance(e, InitEvent) for e in evs)
    assert any(isinstance(e, AssistantEvent) and e.tool_uses for e in evs)
    assert isinstance(evs[-1], ResultEvent)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_stream.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'skillscope.runner.stream'`

- [ ] **Step 4: Write stream.py**

```python
# src/skillscope/runner/stream.py
"""Pure parser for Claude Code's stream-json output. No IO."""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Usage:
    input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    output_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.output_tokens + other.output_tokens,
        )

    @classmethod
    def from_dict(cls, d: object) -> Usage:
        if not isinstance(d, dict):
            return cls()
        return cls(
            int(d.get("input_tokens") or 0),
            int(d.get("cache_creation_input_tokens") or 0),
            int(d.get("cache_read_input_tokens") or 0),
            int(d.get("output_tokens") or 0),
        )


@dataclass
class ToolUse:
    name: str
    input: dict


@dataclass
class InitEvent:
    model: str | None
    claude_version: str | None
    skills: list[str] = field(default_factory=list)


@dataclass
class AssistantEvent:
    tool_uses: list[ToolUse]
    text: str
    usage: Usage
    stop_reason: str | None


@dataclass
class ResultEvent:
    subtype: str
    is_error: bool
    total_cost_usd: float | None
    num_turns: int | None
    duration_ms: int | None
    usage: Usage


@dataclass
class RawEvent:
    line: str


Event = InitEvent | AssistantEvent | ResultEvent | RawEvent


def skill_name(tool_use: ToolUse) -> str | None:
    """The skill a Skill tool_use names. Key confirmed by the day-0 probe fixture."""
    v = tool_use.input.get("skill") or tool_use.input.get("name")
    return str(v) if v else None


def parse_line(line: str) -> Event:
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return RawEvent(line)
    if not isinstance(obj, dict):
        return RawEvent(line)
    kind = obj.get("type")
    if kind == "system" and obj.get("subtype") == "init":
        skills = obj.get("skills")
        names: list[str] = []
        if isinstance(skills, list):
            for s in skills:
                if isinstance(s, str):
                    names.append(s)
                elif isinstance(s, dict) and isinstance(s.get("name"), str):
                    names.append(s["name"])
        return InitEvent(
            model=obj.get("model"),
            claude_version=obj.get("claude_code_version") or obj.get("version"),
            skills=names,
        )
    if kind == "assistant":
        msg = obj.get("message") or {}
        content = msg.get("content") or []
        tool_uses: list[ToolUse] = []
        texts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                tool_uses.append(ToolUse(str(block.get("name")), dict(block.get("input") or {})))
            elif block.get("type") == "text":
                texts.append(str(block.get("text") or ""))
        return AssistantEvent(
            tool_uses=tool_uses,
            text="".join(texts),
            usage=Usage.from_dict(msg.get("usage")),
            stop_reason=msg.get("stop_reason"),
        )
    if kind == "result":
        cost = obj.get("total_cost_usd")
        return ResultEvent(
            subtype=str(obj.get("subtype") or ""),
            is_error=bool(obj.get("is_error")),
            total_cost_usd=float(cost) if cost is not None else None,
            num_turns=obj.get("num_turns"),
            duration_ms=obj.get("duration_ms"),
            usage=Usage.from_dict(obj.get("usage")),
        )
    return RawEvent(line)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_stream.py -q && uv run ruff check .`
Expected: `10 passed`. If `test_probe_fixture_parses_without_raw_assistant_or_result` fails, the recorded shape differs from the assumption: fix `parse_line` and the hand-written fixtures to match the recording, not the other way round.

- [ ] **Step 6: Commit**

```bash
git add src/skillscope/runner/stream.py tests/test_stream.py tests/fixtures/streams
git commit -m "feat(runner): stream-json parser with recorded and hand-written fixtures

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: Early-stop state machine

**Files:**
- Create: `src/skillscope/runner/decision.py`
- Test: `tests/test_decision.py`

**Interfaces:**
- Consumes: `Event` types and `skill_name` from `stream.py`; `Outcome` from `models.py`.
- Produces:
  - `@dataclass class Verdict`: `outcome: Outcome`, `kill: bool`
  - `class SessionState`: attributes `outcome: Outcome | None`, `skills_invoked: list[str]`, `exploration_calls: int`, `usage: Usage`, `model: str | None`, `claude_version: str | None`, `skills_loaded: int | None`, `result: ResultEvent | None`; methods `feed(event) -> Verdict | None`, `on_timeout() -> Outcome`, `on_exit(returncode: int) -> Outcome`
  - property `first_skill -> str | None`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_decision.py
from pathlib import Path

from skillscope.models import Outcome
from skillscope.runner.decision import SessionState, Verdict
from skillscope.runner.stream import parse_line

FIXTURES = Path(__file__).parent / "fixtures" / "streams"


def feed_all(name: str) -> tuple[SessionState, list[Verdict]]:
    state = SessionState()
    verdicts = []
    for line in (FIXTURES / name).read_text().splitlines():
        v = state.feed(parse_line(line))
        if v is not None:
            verdicts.append(v)
            if v.kill:
                break
    return state, verdicts


def test_fire_kills_on_first_skill_call():
    state, verdicts = feed_all("fire.ndjson")
    assert verdicts == [Verdict(Outcome.FIRED, kill=True)]
    assert state.outcome is Outcome.FIRED
    assert state.first_skill == "tomato-recipes"
    assert state.skills_invoked == ["tomato-recipes"]
    assert state.result is None  # killed before result arrived
    assert state.usage.output_tokens == 40
    assert state.skills_loaded == 2
    assert state.model == "claude-haiku-4-5-20251001"


def test_no_fire_on_end_turn_without_tool_use():
    state, verdicts = feed_all("no_fire.ndjson")
    assert verdicts == [Verdict(Outcome.NO_FIRE, kill=True)]
    assert state.first_skill is None


def test_explore_then_fire_counts_exploration():
    state, verdicts = feed_all("explore_then_fire.ndjson")
    assert state.outcome is Outcome.FIRED
    assert state.exploration_calls == 2
    assert state.first_skill == "nextjs-app-router"


def test_budget_result_sets_budget_outcome():
    state, verdicts = feed_all("budget.ndjson")
    assert verdicts[-1] == Verdict(Outcome.BUDGET, kill=False)
    assert state.result is not None and state.result.total_cost_usd == 0.051


def test_result_after_no_skill_is_no_fire():
    state = SessionState()
    assert state.feed(parse_line('{"type":"assistant","message":{"content":[{"type":"text","text":"hi"}],"stop_reason":null,"usage":{}}}')) is None
    v = state.feed(parse_line('{"type":"result","subtype":"success","is_error":false}'))
    assert v == Verdict(Outcome.NO_FIRE, kill=False)


def test_error_result_is_error():
    state = SessionState()
    v = state.feed(parse_line('{"type":"result","subtype":"error_during_execution","is_error":true}'))
    assert v == Verdict(Outcome.ERROR, kill=False)


def test_text_only_without_end_turn_does_not_decide():
    """Some versions split content blocks into separate assistant events; don't decide early."""
    state = SessionState()
    v = state.feed(parse_line('{"type":"assistant","message":{"content":[{"type":"text","text":"Let me check"}],"stop_reason":null,"usage":{}}}'))
    assert v is None
    assert state.outcome is None


def test_on_timeout_and_on_exit():
    s = SessionState()
    assert s.on_timeout() is Outcome.TIMEOUT
    s2 = SessionState()
    assert s2.on_exit(1) is Outcome.ERROR
    s3 = SessionState()
    assert s3.on_exit(0) is Outcome.NO_FIRE
    s4 = SessionState()
    s4.feed(parse_line((FIXTURES / "fire.ndjson").read_text().splitlines()[1]))
    assert s4.on_exit(-9) is Outcome.FIRED  # killed by us after a fire keeps the fire
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_decision.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'skillscope.runner.decision'`

- [ ] **Step 3: Write decision.py**

```python
# src/skillscope/runner/decision.py
"""Early-stop state machine. Pure: fed parsed events, returns verdicts."""

from __future__ import annotations

from dataclasses import dataclass, field

from skillscope.models import Outcome
from skillscope.runner.stream import (
    AssistantEvent,
    Event,
    InitEvent,
    ResultEvent,
    Usage,
    skill_name,
)


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    kill: bool


@dataclass
class SessionState:
    outcome: Outcome | None = None
    skills_invoked: list[str] = field(default_factory=list)
    exploration_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    model: str | None = None
    claude_version: str | None = None
    skills_loaded: int | None = None
    result: ResultEvent | None = None

    @property
    def first_skill(self) -> str | None:
        return self.skills_invoked[0] if self.skills_invoked else None

    def feed(self, event: Event) -> Verdict | None:
        if isinstance(event, InitEvent):
            self.model = event.model
            self.claude_version = event.claude_version
            self.skills_loaded = len(event.skills)
            return None
        if isinstance(event, AssistantEvent):
            self.usage = self.usage + event.usage
            fired_now = False
            for tu in event.tool_uses:
                if tu.name == "Skill":
                    self.skills_invoked.append(skill_name(tu) or "?")
                    fired_now = True
                else:
                    self.exploration_calls += 1
            if fired_now and self.outcome is None:
                self.outcome = Outcome.FIRED
                return Verdict(Outcome.FIRED, kill=True)
            if not event.tool_uses and event.stop_reason == "end_turn" and self.outcome is None:
                self.outcome = Outcome.NO_FIRE
                return Verdict(Outcome.NO_FIRE, kill=True)
            return None
        if isinstance(event, ResultEvent):
            self.result = event
            if self.outcome is None:
                if "budget" in event.subtype:
                    self.outcome = Outcome.BUDGET
                elif event.is_error or event.subtype.startswith("error"):
                    self.outcome = Outcome.ERROR
                else:
                    self.outcome = Outcome.NO_FIRE
            return Verdict(self.outcome, kill=False)
        return None

    def on_timeout(self) -> Outcome:
        if self.outcome is None:
            self.outcome = Outcome.TIMEOUT
        return self.outcome

    def on_exit(self, returncode: int) -> Outcome:
        if self.outcome is None:
            self.outcome = Outcome.NO_FIRE if returncode == 0 else Outcome.ERROR
        return self.outcome
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_decision.py -q && uv run ruff check .`
Expected: `8 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/skillscope/runner/decision.py tests/test_decision.py
git commit -m "feat(runner): early-stop state machine

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Fake claude binary

**Files:**
- Create: `tests/fake_claude/claude` (executable)
- Create: `tests/fixtures/streams/index.json`
- Create: `tests/conftest.py`
- Test: `tests/test_fake_claude.py`

**Interfaces:**
- Produces: a script that, given the same argv shape as the real CLI, streams a fixture. Prompt lookup is by exact text in `index.json`. Env knobs: `FAKE_CLAUDE_HANG=1` (sleep forever after the init line), `FAKE_CLAUDE_FAIL=1` (exit 1 with stderr `fake failure`), `FAKE_CLAUDE_AUTH_FAIL=1` (exit 1 with stderr `Invalid API key · Please run /login`), `FAKE_CLAUDE_LOG=<path>` (append one JSON line with argv, cwd, and env keys `CLAUDE_CONFIG_DIR`, `ANTHROPIC_API_KEY` presence), `FAKE_CLAUDE_DELAY` (seconds between lines, default 0.05).
- Produces: conftest fixture `fake_claude_path` that prepends `tests/fake_claude` to `PATH` for the test.

- [ ] **Step 1: Write index.json**

```json
{
  "Give me a quick recipe for a tomato salsa.": "fire.ndjson",
  "What is the capital of France? Answer in one word.": "no_fire.ndjson",
  "Add a loading skeleton to the dashboard route while data fetches": "explore_then_fire.ndjson",
  "Find every TODO in the repo": "budget.ndjson"
}
```

- [ ] **Step 2: Write the fake binary**

```python
#!/usr/bin/env python3
"""Fake `claude` for tests. Replays a recorded stream-json transcript for a known prompt."""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
STREAMS = HERE.parent / "fixtures" / "streams"
VALUE_FLAGS = {
    "--model", "--output-format", "--tools", "--allowedTools", "--allowed-tools",
    "--permission-prompts", "--max-budget-usd", "--setting-sources", "--settings",
    "--permission-mode",
}


def prompt_from_argv(argv: list[str]) -> str:
    it = iter(argv)
    positional = []
    for a in it:
        if a in VALUE_FLAGS:
            next(it, None)
        elif a.startswith("-"):
            continue
        else:
            positional.append(a)
    return positional[0] if positional else ""


def main() -> int:
    argv = sys.argv[1:]
    if os.environ.get("FAKE_CLAUDE_LOG"):
        with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
            f.write(json.dumps({
                "argv": argv,
                "cwd": os.getcwd(),
                "CLAUDE_CONFIG_DIR": os.environ.get("CLAUDE_CONFIG_DIR"),
                "has_api_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
            }) + "\n")
    if os.environ.get("FAKE_CLAUDE_AUTH_FAIL"):
        sys.stderr.write("Invalid API key · Please run /login\n")
        return 1
    if os.environ.get("FAKE_CLAUDE_FAIL"):
        sys.stderr.write("fake failure\n")
        return 1
    prompt = prompt_from_argv(argv)
    index = json.loads((STREAMS / "index.json").read_text())
    name = index.get(prompt, "no_fire.ndjson")
    delay = float(os.environ.get("FAKE_CLAUDE_DELAY", "0.05"))
    lines = (STREAMS / name).read_text().splitlines()
    for i, line in enumerate(lines):
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        if i == 0 and os.environ.get("FAKE_CLAUDE_HANG"):
            time.sleep(3600)
        time.sleep(delay)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Run: `chmod +x tests/fake_claude/claude`

- [ ] **Step 3: Write conftest.py**

```python
# tests/conftest.py
import os
from pathlib import Path

import pytest

FAKE_DIR = Path(__file__).parent / "fake_claude"


@pytest.fixture
def fake_claude_path(monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PATH", f"{FAKE_DIR}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-real")
    for var in ("FAKE_CLAUDE_HANG", "FAKE_CLAUDE_FAIL", "FAKE_CLAUDE_AUTH_FAIL", "FAKE_CLAUDE_LOG"):
        monkeypatch.delenv(var, raising=False)
    return FAKE_DIR


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills"
    for name in ("tomato-recipes", "nextjs-app-router", "other"):
        (d / name).mkdir(parents=True)
        (d / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} skill\n---\nbody\n")
    return d


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    (d / "src").mkdir(parents=True)
    (d / "src" / "main.ts").write_text("export const x = 1;\n")
    (d / ".git").mkdir()
    (d / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (d / "node_modules" / "left-pad").mkdir(parents=True)
    (d / "node_modules" / "left-pad" / "index.js").write_text("module.exports = 1;\n")
    return d
```

- [ ] **Step 4: Write the failing test**

```python
# tests/test_fake_claude.py
import json
import os
import subprocess
from pathlib import Path


def test_fake_claude_replays_known_prompt(fake_claude_path: Path, tmp_path: Path):
    log = tmp_path / "log.jsonl"
    out = subprocess.run(
        ["claude", "-p", "Give me a quick recipe for a tomato salsa.", "--model", "haiku",
         "--tools", "Read,Grep,Glob,Skill", "--bare"],
        capture_output=True, text=True,
        env={**os.environ, "FAKE_CLAUDE_LOG": str(log), "CLAUDE_CONFIG_DIR": str(tmp_path / "cfg")},
    )
    assert out.returncode == 0
    assert '"name":"Skill"' in out.stdout
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["CLAUDE_CONFIG_DIR"] == str(tmp_path / "cfg")
    assert entry["has_api_key"] is True


def test_fake_claude_unknown_prompt_is_no_fire(fake_claude_path: Path):
    out = subprocess.run(["claude", "-p", "totally unknown"], capture_output=True, text=True)
    assert out.returncode == 0
    assert '"name":"Skill"' not in out.stdout
    assert '"type":"result"' in out.stdout


def test_fake_claude_fail_mode(fake_claude_path: Path):
    out = subprocess.run(["claude", "-p", "x"], capture_output=True, text=True,
                         env={**os.environ, "FAKE_CLAUDE_FAIL": "1"})
    assert out.returncode == 1
    assert "fake failure" in out.stderr
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_fake_claude.py -q`
Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add tests/fake_claude/claude tests/fixtures/streams/index.json tests/conftest.py tests/test_fake_claude.py
git commit -m "test: fake claude binary that replays recorded transcripts

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Session runner (subprocess, stream, kill)

**Files:**
- Create: `src/skillscope/runner/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `parse_line` (stream.py), `SessionState` (decision.py), `Prompt`, `Session`, `SessionOptions`, `SessionRecord`, `Outcome`, `CostSource` (models.py).
- Produces:
  - `PRICES_PER_MTOK: dict[str, tuple[float, float, float, float]]` (input, output, cache_write, cache_read)
  - `estimate_cost(usage: Usage, model: str | None) -> float`
  - `build_argv(prompt_text: str, opts: SessionOptions) -> list[str]`
  - `build_env(opts: SessionOptions, base: Mapping[str, str]) -> dict[str, str]`
  - `async def run(workspace: Path, prompt: Prompt, session: Session, opts: SessionOptions) -> SessionRecord`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_session.py
import json
import os
from pathlib import Path

import pytest

from skillscope.models import CostSource, Outcome, Prompt, Session, SessionOptions
from skillscope.runner.session import build_argv, build_env, estimate_cost, run
from skillscope.runner.stream import Usage


def opts_for(tmp_path: Path, **kw) -> SessionOptions:
    raw = tmp_path / "raw"
    raw.mkdir(exist_ok=True)
    cfg = tmp_path / "cfg"
    cfg.mkdir(exist_ok=True)
    return SessionOptions(run_id="t", config_dir=cfg, raw_dir=raw, **kw)


def test_estimate_cost_haiku():
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000,
              cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    assert estimate_cost(u, "haiku") == pytest.approx(1.0 + 5.0 + 1.25 + 0.10)
    assert estimate_cost(u, "claude-haiku-4-5-20251001") == pytest.approx(7.35)


def test_estimate_cost_unknown_model_uses_haiku_and_is_positive():
    assert estimate_cost(Usage(output_tokens=1000), None) > 0


def test_build_argv_shape(tmp_path: Path):
    argv = build_argv("hello world", opts_for(tmp_path, model="haiku", budget_usd=0.07))
    assert argv[0] == "claude"
    assert "--bare" in argv
    assert argv[argv.index("-p") + 1] == "hello world"
    assert argv[argv.index("--model") + 1] == "haiku"
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob,Skill"
    assert argv[argv.index("--allowedTools") + 1] == "Read,Grep,Glob,Skill"
    assert argv[argv.index("--permission-prompts") + 1] == "none"
    assert argv[argv.index("--max-budget-usd") + 1] == "0.07"


def test_build_env_sets_config_dir_and_passes_key_only(tmp_path: Path):
    o = opts_for(tmp_path)
    env = build_env(o, {"ANTHROPIC_API_KEY": "k", "PATH": "/bin", "HOME": "/h", "SECRET": "x"})
    assert env["CLAUDE_CONFIG_DIR"] == str(o.config_dir)
    assert env["ANTHROPIC_API_KEY"] == "k"
    assert env["PATH"] == "/bin"
    assert env["HOME"] == "/h"
    assert "SECRET" not in env


async def test_run_fire_kills_early_and_estimates_cost(fake_claude_path: Path, tmp_path: Path):
    o = opts_for(tmp_path)
    p = Prompt(id="p1", text="Give me a quick recipe for a tomato salsa.", expected="tomato-recipes", origin="task")
    s = Session(config="full", prompt_id="p1", repeat=0)
    rec = await run(tmp_path, p, s, o)
    assert rec.outcome is Outcome.FIRED
    assert rec.first_skill == "tomato-recipes"
    assert rec.matched is True
    assert rec.cost_source is CostSource.ESTIMATED
    assert rec.cost_usd > 0
    assert rec.skills_loaded == 2
    assert rec.claude_version == "2.1.280"
    raw = rec.raw_path.read_text().splitlines()
    assert rec.raw_path.name == "full_p1_0.ndjson"
    assert not any('"type":"result"' in line for line in raw), "should have been killed before result"


async def test_run_no_fire_end_turn_kills_before_result(fake_claude_path: Path, tmp_path: Path):
    o = opts_for(tmp_path)
    p = Prompt(id="c1", text="What is the capital of France? Answer in one word.", expected="none", origin="control")
    s = Session(config="full", prompt_id="c1", repeat=0)
    rec = await run(tmp_path, p, s, o)
    assert rec.outcome is Outcome.NO_FIRE
    assert rec.matched is None
    # end_turn kill happens before result; cost is therefore estimated
    assert rec.cost_source is CostSource.ESTIMATED


async def test_run_budget_outcome(fake_claude_path: Path, tmp_path: Path):
    o = opts_for(tmp_path)
    p = Prompt(id="b1", text="Find every TODO in the repo", expected="none", origin="control")
    rec = await run(tmp_path, p, Session(config="full", prompt_id="b1", repeat=0), o)
    assert rec.outcome is Outcome.BUDGET
    assert rec.cost_source is CostSource.RESULT
    assert rec.cost_usd == pytest.approx(0.051)


async def test_run_timeout(fake_claude_path: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_HANG", "1")
    o = opts_for(tmp_path, timeout_s=0.5)
    p = Prompt(id="p1", text="Give me a quick recipe for a tomato salsa.", expected="tomato-recipes", origin="task")
    rec = await run(tmp_path, p, Session(config="full", prompt_id="p1", repeat=0), o)
    assert rec.outcome is Outcome.TIMEOUT
    assert rec.duration_ms < 5000


async def test_run_error_captures_stderr(fake_claude_path: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_FAIL", "1")
    o = opts_for(tmp_path)
    p = Prompt(id="p1", text="x", expected="a", origin="task")
    rec = await run(tmp_path, p, Session(config="full", prompt_id="p1", repeat=0), o)
    assert rec.outcome is Outcome.ERROR
    assert "fake failure" in (rec.error or "")


async def test_run_passes_config_dir_and_cwd(fake_claude_path: Path, tmp_path: Path, monkeypatch):
    log = tmp_path / "log.jsonl"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    o = opts_for(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    p = Prompt(id="p1", text="x", expected="a", origin="task")
    await run(ws, p, Session(config="full", prompt_id="p1", repeat=0), o)
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["cwd"] == str(ws)
    assert entry["CLAUDE_CONFIG_DIR"] == str(o.config_dir)
    assert os.path.expanduser("~/.claude") not in entry["CLAUDE_CONFIG_DIR"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_session.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'skillscope.runner.session'`

- [ ] **Step 3: Write session.py**

```python
# src/skillscope/runner/session.py
"""The only module that knows how Claude Code is invoked."""

from __future__ import annotations

import asyncio
import os
import signal
import time
from collections.abc import Mapping
from pathlib import Path

from skillscope.models import CostSource, Outcome, Prompt, Session, SessionOptions, SessionRecord
from skillscope.runner.decision import SessionState
from skillscope.runner.stream import Usage, parse_line

TOOLS = "Read,Grep,Glob,Skill"

# (input, output, cache_write, cache_read) USD per million tokens. Haiku 4.5 list prices.
PRICES_PER_MTOK: dict[str, tuple[float, float, float, float]] = {
    "haiku": (1.00, 5.00, 1.25, 0.10),
    "claude-haiku-4-5": (1.00, 5.00, 1.25, 0.10),
}
_PASSTHROUGH_ENV = ("PATH", "HOME", "ANTHROPIC_API_KEY", "TMPDIR", "LANG", "LC_ALL", "TERM")


def _prices(model: str | None) -> tuple[float, float, float, float]:
    if model:
        for key, prices in PRICES_PER_MTOK.items():
            if model == key or model.startswith(key):
                return prices
    return PRICES_PER_MTOK["haiku"]


def estimate_cost(usage: Usage, model: str | None) -> float:
    inp, out, cw, cr = _prices(model)
    return (
        usage.input_tokens * inp
        + usage.output_tokens * out
        + usage.cache_creation_input_tokens * cw
        + usage.cache_read_input_tokens * cr
    ) / 1_000_000


def build_argv(prompt_text: str, opts: SessionOptions) -> list[str]:
    return [
        opts.claude_bin,
        "-p", prompt_text,
        "--bare",
        "--model", opts.model,
        "--output-format", "stream-json",
        "--verbose",
        "--tools", TOOLS,
        "--allowedTools", TOOLS,
        "--permission-prompts", "none",
        "--max-budget-usd", f"{opts.budget_usd:g}",
    ]


def build_env(opts: SessionOptions, base: Mapping[str, str]) -> dict[str, str]:
    env = {k: base[k] for k in _PASSTHROUGH_ENV if k in base}
    env["CLAUDE_CONFIG_DIR"] = str(opts.config_dir)
    return env


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass


async def run(workspace: Path, prompt: Prompt, session: Session, opts: SessionOptions) -> SessionRecord:
    raw_path = opts.raw_dir / f"{session.config}_{session.prompt_id}_{session.repeat}.ndjson"
    state = SessionState()
    started = time.monotonic()
    error: str | None = None
    killed = False

    proc = await asyncio.create_subprocess_exec(
        *build_argv(prompt.text, opts),
        cwd=workspace,
        env=build_env(opts, os.environ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stdout is not None and proc.stderr is not None
    stderr_task = asyncio.create_task(proc.stderr.read())
    deadline = started + opts.timeout_s

    with raw_path.open("w") as raw:
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                data = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
                if not data:
                    break
                line = data.decode("utf-8", errors="replace").rstrip("\n")
                raw.write(line + "\n")
                verdict = state.feed(parse_line(line))
                if verdict is not None and verdict.kill:
                    killed = True
                    _kill_group(proc)
                    break
        except TimeoutError:
            state.on_timeout()
            killed = True
            _kill_group(proc)
        except asyncio.CancelledError:
            # Pool shutdown or Ctrl-C: never leave a paid session running.
            _kill_group(proc)
            stderr_task.cancel()
            raise

    try:
        returncode = await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError:
        _kill_group(proc)
        returncode = await proc.wait()
    stderr_bytes = await stderr_task
    stderr_tail = stderr_bytes.decode("utf-8", errors="replace")[-2000:]

    if not killed:
        outcome = state.on_exit(returncode)
    else:
        outcome = state.outcome or Outcome.ERROR
    if outcome is Outcome.ERROR:
        error = stderr_tail.strip() or f"exit {returncode}"

    if state.result is not None and state.result.total_cost_usd is not None:
        cost, source = state.result.total_cost_usd, CostSource.RESULT
    else:
        cost, source = estimate_cost(state.usage, state.model or opts.model), CostSource.ESTIMATED

    duration_ms = int((time.monotonic() - started) * 1000)
    if state.result is not None and state.result.duration_ms is not None:
        duration_ms = int(state.result.duration_ms)

    return SessionRecord(
        run_id=opts.run_id,
        config=session.config,
        prompt_id=session.prompt_id,
        repeat=session.repeat,
        outcome=outcome,
        skills_invoked=list(state.skills_invoked),
        first_skill=state.first_skill,
        expected=list(prompt.expected),
        matched=prompt.matches(state.first_skill),
        exploration_calls=state.exploration_calls,
        # input_tokens is everything the model read: fresh + cache write + cache read
        input_tokens=state.usage.input_tokens
        + state.usage.cache_creation_input_tokens
        + state.usage.cache_read_input_tokens,
        output_tokens=state.usage.output_tokens,
        cost_usd=cost,
        cost_source=source,
        duration_ms=duration_ms,
        model=state.model or opts.model,
        claude_version=state.claude_version,
        error=error,
        raw_path=raw_path,
        skills_loaded=state.skills_loaded,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session.py -q && uv run ruff check .`
Expected: `10 passed`. If `test_run_timeout` hangs, the process group kill did not reach the fake: check that `start_new_session=True` is set and that `os.killpg` is used, not `proc.kill()`.

- [ ] **Step 5: If the day-0 probe changed the argv, apply it now**

If the spec's section 3.1 says the working form was `--setting-sources project` rather than `--bare`, or that `Skill` must not be in `--tools`, edit `build_argv` and `test_build_argv_shape` together so the test documents the confirmed shape. Re-run the tests.

- [ ] **Step 6: Commit**

```bash
git add src/skillscope/runner/session.py tests/test_session.py
git commit -m "feat(runner): session wrapper with early kill, timeout and cost estimate

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Workspace builder

**Files:**
- Create: `src/skillscope/runner/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: `Config` (models.py).
- Produces: `build(repo: Path, skills_dir: Path, config: Config, root: Path) -> Path` returning `root / config.name`. Raises `FileNotFoundError` naming the missing skill folder.
- `EXCLUDE = (".git", "node_modules", ".next", "dist", ".skillscope", "__pycache__")`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_workspace.py
from pathlib import Path

import pytest

from skillscope.models import Config
from skillscope.runner.workspace import build


def test_build_copies_repo_without_excluded_dirs(repo_dir: Path, skills_dir: Path, tmp_path: Path):
    ws = build(repo_dir, skills_dir, Config(name="minimal", skills=["tomato-recipes"]), tmp_path / "ws")
    assert ws == tmp_path / "ws" / "minimal"
    assert (ws / "src" / "main.ts").read_text() == "export const x = 1;\n"
    assert not (ws / ".git").exists()
    assert not (ws / "node_modules").exists()


def test_build_installs_only_the_configs_skills(repo_dir: Path, skills_dir: Path, tmp_path: Path):
    ws = build(repo_dir, skills_dir, Config(name="minimal", skills=["tomato-recipes"]), tmp_path / "ws")
    installed = sorted(p.name for p in (ws / ".claude" / "skills").iterdir())
    assert installed == ["tomato-recipes"]
    assert (ws / ".claude" / "skills" / "tomato-recipes" / "SKILL.md").exists()


def test_build_star_installs_all_skills(repo_dir: Path, skills_dir: Path, tmp_path: Path):
    ws = build(repo_dir, skills_dir, Config(name="full", skills="*"), tmp_path / "ws")
    installed = sorted(p.name for p in (ws / ".claude" / "skills").iterdir())
    assert installed == ["nextjs-app-router", "other", "tomato-recipes"]


def test_build_is_idempotent(repo_dir: Path, skills_dir: Path, tmp_path: Path):
    build(repo_dir, skills_dir, Config(name="full", skills="*"), tmp_path / "ws")
    ws = build(repo_dir, skills_dir, Config(name="full", skills=["other"]), tmp_path / "ws")
    installed = sorted(p.name for p in (ws / ".claude" / "skills").iterdir())
    assert installed == ["other"]


def test_build_missing_skill_raises(repo_dir: Path, skills_dir: Path, tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="ghost"):
        build(repo_dir, skills_dir, Config(name="x", skills=["ghost"]), tmp_path / "ws")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_workspace.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write workspace.py**

```python
# src/skillscope/runner/workspace.py
from __future__ import annotations

import shutil
from pathlib import Path

from skillscope.models import Config

EXCLUDE = (".git", "node_modules", ".next", "dist", ".skillscope", "__pycache__")


def build(repo: Path, skills_dir: Path, config: Config, root: Path) -> Path:
    """Copy `repo` to root/<config.name> and install the config's skills. One per config."""
    dest = root / config.name
    names = config.resolve(skills_dir)
    for name in names:
        if not (skills_dir / name).is_dir():
            raise FileNotFoundError(f"skill folder not found: {name} (in {skills_dir})")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(repo, dest, ignore=shutil.ignore_patterns(*EXCLUDE), symlinks=True)
    target = dest / ".claude" / "skills"
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(skills_dir / name, target / name)
    return dest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_workspace.py -q && uv run ruff check .`
Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/skillscope/runner/workspace.py tests/test_workspace.py
git commit -m "feat(runner): per-config workspace builder

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Event log store

**Files:**
- Create: `src/skillscope/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `SessionRecord`, `SessionKey` (models.py).
- Produces: `class EventLog`: `__init__(path: Path)`, `append(record: SessionRecord) -> None`, `load() -> list[SessionRecord]`, `done_keys() -> set[SessionKey]`. `load` skips a torn last line and logs a warning via `logging.getLogger("skillscope.store")`; a malformed non-last line raises `ValueError`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_store.py
import logging
from pathlib import Path

import pytest

from skillscope.models import SessionRecord
from skillscope.store import EventLog


def rec(config: str, pid: str, repeat: int) -> SessionRecord:
    return SessionRecord(
        run_id="r", config=config, prompt_id=pid, repeat=repeat, outcome="fired",
        skills_invoked=["a"], first_skill="a", expected=["a"], matched=True,
        exploration_calls=0, input_tokens=1, output_tokens=1, cost_usd=0.001,
        cost_source="estimated", duration_ms=1, model="haiku", claude_version="2.1.280",
        error=None, raw_path=Path("raw/x.ndjson"),
    )


def test_append_then_load_roundtrip(tmp_path: Path):
    log = EventLog(tmp_path / "events.jsonl")
    log.append(rec("full", "p1", 0))
    log.append(rec("full", "p2", 0))
    loaded = log.load()
    assert [r.prompt_id for r in loaded] == ["p1", "p2"]
    assert log.done_keys() == {("full", "p1", 0), ("full", "p2", 0)}


def test_load_missing_file_is_empty(tmp_path: Path):
    log = EventLog(tmp_path / "nope.jsonl")
    assert log.load() == []
    assert log.done_keys() == set()


def test_torn_last_line_is_skipped_with_warning(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(rec("full", "p1", 0))
    with path.open("a") as f:
        f.write('{"run_id": "r", "config": "full", "prompt_id": "p2"')  # torn, no newline
    with caplog.at_level(logging.WARNING, logger="skillscope.store"):
        loaded = log.load()
    assert [r.prompt_id for r in loaded] == ["p1"]
    assert "torn" in caplog.text


def test_malformed_middle_line_raises(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.append(rec("full", "p1", 0))
    with path.open("a") as f:
        f.write("garbage\n")
    log.append(rec("full", "p2", 0))
    with pytest.raises(ValueError, match="line 2"):
        log.load()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write store.py**

```python
# src/skillscope/store.py
from __future__ import annotations

import logging
import os
from pathlib import Path

from pydantic import ValidationError

from skillscope.models import SessionKey, SessionRecord

log = logging.getLogger("skillscope.store")


class EventLog:
    """Append-only JSONL of SessionRecord, one per line, fsynced per append."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: SessionRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as f:
            f.write(record.model_dump_json() + "\n")
            f.flush()
            os.fsync(f.fileno())

    def load(self) -> list[SessionRecord]:
        if not self.path.exists():
            return []
        text = self.path.read_text()
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        records: list[SessionRecord] = []
        last = len(lines) - 1
        for i, line in enumerate(lines):
            try:
                records.append(SessionRecord.model_validate_json(line))
            except (ValidationError, ValueError) as e:
                if i == last and not text.endswith("\n"):
                    log.warning("skipping torn last line in %s", self.path)
                    continue
                raise ValueError(f"{self.path}: malformed record at line {i + 1}: {e}") from e
        return records

    def done_keys(self) -> set[SessionKey]:
        return {r.key() for r in self.load()}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py -q && uv run ruff check .`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/skillscope/store.py tests/test_store.py
git commit -m "feat(store): append-only fsynced event log with torn-line recovery

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: Planner

**Files:**
- Create: `src/skillscope/runner/plan.py`
- Test: `tests/test_plan.py`

**Interfaces:**
- Consumes: `Prompt`, `Config`, `Session`, `SessionKey` (models.py).
- Produces: `plan(prompts: list[Prompt], configs: list[Config], repeats: int, done: set[SessionKey]) -> list[Session]`. Order: config, then prompt, then repeat (0-based). Excludes keys in `done`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_plan.py
from skillscope.models import Config, Prompt
from skillscope.runner.plan import plan


def prompts():
    return [Prompt(id="p1", text="a", expected="s", origin="task"),
            Prompt(id="p2", text="b", expected="none", origin="control")]


def configs():
    return [Config(name="minimal", skills=["s"]), Config(name="full", skills="*")]


def test_plan_orders_config_prompt_repeat():
    sessions = plan(prompts(), configs(), repeats=2, done=set())
    assert [s.key() for s in sessions] == [
        ("minimal", "p1", 0), ("minimal", "p1", 1),
        ("minimal", "p2", 0), ("minimal", "p2", 1),
        ("full", "p1", 0), ("full", "p1", 1),
        ("full", "p2", 0), ("full", "p2", 1),
    ]


def test_plan_skips_done():
    done = {("minimal", "p1", 0), ("full", "p2", 1)}
    sessions = plan(prompts(), configs(), repeats=2, done=done)
    assert len(sessions) == 6
    assert ("minimal", "p1", 0) not in {s.key() for s in sessions}


def test_plan_repeats_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        plan(prompts(), configs(), repeats=0, done=set())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_plan.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write plan.py**

```python
# src/skillscope/runner/plan.py
from __future__ import annotations

from skillscope.models import Config, Prompt, Session, SessionKey


def plan(prompts: list[Prompt], configs: list[Config], repeats: int, done: set[SessionKey]) -> list[Session]:
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    sessions: list[Session] = []
    for config in configs:
        for prompt in prompts:
            for r in range(repeats):
                s = Session(config=config.name, prompt_id=prompt.id, repeat=r)
                if s.key() not in done:
                    sessions.append(s)
    return sessions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_plan.py -q && uv run ruff check .`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/skillscope/runner/plan.py tests/test_plan.py
git commit -m "feat(runner): planner with resume via done keys

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 11: Pool with cost cap and auth abort

**Files:**
- Create: `src/skillscope/runner/pool.py`
- Test: `tests/test_pool.py`

**Interfaces:**
- Consumes: `Session`, `Prompt`, `SessionOptions`, `SessionRecord`, `Outcome` (models.py); a runner callable with the signature of `session.run`.
- Produces:
  - `Runner = Callable[[Path, Prompt, Session, SessionOptions], Awaitable[SessionRecord]]`
  - `class Pool`: `__init__(runner: Runner, parallel: int, cost_cap_usd: float)`; attributes after a run: `total_cost: float`, `skipped: int` (sessions not started because of the cap or an abort), `aborted_reason: str | None`; `async def run(sessions, prompts: dict[str, Prompt], workspaces: dict[str, Path], opts: SessionOptions) -> AsyncIterator[SessionRecord]`
  - `is_auth_error(record) -> bool`: outcome is error and the error text contains one of `"Invalid API key"`, `"authentication"`, `"401"`, `"not logged in"` (case-insensitive).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pool.py
import asyncio
from pathlib import Path

from skillscope.models import Outcome, Prompt, Session, SessionOptions, SessionRecord
from skillscope.runner.pool import Pool, is_auth_error


def rec(session: Session, outcome=Outcome.FIRED, cost=0.01, error=None) -> SessionRecord:
    return SessionRecord(
        run_id="r", config=session.config, prompt_id=session.prompt_id, repeat=session.repeat,
        outcome=outcome, skills_invoked=[], first_skill=None, expected=[], matched=None,
        exploration_calls=0, input_tokens=0, output_tokens=0, cost_usd=cost,
        cost_source="estimated", duration_ms=1, model="haiku", claude_version=None,
        error=error, raw_path=Path("x"),
    )


def make(n: int) -> tuple[list[Session], dict[str, Prompt], dict[str, Path]]:
    sessions = [Session(config="full", prompt_id=f"p{i}", repeat=0) for i in range(n)]
    prompts = {f"p{i}": Prompt(id=f"p{i}", text="t", expected="a", origin="task") for i in range(n)}
    return sessions, prompts, {"full": Path("/tmp/ws")}


def opts(tmp_path: Path) -> SessionOptions:
    return SessionOptions(run_id="r", config_dir=tmp_path, raw_dir=tmp_path)


async def test_pool_runs_all_and_bounds_parallelism(tmp_path: Path):
    running = 0
    peak = 0

    async def runner(ws, prompt, session, o):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return rec(session)

    sessions, prompts, ws = make(10)
    pool = Pool(runner, parallel=3, cost_cap_usd=100)
    out = [r async for r in pool.run(sessions, prompts, ws, opts(tmp_path))]
    assert len(out) == 10
    assert peak == 3
    assert pool.skipped == 0
    assert pool.total_cost == 0.1


async def test_pool_stops_launching_at_cost_cap(tmp_path: Path):
    async def runner(ws, prompt, session, o):
        await asyncio.sleep(0.01)
        return rec(session, cost=0.03)

    sessions, prompts, ws = make(10)
    pool = Pool(runner, parallel=1, cost_cap_usd=0.05)
    out = [r async for r in pool.run(sessions, prompts, ws, opts(tmp_path))]
    assert len(out) == 2  # 0.03 < cap, 0.06 >= cap → stop
    assert pool.skipped == 8
    assert pool.aborted_reason is not None and "cost cap" in pool.aborted_reason


async def test_pool_aborts_on_auth_error(tmp_path: Path):
    async def runner(ws, prompt, session, o):
        await asyncio.sleep(0.01)
        return rec(session, outcome=Outcome.ERROR, error="Invalid API key · Please run /login")

    sessions, prompts, ws = make(6)
    pool = Pool(runner, parallel=2, cost_cap_usd=100)
    out = [r async for r in pool.run(sessions, prompts, ws, opts(tmp_path))]
    assert 1 <= len(out) <= 2
    assert pool.aborted_reason is not None and "auth" in pool.aborted_reason
    assert pool.skipped == 6 - len(out)


async def test_pool_runner_exception_becomes_error_record(tmp_path: Path):
    async def runner(ws, prompt, session, o):
        raise RuntimeError("boom")

    sessions, prompts, ws = make(2)
    pool = Pool(runner, parallel=2, cost_cap_usd=100)
    out = [r async for r in pool.run(sessions, prompts, ws, opts(tmp_path))]
    assert [r.outcome for r in out] == [Outcome.ERROR, Outcome.ERROR]
    assert all("boom" in (r.error or "") for r in out)


def test_is_auth_error():
    s = Session(config="c", prompt_id="p", repeat=0)
    assert is_auth_error(rec(s, Outcome.ERROR, error="Invalid API key")) is True
    assert is_auth_error(rec(s, Outcome.ERROR, error="segfault")) is False
    assert is_auth_error(rec(s, Outcome.FIRED)) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pool.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write pool.py**

```python
# src/skillscope/runner/pool.py
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from skillscope.models import CostSource, Outcome, Prompt, Session, SessionOptions, SessionRecord

Runner = Callable[[Path, Prompt, Session, SessionOptions], Awaitable[SessionRecord]]

_AUTH_MARKERS = ("invalid api key", "authentication", "401", "not logged in")


def is_auth_error(record: SessionRecord) -> bool:
    if record.outcome is not Outcome.ERROR or not record.error:
        return False
    text = record.error.lower()
    return any(m in text for m in _AUTH_MARKERS)


def _error_record(session: Session, prompt: Prompt, opts: SessionOptions, message: str) -> SessionRecord:
    return SessionRecord(
        run_id=opts.run_id, config=session.config, prompt_id=session.prompt_id,
        repeat=session.repeat, outcome=Outcome.ERROR, skills_invoked=[], first_skill=None,
        expected=list(prompt.expected), matched=None, exploration_calls=0, input_tokens=0,
        output_tokens=0, cost_usd=0.0, cost_source=CostSource.ESTIMATED, duration_ms=0,
        model=opts.model, claude_version=None, error=message,
        raw_path=opts.raw_dir / f"{session.config}_{session.prompt_id}_{session.repeat}.ndjson",
    )


class Pool:
    def __init__(self, runner: Runner, parallel: int, cost_cap_usd: float) -> None:
        self.runner = runner
        self.parallel = max(1, parallel)
        self.cost_cap_usd = cost_cap_usd
        self.total_cost = 0.0
        self.skipped = 0
        self.aborted_reason: str | None = None

    async def run(
        self,
        sessions: list[Session],
        prompts: dict[str, Prompt],
        workspaces: dict[str, Path],
        opts: SessionOptions,
    ) -> AsyncIterator[SessionRecord]:
        queue: asyncio.Queue[Session] = asyncio.Queue()
        for s in sessions:
            queue.put_nowait(s)
        results: asyncio.Queue[SessionRecord | None] = asyncio.Queue()
        self.total_cost = 0.0
        self.skipped = 0
        self.aborted_reason = None

        async def worker() -> None:
            while True:
                try:
                    session = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                if self.aborted_reason is not None:
                    self.skipped += 1
                    continue
                if self.total_cost >= self.cost_cap_usd:
                    self.aborted_reason = f"cost cap {self.cost_cap_usd:.2f} USD reached"
                    self.skipped += 1
                    continue
                prompt = prompts[session.prompt_id]
                try:
                    record = await self.runner(workspaces[session.config], prompt, session, opts)
                except Exception as e:  # noqa: BLE001 - one bad session must not stop the run
                    record = _error_record(session, prompt, opts, f"{type(e).__name__}: {e}")
                self.total_cost += record.cost_usd
                if is_auth_error(record) and self.aborted_reason is None:
                    self.aborted_reason = "auth error from Claude Code; remaining sessions skipped"
                await results.put(record)

        async def supervise() -> None:
            await asyncio.gather(*(worker() for _ in range(self.parallel)))
            await results.put(None)

        sup = asyncio.create_task(supervise())
        try:
            while True:
                item = await results.get()
                if item is None:
                    break
                yield item
        finally:
            if not sup.done():
                sup.cancel()
                try:
                    await sup
                except asyncio.CancelledError:
                    pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pool.py -q && uv run ruff check .`
Expected: `5 passed`. Note `test_pool_stops_launching_at_cost_cap` depends on the cap check being before each start and on `total_cost` being updated before the next dequeue in a single-worker pool.

- [ ] **Step 5: Commit**

```bash
git add src/skillscope/runner/pool.py tests/test_pool.py
git commit -m "feat(runner): bounded pool with cost cap and auth abort

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: Resume integration test (parallel, kill midway, rerun)

**Files:**
- Test: `tests/test_resume.py`

**Interfaces:**
- Consumes: `plan`, `Pool`, `EventLog`, `session.run`, `workspace.build`, fake claude.

- [ ] **Step 1: Write the integration test**

```python
# tests/test_resume.py
"""End-to-end over the fake binary: 4 parallel sessions, interrupted midway, then resumed."""
import asyncio
from pathlib import Path

from skillscope.models import Config, Prompt, SessionOptions
from skillscope.runner import session as session_mod
from skillscope.runner.plan import plan
from skillscope.runner.pool import Pool
from skillscope.runner.workspace import build
from skillscope.store import EventLog

PROMPTS = [
    Prompt(id="p1", text="Give me a quick recipe for a tomato salsa.", expected="tomato-recipes", origin="task"),
    Prompt(id="p2", text="Add a loading skeleton to the dashboard route while data fetches", expected="nextjs-app-router", origin="task"),
    Prompt(id="c1", text="What is the capital of France? Answer in one word.", expected="none", origin="control"),
]
CONFIGS = [Config(name="minimal", skills=["tomato-recipes"]), Config(name="full", skills="*")]


async def drive(run_dir: Path, workspaces: dict[str, Path], stop_after: int | None) -> int:
    log = EventLog(run_dir / "events.jsonl")
    opts = SessionOptions(run_id="r", config_dir=run_dir / "cfg", raw_dir=run_dir / "raw", timeout_s=10)
    opts.config_dir.mkdir(exist_ok=True)
    opts.raw_dir.mkdir(exist_ok=True)
    sessions = plan(PROMPTS, CONFIGS, repeats=2, done=log.done_keys())
    pool = Pool(session_mod.run, parallel=4, cost_cap_usd=100)
    n = 0
    agen = pool.run(sessions, {p.id: p for p in PROMPTS}, workspaces, opts)
    try:
        async for record in agen:
            log.append(record)
            n += 1
            if stop_after is not None and n >= stop_after:
                break
    finally:
        await agen.aclose()
    return n


async def test_interrupt_then_resume_yields_exactly_one_record_per_key(
    fake_claude_path: Path, repo_dir: Path, skills_dir: Path, tmp_path: Path
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    workspaces = {c.name: build(repo_dir, skills_dir, c, run_dir / "ws") for c in CONFIGS}

    first = await drive(run_dir, workspaces, stop_after=5)
    assert first == 5
    keys_after_first = EventLog(run_dir / "events.jsonl").done_keys()
    assert len(keys_after_first) == 5

    second = await drive(run_dir, workspaces, stop_after=None)
    assert second == 12 - 5

    records = EventLog(run_dir / "events.jsonl").load()
    keys = [r.key() for r in records]
    assert len(keys) == 12
    assert len(set(keys)) == 12, "resume must not duplicate any (config, prompt, repeat)"

    third = await drive(run_dir, workspaces, stop_after=None)
    assert third == 0


async def test_fire_counts_from_fake_are_deterministic(
    fake_claude_path: Path, repo_dir: Path, skills_dir: Path, tmp_path: Path
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    workspaces = {c.name: build(repo_dir, skills_dir, c, run_dir / "ws") for c in CONFIGS}
    await drive(run_dir, workspaces, stop_after=None)
    records = EventLog(run_dir / "events.jsonl").load()
    by = {(r.config, r.prompt_id, r.repeat): r for r in records}
    assert by[("full", "p1", 0)].first_skill == "tomato-recipes"
    assert by[("full", "p2", 1)].first_skill == "nextjs-app-router"
    assert by[("minimal", "c1", 0)].outcome.value == "no_fire"
    await asyncio.sleep(0)  # let any kill-group cleanup settle
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_resume.py -q`
Expected: `2 passed` in under 10 seconds. If the first test hangs on `aclose()`, the pool's `finally` is not cancelling the supervisor; fix in `pool.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_resume.py
git commit -m "test: parallel interrupt-and-resume integration over the fake binary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: Terminal fire table and summary

**Files:**
- Create: `src/skillscope/report/terminal.py`
- Test: `tests/test_terminal.py`

**Interfaces:**
- Consumes: `SessionRecord`, `Prompt`, `Config`, `Outcome` (models.py).
- Produces:
  - `@dataclass class ConfigSummary`: `config: str`, `sessions: int`, `task_sessions: int`, `wrong_or_none_rate: float`, `per_repeat_rates: list[float]`, `control_sessions: int`, `control_fire_rate: float`, `delta_vs_minimal: float | None`, `errors: int`
  - `summarise(records, prompts, configs, baseline: str = "minimal") -> list[ConfigSummary]`
  - `fire_table(records, prompts, configs) -> rich.table.Table` — rows are skills, one column per config, cell text `"{correct}/{expected} (+{unexpected})"`
  - `summary_table(summaries) -> rich.table.Table`
  - `render(records, prompts, configs, console: rich.console.Console) -> None` prints both.

Definitions: a session is *wrong-or-none* when its prompt is a task prompt and `matched` is not `True` (covers wrong skill, no fire, timeout, budget, error). `control_fire_rate` is the share of control sessions whose outcome is `fired`. `delta_vs_minimal` is this config's wrong-or-none rate minus the baseline's, in percentage points, `None` for the baseline itself or when the baseline is absent.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_terminal.py
from pathlib import Path

from rich.console import Console

from skillscope.models import Config, Outcome, Prompt, SessionRecord
from skillscope.report.terminal import fire_table, render, summarise, summary_table

PROMPTS = [
    Prompt(id="p1", text="a", expected="nextjs", origin="task"),
    Prompt(id="p2", text="b", expected="nextjs", origin="task"),
    Prompt(id="c1", text="c", expected="none", origin="control"),
]
CONFIGS = [Config(name="minimal", skills=["nextjs"]), Config(name="full", skills="*")]


def rec(config, pid, repeat, first, outcome=Outcome.FIRED):
    prompt = {p.id: p for p in PROMPTS}[pid]
    return SessionRecord(
        run_id="r", config=config, prompt_id=pid, repeat=repeat, outcome=outcome,
        skills_invoked=[first] if first else [], first_skill=first, expected=prompt.expected,
        matched=prompt.matches(first), exploration_calls=0, input_tokens=0, output_tokens=0,
        cost_usd=0.01, cost_source="estimated", duration_ms=1, model="haiku",
        claude_version=None, error=None, raw_path=Path("x"),
    )


RECORDS = [
    # minimal: both task prompts correct on both repeats, control never fires
    rec("minimal", "p1", 0, "nextjs"), rec("minimal", "p1", 1, "nextjs"),
    rec("minimal", "p2", 0, "nextjs"), rec("minimal", "p2", 1, "nextjs"),
    rec("minimal", "c1", 0, None, Outcome.NO_FIRE), rec("minimal", "c1", 1, None, Outcome.NO_FIRE),
    # full: p1 correct once and wrong once; p2 no-fire both; control fires once
    rec("full", "p1", 0, "nextjs"), rec("full", "p1", 1, "react"),
    rec("full", "p2", 0, None, Outcome.NO_FIRE), rec("full", "p2", 1, None, Outcome.NO_FIRE),
    rec("full", "c1", 0, "react"), rec("full", "c1", 1, None, Outcome.NO_FIRE),
]


def test_summarise_rates_and_delta():
    s = {x.config: x for x in summarise(RECORDS, PROMPTS, CONFIGS)}
    assert s["minimal"].wrong_or_none_rate == 0.0
    assert s["minimal"].delta_vs_minimal is None
    assert s["full"].task_sessions == 4
    assert s["full"].wrong_or_none_rate == 0.75
    assert s["full"].per_repeat_rates == [0.5, 1.0]
    assert s["full"].control_fire_rate == 0.5
    assert s["full"].delta_vs_minimal == 75.0


def test_fire_table_cells():
    table = fire_table(RECORDS, PROMPTS, CONFIGS)
    console = Console(width=120, record=True)
    console.print(table)
    text = console.export_text()
    assert "nextjs" in text and "react" in text
    assert "4/4 (+0)" in text      # nextjs under minimal
    assert "1/4 (+0)" in text      # nextjs under full
    assert "0/0 (+2)" in text      # react under full: never expected, fired twice


def test_render_prints_both_tables():
    console = Console(width=120, record=True)
    render(RECORDS, PROMPTS, CONFIGS, console)
    text = console.export_text()
    assert "wrong-or-none" in text
    assert "minimal" in text and "full" in text
    assert "+75.0" in text


def test_summary_table_has_one_row_per_config():
    t = summary_table(summarise(RECORDS, PROMPTS, CONFIGS))
    assert t.row_count == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_terminal.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write terminal.py**

```python
# src/skillscope/report/terminal.py
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from rich.console import Console
from rich.table import Table

from skillscope.models import Config, Outcome, Prompt, SessionRecord


@dataclass
class ConfigSummary:
    config: str
    sessions: int
    task_sessions: int
    wrong_or_none_rate: float
    per_repeat_rates: list[float]
    control_sessions: int
    control_fire_rate: float
    delta_vs_minimal: float | None
    errors: int


def _rate(num: int, den: int) -> float:
    return num / den if den else 0.0


def summarise(
    records: list[SessionRecord], prompts: list[Prompt], configs: list[Config], baseline: str = "minimal"
) -> list[ConfigSummary]:
    by_prompt = {p.id: p for p in prompts}
    out: list[ConfigSummary] = []
    for cfg in configs:
        rs = [r for r in records if r.config == cfg.name]
        task = [r for r in rs if by_prompt[r.prompt_id].expected]
        control = [r for r in rs if not by_prompt[r.prompt_id].expected]
        wrong = [r for r in task if r.matched is not True]
        repeats = sorted({r.repeat for r in task})
        per_repeat = [
            _rate(sum(1 for r in wrong if r.repeat == k), sum(1 for r in task if r.repeat == k))
            for k in repeats
        ]
        out.append(
            ConfigSummary(
                config=cfg.name,
                sessions=len(rs),
                task_sessions=len(task),
                wrong_or_none_rate=_rate(len(wrong), len(task)),
                per_repeat_rates=per_repeat,
                control_sessions=len(control),
                control_fire_rate=_rate(sum(1 for r in control if r.outcome is Outcome.FIRED), len(control)),
                delta_vs_minimal=None,
                errors=sum(1 for r in rs if r.outcome is Outcome.ERROR),
            )
        )
    base = next((s for s in out if s.config == baseline), None)
    if base is not None:
        for s in out:
            if s.config != baseline:
                s.delta_vs_minimal = round((s.wrong_or_none_rate - base.wrong_or_none_rate) * 100, 1)
    return out


def fire_table(records: list[SessionRecord], prompts: list[Prompt], configs: list[Config]) -> Table:
    by_prompt = {p.id: p for p in prompts}
    skills: set[str] = set()
    for p in prompts:
        skills.update(p.expected)
    for r in records:
        if r.first_skill:
            skills.add(r.first_skill)
    expected_n: dict[tuple[str, str], int] = defaultdict(int)
    correct_n: dict[tuple[str, str], int] = defaultdict(int)
    unexpected_n: dict[tuple[str, str], int] = defaultdict(int)
    for r in records:
        exp = by_prompt[r.prompt_id].expected
        for s in exp:
            expected_n[(s, r.config)] += 1
        if r.first_skill:
            if r.first_skill in exp:
                correct_n[(r.first_skill, r.config)] += 1
            else:
                unexpected_n[(r.first_skill, r.config)] += 1
    table = Table(title="Fires per skill (correct/expected, +unexpected)")
    table.add_column("skill")
    for cfg in configs:
        table.add_column(cfg.name, justify="right")
    for s in sorted(skills):
        cells = [
            f"{correct_n[(s, c.name)]}/{expected_n[(s, c.name)]} (+{unexpected_n[(s, c.name)]})"
            for c in configs
        ]
        table.add_row(s, *cells)
    return table


def summary_table(summaries: list[ConfigSummary]) -> Table:
    table = Table(title="Per-config summary")
    for col in ("config", "sessions", "wrong-or-none %", "per repeat", "control fire %", "delta vs minimal", "errors"):
        table.add_column(col, justify="right" if col != "config" else "left")
    for s in summaries:
        delta = "" if s.delta_vs_minimal is None else f"{s.delta_vs_minimal:+.1f}"
        table.add_row(
            s.config,
            str(s.sessions),
            f"{s.wrong_or_none_rate * 100:.1f}",
            " ".join(f"{r * 100:.0f}" for r in s.per_repeat_rates),
            f"{s.control_fire_rate * 100:.1f}",
            delta,
            str(s.errors),
        )
    return table


def render(records: list[SessionRecord], prompts: list[Prompt], configs: list[Config], console: Console) -> None:
    console.print(fire_table(records, prompts, configs))
    console.print(summary_table(summarise(records, prompts, configs)))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_terminal.py -q && uv run ruff check .`
Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/skillscope/report/terminal.py tests/test_terminal.py
git commit -m "feat(report): fire-count table and per-config summary

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: CLI (`scan`, `report`)

**Files:**
- Create: `src/skillscope/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces: Typer `app` with commands:
  - `scan REPO -p/--prompts PATH -s/--skills PATH -c/--configs PATH [-j/--parallel 4] [-r/--repeats 3] [-m/--model haiku] [--cost-cap 5.0] [--timeout 120] [--budget 0.05] [-n/--dry-run] [-o/--out DIR]`
  - `report DIR`
  - Run folder default: `.skillscope/runs/<YYYYMMDD-HHMMSS>` under the current directory. `scan` copies `prompts.yaml` and `configs.yaml` into it, builds workspaces under `<run>/workspaces`, sets `CLAUDE_CONFIG_DIR` to `<run>/claude-config`, and writes raw NDJSON to `<run>/raw`.
  - Dry run prints sessions per config, total, and an estimate at `--est-per-session 0.012` USD, then exits 0 without touching the API.
  - Pre-flight: exits 2 with a clear message if `ANTHROPIC_API_KEY` is unset (skipped for dry run).
  - After the run: prints both tables, prints `partial run: N sessions not started (<reason>)` when the pool aborted, and exits 0.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
from pathlib import Path

from typer.testing import CliRunner

from skillscope.cli import app
from skillscope.store import EventLog

runner = CliRunner()

PROMPTS_YAML = """\
- id: p1
  text: Give me a quick recipe for a tomato salsa.
  expected: tomato-recipes
  origin: task
- id: c1
  text: What is the capital of France? Answer in one word.
  expected: none
  origin: control
"""
CONFIGS_YAML = 'minimal: [tomato-recipes]\nfull: "*"\n'


def write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    p = tmp_path / "prompts.yaml"
    c = tmp_path / "configs.yaml"
    p.write_text(PROMPTS_YAML)
    c.write_text(CONFIGS_YAML)
    return p, c


def test_dry_run_prints_plan_and_spends_nothing(repo_dir: Path, skills_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p, c = write_inputs(tmp_path)
    out = tmp_path / "run"
    result = runner.invoke(app, ["scan", str(repo_dir), "-p", str(p), "-s", str(skills_dir), "-c", str(c),
                                 "-r", "3", "-n", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "minimal: 6" in result.output
    assert "full: 6" in result.output
    assert "total: 12" in result.output
    assert "$" in result.output
    assert not (out / "events.jsonl").exists()


def test_scan_requires_api_key(repo_dir: Path, skills_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    p, c = write_inputs(tmp_path)
    result = runner.invoke(app, ["scan", str(repo_dir), "-p", str(p), "-s", str(skills_dir), "-c", str(c),
                                 "-o", str(tmp_path / "run")])
    assert result.exit_code == 2
    assert "ANTHROPIC_API_KEY" in result.output


def test_scan_end_to_end_with_fake(fake_claude_path: Path, repo_dir: Path, skills_dir: Path, tmp_path: Path):
    p, c = write_inputs(tmp_path)
    out = tmp_path / "run"
    result = runner.invoke(app, ["scan", str(repo_dir), "-p", str(p), "-s", str(skills_dir), "-c", str(c),
                                 "-r", "2", "-j", "2", "-o", str(out), "--timeout", "10"])
    assert result.exit_code == 0, result.output
    records = EventLog(out / "events.jsonl").load()
    assert len(records) == 8
    assert (out / "prompts.yaml").exists() and (out / "configs.yaml").exists()
    assert (out / "raw" / "full_p1_0.ndjson").exists()
    assert (out / "workspaces" / "full" / ".claude" / "skills" / "other").exists()
    assert "tomato-recipes" in result.output
    assert "Per-config summary" in result.output


def test_scan_resumes_and_report_reprints(fake_claude_path: Path, repo_dir: Path, skills_dir: Path, tmp_path: Path):
    p, c = write_inputs(tmp_path)
    out = tmp_path / "run"
    args = ["scan", str(repo_dir), "-p", str(p), "-s", str(skills_dir), "-c", str(c), "-o", str(out), "--timeout", "10"]
    assert runner.invoke(app, args).exit_code == 0
    n1 = len(EventLog(out / "events.jsonl").load())
    second = runner.invoke(app, args)
    assert second.exit_code == 0
    assert "nothing to run" in second.output
    assert len(EventLog(out / "events.jsonl").load()) == n1
    rep = runner.invoke(app, ["report", str(out)])
    assert rep.exit_code == 0
    assert "Per-config summary" in rep.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write cli.py**

```python
# src/skillscope/cli.py
from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from skillscope.models import SessionOptions, load_configs, load_prompts
from skillscope.report.terminal import render
from skillscope.runner import session as session_mod
from skillscope.runner.plan import plan
from skillscope.runner.pool import Pool
from skillscope.runner.workspace import build
from skillscope.store import EventLog

app = typer.Typer(add_completion=False, no_args_is_help=True)
console = Console()


def _default_run_dir() -> Path:
    return Path.cwd() / ".skillscope" / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")


@app.command()
def scan(
    repo: Path = typer.Argument(..., exists=True, file_okay=False, help="Repository to copy into workspaces"),
    prompts_path: Path = typer.Option(..., "-p", "--prompts", exists=True, dir_okay=False),
    skills_dir: Path = typer.Option(..., "-s", "--skills", exists=True, file_okay=False),
    configs_path: Path = typer.Option(..., "-c", "--configs", exists=True, dir_okay=False),
    parallel: int = typer.Option(4, "-j", "--parallel"),
    repeats: int = typer.Option(3, "-r", "--repeats"),
    model: str = typer.Option("haiku", "-m", "--model"),
    cost_cap: float = typer.Option(5.0, "--cost-cap", help="Stop launching sessions past this USD total"),
    timeout: float = typer.Option(120.0, "--timeout", help="Per-session wall clock seconds"),
    budget: float = typer.Option(0.05, "--budget", help="Per-session --max-budget-usd"),
    est_per_session: float = typer.Option(0.012, "--est-per-session", help="USD per session for dry-run estimate"),
    dry_run: bool = typer.Option(False, "-n", "--dry-run"),
    out: Path | None = typer.Option(None, "-o", "--out", help="Run folder (default .skillscope/runs/<timestamp>)"),
) -> None:
    prompts = load_prompts(prompts_path)
    configs = load_configs(configs_path)
    run_dir = out or _default_run_dir()
    log = EventLog(run_dir / "events.jsonl")
    sessions = plan(prompts, configs, repeats, log.done_keys())

    per_config = {c.name: sum(1 for s in sessions if s.config == c.name) for c in configs}
    for name, n in per_config.items():
        console.print(f"{name}: {n} sessions")
    console.print(f"total: {len(sessions)} sessions, estimated ${len(sessions) * est_per_session:.2f}")
    if dry_run:
        return
    if not sessions:
        console.print("nothing to run; all sessions already in events.jsonl")
        render(log.load(), prompts, configs, console)
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[red]ANTHROPIC_API_KEY is not set; refusing to start.[/red]")
        raise typer.Exit(code=2)

    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(prompts_path, run_dir / "prompts.yaml")
    shutil.copy(configs_path, run_dir / "configs.yaml")
    (run_dir / "raw").mkdir(exist_ok=True)
    (run_dir / "claude-config").mkdir(exist_ok=True)
    workspaces = {c.name: build(repo, skills_dir, c, run_dir / "workspaces") for c in configs}
    opts = SessionOptions(
        run_id=run_dir.name, model=model, budget_usd=budget, timeout_s=timeout,
        config_dir=run_dir / "claude-config", raw_dir=run_dir / "raw",
    )
    pool = Pool(session_mod.run, parallel=parallel, cost_cap_usd=cost_cap)

    async def drive() -> None:
        done = 0
        async for record in pool.run(sessions, {p.id: p for p in prompts}, workspaces, opts):
            log.append(record)
            done += 1
            console.print(
                f"[{done}/{len(sessions)}] {record.config} {record.prompt_id} r{record.repeat} "
                f"{record.outcome.value} {record.first_skill or '-'} ${pool.total_cost:.3f}"
            )

    try:
        asyncio.run(drive())
    except KeyboardInterrupt:
        console.print("[yellow]interrupted; rerun the same command to resume[/yellow]")
    render(log.load(), prompts, configs, console)
    if pool.aborted_reason:
        console.print(f"[yellow]partial run: {pool.skipped} sessions not started ({pool.aborted_reason})[/yellow]")
    console.print(f"run folder: {run_dir}")


@app.command()
def report(run_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    prompts = load_prompts(run_dir / "prompts.yaml")
    configs = load_configs(run_dir / "configs.yaml")
    render(EventLog(run_dir / "events.jsonl").load(), prompts, configs, console)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -q && uv run ruff check .`
Expected: `4 passed`. Then the whole suite: `uv run pytest -q` → all green.

- [ ] **Step 5: Check the installed entry point**

Run: `uv run skillscope --help`
Expected: usage text listing `scan` and `report`.

- [ ] **Step 6: Commit**

```bash
git add src/skillscope/cli.py tests/test_cli.py
git commit -m "feat(cli): scan and report commands with dry run, resume and pre-flight

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: Live smoke test and cost-estimate check

**Files:**
- Test: `tests/test_live.py`

**Interfaces:**
- Consumes: `session.run`, `estimate_cost`. Skipped unless `SKILLSCOPE_LIVE=1`. Costs about $0.01.

- [ ] **Step 1: Write the test**

```python
# tests/test_live.py
"""One real Haiku session. Run with SKILLSCOPE_LIVE=1 and ANTHROPIC_API_KEY set."""
import os
from pathlib import Path

import pytest

from skillscope.models import Config, Outcome, Prompt, Session, SessionOptions
from skillscope.runner.session import estimate_cost, run
from skillscope.runner.stream import ResultEvent, Usage, parse_line
from skillscope.runner.workspace import build

pytestmark = pytest.mark.skipif(os.environ.get("SKILLSCOPE_LIVE") != "1", reason="set SKILLSCOPE_LIVE=1")


async def test_live_no_fire_result_cost_matches_estimate_within_25_percent(
    repo_dir: Path, skills_dir: Path, tmp_path: Path
):
    ws = build(repo_dir, skills_dir, Config(name="full", skills="*"), tmp_path / "ws")
    opts = SessionOptions(run_id="live", config_dir=tmp_path / "cfg", raw_dir=tmp_path / "raw", timeout_s=90)
    opts.config_dir.mkdir()
    opts.raw_dir.mkdir()
    p = Prompt(id="c1", text="Reply with the single word OK and nothing else.", expected="none", origin="control")
    rec = await run(ws, p, Session(config="full", prompt_id="c1", repeat=0), opts)
    assert rec.outcome in (Outcome.NO_FIRE, Outcome.FIRED)
    assert rec.claude_version, "init event must carry the CLI version"
    raw = rec.raw_path.read_text().splitlines()
    results = [e for e in (parse_line(x) for x in raw) if isinstance(e, ResultEvent)]
    if results and results[-1].total_cost_usd:
        usage = Usage()
        for line in raw:
            ev = parse_line(line)
            if hasattr(ev, "usage") and not isinstance(ev, ResultEvent):
                usage = usage + ev.usage
        est = estimate_cost(usage, rec.model)
        real = results[-1].total_cost_usd
        assert abs(est - real) / real < 0.25, f"estimate {est} vs result {real}"
```

- [ ] **Step 2: Run it once for real**

Run: `SKILLSCOPE_LIVE=1 uv run pytest tests/test_live.py -q -s`
Expected: `1 passed`. If the cost assertion fails, adjust `PRICES_PER_MTOK` to the ratio observed and note it in the commit message. If `claude_version` is None, the init line's key differs from the parser's expectation: fix `parse_line` from the raw file.

- [ ] **Step 3: Commit**

```bash
git add tests/test_live.py
git commit -m "test: live smoke test comparing cost estimate to result total

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 16: Gate inputs (repo, 30 skills, 40 prompts) — user checkpoint

**Files:**
- Create: `gate/README.md`
- Create: `gate/configs.yaml`
- Create: `gate/prompts.yaml`
- Create: `gate/skills/<30 folders>/SKILL.md` plus `gate/skills/SOURCES.md`
- Create: `gate/repo/` (cloned at a pinned commit; gitignored, with the clone command recorded)

**Interfaces:**
- Produces the three inputs `skillscope scan` needs. Nothing is run against the API in this task.

- [ ] **Step 1: Pick the repo**

Shortlist to present to the user (all public, Next.js app router, small enough to copy in seconds):

1. `nextjs/saas-starter` — Next.js 15 app router, Drizzle, Stripe, Tailwind. Realistic feature surface.
2. `steven-tey/precedent` — app router starter with auth and Tailwind. Small.
3. `shadcn-ui/taxonomy` — app router, content, auth, Tailwind. Medium, older Next version.

Recommend 1. Ask the user to pick; then:

```bash
git clone --depth 50 https://github.com/nextjs/saas-starter gate/repo
git -C gate/repo rev-parse HEAD > gate/REPO_COMMIT
echo "gate/repo/" >> .gitignore
```

- [ ] **Step 2: Assemble the skill set**

Write `gate/skills/SOURCES.md` first, one line per skill: name, source repo, commit, licence. Sources to pull from (verify names on skills.sh and in each repo; the exact folder names must be read from the source, not assumed):

- Expected (5): a Next.js app-router skill, a React components skill, a Tailwind skill, a testing skill (Vitest or Playwright), a TypeScript style skill. Candidates: `vercel-labs/agent-skills` (React and Next.js best practices, web design guidelines), skills.sh top installs for `nextjs`, `tailwind`, `vitest`, `playwright`, `typescript`.
- Near distractors (10): other frontend or JS skills — Vue, Svelte, Angular, Remix, Express or Fastify, GraphQL, Prisma or Drizzle (if not already expected), CSS-in-JS, Storybook, npm publishing.
- Far distractors (15): from `anthropics/skills` (`docx`, `pptx`, `xlsx`, `pdf`, `mcp-builder`, `webapp-testing` if it doesn't overlap testing), plus Python or Rust or DevOps skills from skills.sh (FastAPI, Django, pandas, Rust CLI, Docker, Kubernetes, Terraform, GitHub Actions, SQL migrations).

For each: `git clone --depth 1 <repo> /tmp/src && cp -r /tmp/src/<path-to-skill> gate/skills/<name>`. Every folder must contain `SKILL.md` with `name` and `description` frontmatter. Verify with:

```bash
for d in gate/skills/*/; do test -f "$d/SKILL.md" || echo "MISSING $d"; done; ls gate/skills | wc -l
```

Expected: no `MISSING`, count `30`.

- [ ] **Step 3: Write configs.yaml**

```yaml
# gate/configs.yaml — replace names with the 30 actual folder names from gate/skills
minimal: [<expected-1>, <expected-2>, <expected-3>, <expected-4>, <expected-5>]
medium:  [<expected-1>, <expected-2>, <expected-3>, <expected-4>, <expected-5>,
          <near-1>, <near-2>, <near-3>, <near-4>, <near-5>, <near-6>, <near-7>, <near-8>, <near-9>, <near-10>]
full: "*"
```

Validate: `uv run python -c "from pathlib import Path; from skillscope.models import load_configs; [print(c.name, len(c.resolve(Path('gate/skills')))) for c in load_configs(Path('gate/configs.yaml'))]"` → `minimal 5`, `medium 15`, `full 30`.

- [ ] **Step 4: Draft prompts.yaml**

40 prompts: 30 task prompts, 6 per expected skill, written the way a developer types into Claude Code about *this* repo (name real routes, files and components from `gate/repo`); 10 controls with `expected: none`: 5 off-ecosystem tasks (Rust, Python data, Terraform) and 5 generic questions (explain a git command, what does this env var do). Use `origin: task` and `origin: control`. Example shape:

```yaml
- id: t01
  text: The /dashboard page flashes empty before team data loads. Add a loading state using the app router's conventions.
  expected: <nextjs-app-router-skill-name>
  origin: task
- id: t07
  text: Extract the pricing cards on /pricing into a reusable component with props for plan name, price and features.
  expected: <react-components-skill-name>
  origin: task
- id: k01
  text: Write a Rust function that parses a TOML config file into a struct.
  expected: none
  origin: control
- id: k06
  text: What does git rebase --onto do?
  expected: none
  origin: control
```

Validate: `uv run python -c "from pathlib import Path; from skillscope.models import load_prompts; ps=load_prompts(Path('gate/prompts.yaml')); print(len(ps), sum(1 for p in ps if p.expected), sum(1 for p in ps if not p.expected))"` → `40 30 10`.

- [ ] **Step 5: Dry run**

Run: `uv run skillscope scan gate/repo -p gate/prompts.yaml -s gate/skills -c gate/configs.yaml -r 3 -n -o .skillscope/runs/gate`
Expected: `minimal: 120`, `medium: 120`, `full: 120`, `total: 360`, estimate about $4.32.

- [ ] **Step 6: Present to the user and wait**

Show the user `gate/prompts.yaml`, `gate/skills/SOURCES.md`, and the dry-run output. Ask them to edit prompts or swap skills. Do not run Task 17 until they say go.

- [ ] **Step 7: Commit**

```bash
git add gate/README.md gate/configs.yaml gate/prompts.yaml gate/skills gate/REPO_COMMIT .gitignore
git commit -m "chore(gate): week-2 gate inputs: 30 skills, 40 prompts, size ladder configs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 17: Gate run and decision record

**Files:**
- Create: `docs/superpowers/gate/2026-10-10-week-2-gate.md`

**Interfaces:**
- Consumes: the CLI and gate inputs.

- [ ] **Step 1: Run the gate**

Run: `uv run skillscope scan gate/repo -p gate/prompts.yaml -s gate/skills -c gate/configs.yaml -r 3 -j 4 --cost-cap 5 -o .skillscope/runs/gate 2>&1 | tee .skillscope/runs/gate/console.log`
Expected: 360 lines of progress, two tables, total cost under $5, under 20 minutes. If the run stops on the cost cap or an auth error, read the message, fix, and rerun the same command to resume.

- [ ] **Step 2: Sanity checks before reading the numbers**

Run: `uv run python - <<'EOF'
from pathlib import Path
from collections import Counter
from skillscope.store import EventLog
rs = EventLog(Path('.skillscope/runs/gate/events.jsonl')).load()
print('records', len(rs))
print('outcomes', Counter(r.outcome.value for r in rs))
print('skills_loaded by config', {c: sorted({r.skills_loaded for r in rs if r.config == c}) for c in ('minimal','medium','full')})
print('cost', round(sum(r.cost_usd for r in rs), 3), 'estimated share', sum(1 for r in rs if r.cost_source.value=='estimated')/len(rs))
print('errors', [(r.config, r.prompt_id, (r.error or '')[:80]) for r in rs if r.outcome.value=='error'][:10])
EOF`

Expected: 360 records; `skills_loaded` is `[5]`, `[15]`, `[30]` respectively (this proves each config's skills were actually listed to the model); errors are zero or explained; timeouts are rare. If `skills_loaded` is `None` everywhere, the init line does not list skills and this check must be replaced by inspecting one raw transcript per config by hand.

- [ ] **Step 3: Write the decision record**

```markdown
# Week-2 gate: <date>

Run folder: `.skillscope/runs/gate` (events.jsonl, raw/, console.log). Repo: nextjs/saas-starter at <commit>. Model: Haiku 4.5 via Claude Code <version>. 40 prompts × 3 configs × 3 repeats.

## Rule
wrong-or-none rate on `full` minus `minimal` ≥ 15 points, with `medium` between them.

## Numbers
<paste the per-config summary table>

## Decision
PASS / FAIL, with the two numbers that decided it.

## What the noise looks like
Per-repeat rates for each config, and the largest spread between repeats.

## Observations
- Which distractors fired most on task prompts (from the fire table).
- Control fire rate per config.
- Exploration: share of no-fire sessions with exploration_calls > 0.
- Anything that looked wrong in the raw transcripts.

## Next
If PASS: start iteration 2 (profiler, catalog loader, prompt generator, SQLite). If FAIL: the specific hypothesis to test before any more building.
```

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/gate/2026-10-10-week-2-gate.md
git commit -m "docs(gate): week-2 gate result and decision

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

Report the decision to the user with the two headline numbers.
