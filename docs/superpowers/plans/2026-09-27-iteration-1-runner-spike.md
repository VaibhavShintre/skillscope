# Iteration 1: Runner Spike Implementation Plan

Date: 2026-09-27

Revised: 2026-09-29 after implementation-readiness review

Status: approved design, implementation not started

## Goal

Build the smallest trustworthy path from a hand-written `prompts.yaml` and a
folder of Claude Code skills to a reproducible trigger-selection report. Then
run the week-2 gate on a pinned Next.js repository with 5-, 15-, and 30-skill
catalogs.

Iteration 1 answers one question: does increasing catalog size measurably make
Claude select the wrong skill, or no skill, on project-specific prompts?

It does not implement the profiler, registry, generated prompts, static gate,
SQLite, statistical shadowing graph, HTML report, recommendations, or `apply`.

## Authoritative contracts

This plan is the implementation source of truth for Iteration 1. The companion
design is `docs/superpowers/specs/2026-09-27-iteration-1-runner-spike-design.md`.
If the project overview differs, this plan wins for Iteration 1.

The day-zero probe is authoritative for Claude Code flags and stream fields.
Recorded output wins over remembered or documented field shapes. Any probe
finding that changes a contract must update the design, fixtures, and relevant
tests in the same commit.

## Platform decision

Iteration 1 runs under Linux or WSL2. Native Windows process-tree termination
is explicitly deferred until after the gate. This is intentional: the runner
must reliably terminate the Claude process and all descendants after a trigger
decision, timeout, cancellation, or Ctrl-C.

Development from Windows therefore uses a WSL2 checkout or mounts this checkout
inside WSL. All documented shell commands are Bash commands. The CLI itself must
fail preflight with a clear unsupported-platform message when run natively on
Windows during Iteration 1.

## Fixed decisions

| Concern | Decision |
| --- | --- |
| Runtime | Python 3.12 via `uv`; Linux/WSL2 for Iteration 1 |
| Claude Code | Minimum compatible version established by the probe; version recorded per run |
| Model | Explicit model ID from the probe, never the floating `haiku` alias for a gate run |
| Authentication | `ANTHROPIC_API_KEY`; never print or persist its value |
| Skill loading | Chosen only after the probe proves exact resolved skills in `init` output |
| Tool surface | `Read`, `Grep`, `Glob`, and `Skill`; command-line restriction and no permission prompts |
| Stop rule | Record the first Skill selection, wait for its tool result when emitted, then terminate |
| Backstops | `--max-turns 3` when supported, per-session budget, and 120-second wall timeout |
| Workspace | One sterile, read-only workspace per catalog configuration |
| Claude state | One unique `CLAUDE_CONFIG_DIR` per session; never shared concurrently |
| Persistence | Append-only, fsynced `events.jsonl` plus immutable `run-manifest.json` |
| Resume | Allowed only when every immutable input and runner option matches the manifest |
| Ordering | Seeded, reproducible interleaving across configurations |
| Cost cap | Reserved-cost hard launch cap; in-flight reservation is counted before launch |
| Reporting term | “selected” is the model decision; “loaded” is successful Skill tool completion |
| Gate | Paired prompt-level full-minus-minimal degradation of at least 15 percentage points |

## Safety and validity invariants

1. A real run never reads or writes the user's normal Claude configuration.
2. Every subprocess gets a unique scratch configuration directory.
3. A measured workspace contains exactly the resolved skills in its configuration.
4. Existing repository skills, commands, agents, hooks, plugins, local settings,
   and generated build folders cannot silently enter a measured configuration.
5. The run manifest binds results to repository, prompts, configs, skills,
   model, Claude version, runner options, pricing table, and randomization seed.
6. Resuming with different immutable inputs fails before any API call.
7. A `Skill` request is stored as a selection. Successful loading is a separate
   fact based on the following tool result when available.
8. Prompt IDs and configuration names never become unchecked filesystem paths.
9. The cost cap reserves the maximum per-session budget before starting work.
10. A gate is ineligible if completeness, catalog loading, version consistency,
    or error-rate checks fail.

