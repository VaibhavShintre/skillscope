# SkillScope

SkillScope is a privacy-first local application that recommends a lean Claude
skill catalog from a project's description, PRD, technical specification, and
declared stack. It explains and ranks each recommendation instead of returning
an opaque list.

The included candidate catalog is a snapshot of the 19 packages in
[Anthropic's public skills repository](https://github.com/anthropics/skills),
verified on 2026-09-29. Every recommendation includes its publisher and direct
source link. SkillScope's capability mappings and project-fit scores are its
own planning metadata; a listed skill is not necessarily installed locally.
When no first-party package covers a requirement, SkillScope reports a gap
instead of inventing a skill name.

The current MVP provides planning recommendations. Measured Claude trigger
benchmarks will be connected through the runner spike documented under
`docs/superpowers/`.

## Skills Evaluator

The new repository-oriented evaluator is implemented as a separate package in
[`skills-evaluator/`](skills-evaluator/README.md). It profiles a local project,
loads project or user-supplied Agent Skills, creates bounded skill-combination
experiments, runs them through an Anthropic API harness, and produces a
reproducible recommendation report. The SkillScope website remains available
and unchanged under `src/skillscope/`.

## Run locally

```bash
uv sync --locked --extra dev
uv run skillscope ui
```

Without `uv`:

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/skillscope ui
```

On Linux or macOS, replace `.venv/Scripts/` with `.venv/bin/`.

The UI binds to `http://127.0.0.1:8765` and opens that address in the default
browser after the server is ready. Keep the terminal window running while you
use the UI. Do not open `src/skillscope/web/index.html` directly or through an
editor preview: the page needs its local API server. SkillScope makes no
external network requests.

## CLI

```bash
skillscope doctor
skillscope ui --no-open
skillscope analyze --name "Acme" --description "A multi-tenant SaaS app"
```

## Privacy

Manual analysis is the default. Local monitoring, cloud synchronization,
anonymous telemetry, automatic spending, and automatic configuration changes
are independent settings and begin disabled. The MVP stores preferences and
saved analysis reports locally in SQLite.
