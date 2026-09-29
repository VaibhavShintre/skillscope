# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout: two independent Python packages

This repo holds two separate projects, each with its own `pyproject.toml` and `uv.lock`. Run commands from the directory of the package you are working on.

- **Repo root — `skillscope`** (`src/skillscope/`): privacy-first local web app (FastAPI + static JS UI) that recommends a lean Claude skill catalog from a project description/PRD/stack. Planning estimates only; no external network requests.
- **`skills-evaluator/` — `skills_evaluator`**: separate CLI that profiles a real repo, builds bounded skill-combination experiments, runs them against the Anthropic API (or a fake engine), and reports the smallest bundle that performs near the best. Not required by, and does not import from, `skillscope`.

`docs/superpowers/{specs,plans}/` and `Skill Catalog Optimizer Project Overview.md` hold the design intent (notably the planned "runner spike" that will replace SkillScope's inferred routing confidence with measured trigger evidence).

## Commands

Both packages: Python >=3.12, `uv`, pytest, ruff (line length 100, rules `E,F,I,UP,B`).

```bash
# SkillScope (repo root)
uv sync --locked --extra dev
uv run skillscope ui --no-open          # serves http://127.0.0.1:8765
uv run skillscope doctor
uv run pytest -q
uv run pytest tests/test_analysis.py::test_catalog_posture_changes_selected_count -q   # single test
uv run ruff check src tests

# Skills Evaluator
cd skills-evaluator
uv sync --locked --extra dev
uv run pytest -q
uv run ruff check src tests
uv run skills-evaluator plan --project <dir> --offline         # free, no API key
uv run skills-evaluator evaluate --project . --skill tests/fixtures/skills/testing --offline --fake   # full pipeline, no key/cost
```

CI (`.github/workflows/ci.yml`) runs `uv sync --locked --extra dev`, `pytest -q`, `ruff check src tests`, and `uv build` for each package (matrix of `.` and `skills-evaluator`) on Linux/Windows/macOS.

## SkillScope architecture

- `analysis.py` (the core): `extract_capabilities` maps free-text project input to capabilities via `CapabilityRule`s, then `analyze_project` scores catalog skills (compatibility, trigger quality, confidence) and selects a set according to the `Posture` (lean/balanced/comprehensive). Reports gaps rather than inventing skills when no catalog entry covers a capability.
- `catalog.py`: static snapshot of the 19 packages in Anthropic's public skills repo (with `CATALOG_SOURCE` / `CATALOG_VERIFIED_AT`). Capability mappings and fit scores are SkillScope's own metadata — every recommendation must keep its publisher and source link.
- `api.py`: `create_app(database_path)` factory; serves `web/` statically and JSON under `/api/*`. Enforces loopback-only via `TrustedHostMiddleware` and a strict CSP/privacy-headers middleware (`connect-src 'self'`) — don't add external origins or CDN assets.
- `storage.py`: `LocalStore` SQLite (WAL) for settings and saved analyses; location is `~/.skillscope/skillscope.db`, overridable with `SKILLSCOPE_DATA_DIR`. Tests pass an explicit `database_path`.
- `models.py` (pydantic), `report.py` (markdown export), `cli.py` (Typer: `ui`, `doctor`, `analyze`).
- Privacy settings (monitoring, cloud sync, telemetry, auto-spend, auto-config changes) are independent and default to **off**; keep that default.
- The UI must be reached via the server; `web/index.html` does not work opened directly.

## Skills Evaluator architecture

Pipeline: `profiler` (bounded project profile) → `skills` (load/validate Agent Skills from project, local path, or `owner/repo@skill` GitHub shorthand) → `planner.build_plan` (bounded skill-combination experiments, generated + optional YAML prompts via `prompts.py`) → `engine` (runs sessions) → `analysis` (pick smallest near-best bundle) → `report`.

- `engine.py`: `EvaluationEngine` protocol with `AnthropicApiEngine` (live) and `FakeEngine` (deterministic, used by tests and `--fake`). Costs come from `token_cost`/`session_reservation`; unknown models raise rather than guessing prices. The plan enforces `--max-sessions` and a cost cap.
- `storage.py` + `cli.py`: runs live under `./.skills-evaluator/runs/<run_id>/` relative to the **current directory** (not the target project); results are appended per session so `resume` can skip completed `session_order` entries.
- Safety invariants: target project is never modified; `ANTHROPIC_API_KEY` is read only from the environment (never a CLI flag, never written to artifacts); candidate skill scripts are inventoried but never executed. It measures skill discovery/selection in its own API harness, not native Claude Code activation.
- Tests use `tests/fixtures/` (sample project incl. a `.env`, three skills incl. an `unsafe` one, `prompts.yaml`).