## Architecture

```text
inputs ──> preflight ──> immutable manifest ──> seeded planner
                                                   │
                                          reserved-cost pool
                                                   │
                     ┌─────────────────────────────┘
                     v
sterile workspace + unique session config ──> claude -p
                                                   │
                                      stream parser + state
                                                   │
                           selection ──> tool result ──> terminate
                                                   │
                                             SessionRecord
                                                   │
                                      fsynced events.jsonl
                                                   │
                               fire table + gate eligibility/result
```

## Repository layout

```text
skillscope/
  README.md
  LICENSE
  pyproject.toml
  uv.lock
  src/skillscope/
    __init__.py
    cli.py
    models.py
    preflight.py
    manifest.py
    store.py
    runner/
      __init__.py
      stream.py
      decision.py
      process.py
      session.py
      workspace.py
      plan.py
      pool.py
    report/
      __init__.py
      terminal.py
      gate.py
  tests/
    conftest.py
    fake_claude/
      claude
    fixtures/streams/
    test_*.py
  gate/
    README.md
    REPO_COMMIT
    configs.yaml
    prompts.yaml
    skills/
      SOURCES.md
  docs/superpowers/gate/
```

## Data contracts

### Prompts

```yaml
- id: dashboard-loading
  text: Add an App Router loading state for the dashboard while team data loads.
  expected: [nextjs-app-router]
  origin: task

- id: control-rust-toml
  text: Write a Rust function that parses TOML into a struct.
  expected: none
  origin: control
```

Validation rules:

- IDs are unique, non-empty slugs matching `[a-z0-9][a-z0-9_-]{0,63}`.
- Task prompts have at least one expected canonical skill name.
- Control prompts have no expected skills.
- Expected names must resolve during preflight.
- Prompts must not name or explicitly hint at the expected skill.

### Configurations

```yaml
minimal: [nextjs-app-router, react-components, tailwind, playwright, typescript-style]
medium: [nextjs-app-router, react-components, tailwind, playwright, typescript-style,
         vue, svelte, angular, remix, express, graphql, prisma, storybook, css-in-js, npm-publish]
full: "*"
```

Validation rules:

- Names use the same safe-slug rule as prompt IDs.
- Effective skill names, not folder names, are canonical.
- Effective names are unique across the input catalog.
- `minimal` is a subset of `medium`; `medium` is a subset of `full`.
- The resolved gate sizes are exactly 5, 15, and 30.
- The same seeded catalog order is preserved as each configuration grows.

### Session record

Each JSONL line validates as one `SessionRecord` with at least:

```text
schema_version, run_id, config, prompt_id, repeat
outcome
selected_skill, skill_load_succeeded, skill_load_error
skills_selected[]
expected[], matched
exploration_calls, skills_loaded
input_tokens, output_tokens, cost_usd, cost_source
duration_ms, model, claude_version
termination_reason, error, raw_path
```

`matched` compares `selected_skill` with canonical `expected` names. Controls
have `matched = null`. A selection can be matched even when loading fails, but
load failures make the run ineligible until explained.

### Run manifest

`run-manifest.json` is created atomically before the first paid session:

```text
schema_version
created_at_utc
repo_commit and repo_tree_hash
prompts_sha256 and configs_sha256
skill folder hashes and effective names
resolved ordered skills per configuration
model_id and claude_code_version
tools, isolation_mode, max_turns, session_budget, timeout
parallelism, repeats, ordering_seed
pricing_table_version
planned_session_keys
```

Resume compares all immutable fields. A mismatch prints a field-level diff and
exits 2. `--new-run` creates a new timestamped directory; there is no force flag
that mixes incompatible observations.

## Gate definition

There are 30 task prompts, 10 control prompts, 3 configurations, and 3 repeats:
360 planned sessions, of which 270 are task observations.

A task session is wrong-or-none when `matched is not True`; this includes wrong
selection, no selection, timeout, budget stop, and error. Technical failures
are also reported separately so they cannot masquerade as a catalog effect.

For prompt `p` and configuration `c`:

```text
error_rate(p, c) = wrong-or-none repetitions / completed repetitions
```

The headline configuration rate is the mean of its 30 prompt-level task rates.
The paired effect is the mean, across prompts, of:

```text
error_rate(p, full) - error_rate(p, minimal)
```

### Eligibility

A gate result is eligible only when:

- all 360 planned session keys have one valid record;
- resolved `skills_loaded` is 5, 15, and 30 for every respective session;
- every selected skill load succeeds or has an explicitly adjudicated reason;
- all sessions use one exact model ID and one Claude Code version;
- there are no authentication failures;
- error plus timeout rate is at most 2% in each configuration;
- no manifest or raw-transcript integrity check fails.

### Pass rule

The gate passes when all are true:

- paired `full - minimal` wrong-or-none degradation is at least 15.0 percentage points;
- `minimal <= medium <= full` using the prompt-level mean rates;
- `full - minimal` is positive in at least two of the three repeat slices;
- the 95% cluster-bootstrap interval over prompts is reported alongside the
  point estimate. The interval is descriptive for this spike, not an extra
  pass condition.

If eligibility fails, the result is `INELIGIBLE`, never `FAIL`. If eligible but
the pass rule is not met, the result is `FAIL` and Iteration 2 pauses pending a
written explanation.

## Terminal UI

The UI contract and complete wireframes live in
`docs/superpowers/specs/2026-09-29-iteration-1-cli-ui.md`.

Design principles:

- Show decisions and risks before implementation detail.
- Use stable plain-text labels in addition to color.
- Print exact paths and recovery commands.
- Keep progress to one updating region on an interactive terminal and emit one
  line per completed session when output is redirected.
- Never print API keys, full prompts during progress, or raw model output.
- Support `--no-color` and honor `NO_COLOR`.

## Implementation tasks

Every task follows red-green-refactor: add a failing test, run it, implement the
smallest change, run focused tests, run the full suite and Ruff, then commit.

### Task 1: Environment and version preflight

Create `scripts/check-env.sh` and document WSL2/Linux setup. Check Linux, Python
3.12, `uv`, `claude`, `ANTHROPIC_API_KEY` presence, and writable temp space.
Print versions without printing credentials. Add README, Apache-2.0 LICENSE,
`pyproject.toml`, package skeleton, and commit `uv.lock`.

Acceptance:

- Native Windows exits with a WSL2 guidance message.
- Missing key or binary reports one actionable line and exits 2.
- `uv run pytest` and `uv run ruff check .` succeed.
- Package build succeeds; the README referenced by package metadata exists.

### Task 2: Day-zero Claude compatibility matrix

Build a temporary repository with one obvious skill and probe these modes:

1. `--bare` with the workspace as current directory.
2. `--bare --add-dir <workspace>`.
3. `--setting-sources project` with isolated `CLAUDE_CONFIG_DIR`.

For each mode, record init metadata, resolved skill names, Skill request and
tool result, result fields, files written to the scratch config, and whether the
real Claude directory changed. Also probe `--tools`, `--allowedTools`,
`--permission-prompts none`, and `--max-turns 3`.

Select the simplest mode that loads exactly one expected skill and no personal,
synced, plugin, command, agent, hook, or MCP configuration. Save scrubbed fire
and no-fire transcripts. Update the design with the exact Claude version,
model ID, argv, stream fields, and isolation result.

Stop if no mode is sterile and correct.

### Task 3: Models and strict loaders

Implement enums and Pydantic models for prompts, configurations, sessions,
records, options, canonical skill metadata, manifest, and gate result.

Test duplicate IDs, unsafe IDs, task/control consistency, duplicate effective
skill names, subset rules, exact gate sizes, JSON round trips, and helpful error
locations.

### Task 4: Catalog preflight

Implement only the catalog checks required for a valid spike:

- locate `SKILL.md`;
- parse frontmatter enough to determine effective name;
- reject reserved or duplicate names;
- detect `disable-model-invocation: true`;
- map folder names to canonical names;
- hash every file in each skill folder;
- resolve each ordered configuration.

This is not the future static gate. It exists solely to prove what is being
measured before money is spent.

### Task 5: Stream parser

Parse recorded whole-message NDJSON into typed events:

- init;
- assistant text and tool-use blocks;
- user tool-result blocks;
- final result;
- raw unknown event.

Malformed or unknown lines remain raw and are never discarded. Add fixtures for
selection, successful skill load, failed skill load, no selection, exploration,
budget stop, malformed input, and result-only termination.

### Task 6: Decision state machine

Track selection separately from loading:

- first `Skill` tool-use records `selected_skill`;
- wait for its matching tool result when the stream provides one;
- record success/error, then request process termination;
- a clean assistant `end_turn` without selection is `no_fire`;
- result, budget, error, timeout, and external cancellation remain distinct.

If the probed Claude version does not emit a skill tool result before executing
the skill body, the probe defines selection as the stopping point and the record
sets load success to `null`; the report must say “selected,” not “loaded.”

### Task 7: Fake Claude test double

Create an executable Python fixture for Linux/WSL2. It selects transcripts from
prompt text, records argv and environment, supports slow streaming, hangs,
errors, auth failure, and descendant-process spawning. It must never require a
real API key.

### Task 8: POSIX process controller

Centralize process creation and termination. Start a new process group, send a
graceful termination first, wait briefly, then force-kill the group. Handle
already-exited and permission errors. Cancellation must await stderr and child
cleanup before returning.

Integration tests prove no fake descendant survives normal early stop, timeout,
pool cancellation, or Ctrl-C-equivalent cancellation.

### Task 9: Sterile workspace builder

Copy the repository while excluding `.git`, dependency/build caches, and prior
`.skillscope` output. Remove copied `.claude/skills` before installing exactly
the resolved configuration.

The isolation mode selected by Task 2 defines treatment of `CLAUDE.md`, project
settings, commands, agents, hooks, and plugins. Write that policy explicitly and
test it. Reject symlinks that resolve outside the source repository for the
Iteration 1 gate.

### Task 10: Run manifest and resume guard

Hash immutable inputs, resolve repository commit/tree identity, record runner
versions/options, generate the planned session keys, and write the manifest via
temporary-file-plus-rename.

On resume, print a concise diff for any mismatch and exit before creating a
workspace or calling Claude.

### Task 11: Seeded, interleaved planner

Generate every `(prompt, repeat, config)` key once. Interleave configurations
within prompt/repeat blocks and rotate or seed-shuffle their order so catalog
size is not confounded with wall-clock order. Persist the seed in the manifest.

The same inputs and seed produce identical order. Resume filters completed keys
without reordering the remaining plan.

### Task 12: Session runner

For every session:

- create a unique config directory under the run folder;
- build argv from the probed contract;
- pass an explicit safe environment;
- stream and fsync raw NDJSON;
- feed parser and decision state;
- terminate through the process controller;
- return a complete record with canonical identities and termination reason.

Unknown models fail cost estimation rather than silently receiving Haiku rates.
All text files use UTF-8.

### Task 13: Event log

Append one validated JSON record at a time, flush and fsync it, recover only a
torn final line, and reject malformed interior lines or duplicate session keys.
The single CLI consumer performs appends, so concurrent workers never write the
file directly.

### Task 14: Reserved-cost pool

Before launching, reserve the configured per-session budget. Launch only when:

```text
observed_cost + reserved_in_flight + next_reservation <= run_cost_cap
```

Release the reservation when the record arrives and add observed cost. Stop on
auth failure, drain or cancel safely, and expose skipped keys and abort reason.
Tests cover parallel bounds, cap behavior, runner exceptions, auth abort, and
cancellation without leaked processes.

### Task 15: Interrupt and resume integration

Run an interleaved plan against fake Claude, interrupt after several persisted
records, verify all children are gone, then resume. Assert exactly one record
per planned key, unchanged manifest, deterministic remaining order, and no
duplicate API work for completed keys.

### Task 16: Reporter and gate evaluator

Implement:

- per-skill selected/expected/unexpected table;
- per-config task and control rates;
- per-repeat rates;
- skill-load failures and technical errors;
- manifest/version summary;
- eligibility checks;
- paired prompt-level effect;
- deterministic cluster bootstrap using the run seed;
- `PASS`, `FAIL`, `INELIGIBLE`, or `PARTIAL` result.

Machine-readable `gate-result.json` accompanies terminal output.

### Task 17: CLI

Commands:

```text
skillscope scan REPO -p PROMPTS -s SKILLS -c CONFIGS [options]
skillscope report RUN_DIR
skillscope doctor
```

Important options:

```text
-j, --parallel INTEGER
-r, --repeats INTEGER
--model MODEL_ID
--cost-cap FLOAT
--session-budget FLOAT
--timeout FLOAT
--seed INTEGER
-n, --dry-run
-o, --out DIRECTORY
--no-color
--new-run
```

`doctor` is read-only. `scan --dry-run` performs full validation, resolution,
hashing, manifest preview, session count, and reserved worst-case cost without
requiring an API key or creating paid-session state.

### Task 18: Real smoke and cost calibration

Add an opt-in live test with early stopping disabled so a final priced result is
required. Compare estimated and reported cost; absence of a result is a test
failure, not a skipped assertion. Record observed pricing fields and update the
dated price table only from an authoritative source.

Run one live selection case too, confirming exact skill count, selected canonical
name, tool-result behavior, and clean process shutdown.

### Task 19: Gate inputs and user checkpoint

Recommend `nextjs/saas-starter`, but obtain user confirmation before cloning.
Pin its commit. Assemble 30 licensed skills: 5 expected, 10 near distractors,
and 15 far distractors. Record source URL, commit, path, and license for each.

Write 30 project-specific task prompts, six per expected skill, and 10 controls.
Prompts must reference real files/routes while avoiding the expected skill's
name. Validate canonical mappings and exact sizes.

Present these before any paid run:

- `gate/prompts.yaml`;
- `gate/skills/SOURCES.md`;
- resolved configuration membership/order;
- dry-run UI including planned cost.

Wait for user approval.

### Task 20: Gate run

Run 360 sessions with four-way parallelism and the stored seed. The default
cost cap is $5 only if dry-run reservation and the smoke-test estimate show it
can complete; otherwise present a revised cap before spending.

Afterward run integrity and eligibility checks before inspecting the headline
effect. Preserve the manifest, event log, raw streams, console log, and machine
gate result under `.skillscope/runs/gate`.

### Task 21: Decision record

Write `docs/superpowers/gate/2026-10-10-week-2-gate.md` with:

- repository and all pinned versions;
- manifest hash and run directory;
- eligibility result;
- prompt-level rates and paired effect;
- repeat slices and bootstrap interval;
- control fire rates;
- load failures and technical errors;
- notable unexpected skill selections;
- `PASS`, `FAIL`, or `INELIGIBLE` with the exact rule;
- the next action.

Iteration 2 starts only after an eligible `PASS`.

## Verification commands

```bash
uv sync --locked
uv run ruff check .
uv run pytest -q
uv build
uv run skillscope doctor
uv run skillscope scan gate/repo \
  -p gate/prompts.yaml \
  -s gate/skills \
  -c gate/configs.yaml \
  -r 3 -j 4 --seed 20260929 --dry-run \
  -o .skillscope/runs/gate
```

The paid form is identical without `--dry-run`, after the Task 19 checkpoint.

## Explicit deferrals

- Native Windows process control.
- SQLite and cross-run comparisons.
- Generated prompts.
- Registry discovery and installation.
- Full static/security scanning.
- Automatic verdicts or skill overrides.
- HTML UI.
- Outcome evaluation after a skill loads.

These stay out even if convenient during implementation. The gate exists to
decide whether the larger product is worth building.
