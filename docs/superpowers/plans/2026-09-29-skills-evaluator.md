# Skills Evaluator: Local Evaluation Product Plan

Date: 2026-09-29

Status: first local evaluation MVP implemented alongside the existing SkillScope website

## Implementation checkpoint

Implemented under `skills-evaluator/`:

- independent package, lockfile, CLI, license, and cross-platform CI template;
- project profiling with secret/build/cache exclusions;
- project-installed, local, GitHub, and default Anthropic skill discovery;
- canonical hashes, source commits, validation, deduplication, relevance filtering,
  and an initial static safety gate;
- project, skill-positive, and control prompt generation;
- bounded baseline, singleton, full, prefix, and leave-one-out configurations;
- Anthropic API progressive skill-loading harness plus a deterministic no-key engine;
- immutable manifests, seeded ordering, append-and-fsync results, resume guards,
  token-cost reservations, and a hard run cap;
- smallest-near-best bundle selection, per-skill verdicts, JSON/Markdown/HTML reports;
- project-level installation preview with an explicit `--apply` operation; and
- fixtures covering useful, redundant, and unsafe skill paths without paid calls.

Still required for the complete product described below:

- disposable write-capable task sandboxes and deterministic build/test outcome adapters;
- blinded outcome judging for tasks without deterministic assertions;
- adaptive greedy and targeted pair stages driven by earlier observations rather
  than the current bounded prefix approximation;
- a native Claude Code finalist-verification engine; and
- organization/private registry adapters and a richer security scanner.

## Product statement

Skills Evaluator is a separate, cloneable developer tool. A user gives it:

1. an Anthropic API key;
2. the path to a local or cloned software project; and
3. optionally, skills or skill repositories they specifically want evaluated.

The evaluator profiles the project, discovers and validates candidate Agent
Skills, creates a project-specific prompt suite, runs controlled experiments
with different skill combinations, and recommends the smallest bundle that
performs best together.

The primary deliverable is a public GitHub repository that a developer can
clone and run locally. A hosted account and an existing GitHub repository are
not required.

## User promise

```text
Clone Skills Evaluator
        +
Set ANTHROPIC_API_KEY
        +
Point it at a project directory
        â†“
Receive a reproducible evaluation report
        â†“
Recommended skill count + exact bundle + evidence + tradeoffs
```

Skills Evaluator does not merely match keywords. It tries candidate skills on
representative project tasks, compares controlled configurations, measures
individual and interaction effects, and shows the observations behind its
recommendation.

## Non-goals for v0

- A hosted SaaS that uploads repositories.
- Silent changes to the target project or the user's Claude configuration.
- Exhaustively testing all `2^n` skill subsets.
- Treating popularity, publisher identity, or an LLM opinion as proof of quality.
- Claiming that behavior measured in the evaluator is identical across every
  Claude surface and model.
- Executing untrusted skill scripts on the host machine.
- Installing the recommended bundle without a separate user-approved command.

## Distribution and onboarding

The product lives in its own repository, provisionally named
`skills-evaluator`. The current `skillscope` repository remains the prototype
and planning source until migration is explicitly approved.

Minimum onboarding:

```bash
git clone <skills-evaluator-repository>
cd skills-evaluator
uv sync --locked
export ANTHROPIC_API_KEY="..."
uv run skills-evaluator evaluate --project /path/to/project
```

PowerShell:

```powershell
git clone <skills-evaluator-repository>
cd skills-evaluator
uv sync --locked
$env:ANTHROPIC_API_KEY = "..."
uv run skills-evaluator evaluate --project C:\path\to\project
```

The key is accepted only through the process environment or an operating-system
credential provider. It is never accepted as a command-line value, written to a
run artifact, included in logs, or exposed to a skill's scripts.

## Core CLI

The zero-configuration path is:

```text
skills-evaluator evaluate --project PATH
```

For unattended use, `--yes` is valid only when the user also supplies an
explicit `--cost-cap`; otherwise the CLI requires confirmation of the generated
plan and maximum spend.

Users can add candidates and prompts:

```text
skills-evaluator evaluate --project PATH \
  --skill ./my-skills/api-review \
  --skill owner/repository@skill-name \
  --skill https://github.com/owner/repository/tree/COMMIT/skills/testing \
  --prompts ./team-prompts.yaml
```

Supporting commands:

```text
skills-evaluator doctor
skills-evaluator inspect-skill SOURCE
skills-evaluator plan --project PATH [candidate options]
skills-evaluator evaluate --project PATH [candidate options]
skills-evaluator resume RUN_DIR
skills-evaluator report RUN_DIR
skills-evaluator install RUN_DIR --project PATH
```

`plan` is local and free. It shows detected project capabilities, candidate
provenance, security findings, planned experiments, estimated token cost, and
the hard cost cap before any paid request.

## Inputs

### Target project

The profiler reads a user-selected project root and extracts only the signals
needed to construct evaluation tasks:

- languages, frameworks, dependencies, and lockfiles;
- README, architecture, PRD, and explicitly selected documentation;
- test, lint, type-check, and build commands;
- existing `.claude/skills` and compatible Agent Skills directories;
- recent Git history when the user allows it;
- file tree and bounded excerpts from relevant source files.

The initial run presents the inclusion/exclusion manifest before model calls.
Files matching common secret patterns are excluded. The original project is
never mutated; executable trials use disposable copies.

### Candidate skills

Candidates are the union of:

- verified catalog results relevant to the project profile;
- skills already installed in the target project;
- user-supplied local skill folders;
- user-supplied Git repository coordinates or immutable URLs; and
- later, organization-approved private catalogs.

User-supplied skills are evaluated alongside discovered candidates, not given
an automatic scoring advantage. Every candidate receives a canonical identity:

```text
publisher/repository@skill-name#commit
local:<absolute-source-hash>@skill-name
```

Duplicate content is collapsed by folder hash while aliases are retained in
the report.

### Prompt suite

The evaluator produces four prompt classes:

1. **Project tasks:** realistic work derived from architecture, tests, open
   work, and project documentation.
2. **Skill-positive tasks:** tasks that should benefit from one candidate.
3. **Conflict tasks:** ambiguous or cross-domain tasks that could trigger
   overlapping skills.
4. **Controls:** project-adjacent and unrelated tasks on which no candidate
   should activate.

User-provided prompts are preserved verbatim and marked `origin: user`.
Generated prompts are written to `prompts.yaml` before execution so the user
can inspect or edit them. The automatic path continues without editing after a
short summary and explicit cost confirmation.

## What â€œworks bestâ€ means

The recommendation is multi-objective. Skills Evaluator reports each dimension
instead of hiding everything behind one unexplained score.

### Routing metrics

- precision: selected when relevant;
- recall: selected on expected tasks;
- control-fire rate: selected when no skill should be used;
- load success: selected skill successfully became available;
- shadowing: another skill suppresses or replaces the expected skill;
- ambiguity: repeated runs select different skills for the same task.

### Outcome metrics

- relevant project tests passed;
- lint, type-check, or build result when applicable;
- task-specific deterministic assertions;
- size and validity of the produced change;
- regression count;
- blinded rubric score when deterministic checks are unavailable.

Model-judged scores are always labeled separately from deterministic checks.
The judge does not see the skill configuration name, and a baseline result is
included in every comparison.

### Efficiency and safety metrics

- input/output tokens and estimated cost;
- wall-clock latency;
- skill metadata and loaded-body token cost;
- static security findings;
- requested tools, network access, and script execution;
- publisher, license, source commit, and freshness.

## Combination-search strategy

Exhaustive subset evaluation is infeasible: 20 candidates produce 1,048,576
possible bundles before prompts or repeats. v0 uses a deterministic staged
search with a user-visible budget.

### Stage 0: static reduction

Reject invalid or blocked skills, deduplicate identical content, filter skills
with no plausible project capability, and cluster near-duplicate candidates.
Keep user-requested skills in the experiment even when their project match is
low, but label that fact.

### Stage 1: baseline and singletons

Run the prompt suite with no skills and once with each candidate alone. This
estimates marginal outcome improvement, routing precision, cost, and obvious
false activation.

### Stage 2: full and category bundles

Run all surviving skills together, then bundles grouped by project capability.
This exposes catalog-size degradation and coarse interaction effects.

### Stage 3: greedy forward selection

Starting from the baseline, add the skill with the best validated marginal
gain per unit of cost. Stop when no candidate clears the minimum improvement,
the coverage target is met, or the bundle-size/cost cap is reached.

### Stage 4: targeted interactions

For the top bundle, test:

- leave-one-out removal of every selected skill;
- pairwise combinations for high-overlap or suspected-conflict pairs;
- replacement by the best alternative in the same capability cluster; and
- addition of every user-requested candidate that was not selected.

### Stage 5: confirmation

Repeat the baseline, full bundle, recommended bundle, and nearest competing
bundle with seeded prompt ordering. Report uncertainty and mark unstable
verdicts `inconclusive` rather than forcing a winner.

## Recommendation rules

A skill may receive one of these verdicts:

- `essential`: removing it causes a material, repeatable decline;
- `useful`: improves outcomes or routing without harmful conflicts;
- `redundant`: another selected skill provides the same value more efficiently;
- `conflicting`: degrades another skill or raises false activations;
- `irrelevant`: no supported project use case was found;
- `unsafe`: blocked by the security policy;
- `inconclusive`: insufficient or unstable evidence.

The recommended bundle is the smallest bundle within a configurable tolerance
of the best observed outcome. The default tolerance is two percentage points,
subject to a minimum evidence threshold. This prevents a negligible score gain
from adding unnecessary skills.

Every recommendation includes:

```text
skill identity and pinned source
verdict and confidence
capabilities covered
prompts and observations supporting the verdict
baseline-versus-skill outcome delta
leave-one-out delta
known conflicts
cost and latency contribution
security and license status
```

## Execution architecture

```text
project path â”€â”€> profiler â”€â”€> safe project manifest
                                  â”‚
skill sources â”€â”€> loader â”€â”€> validation/security/provenance
                                  â”‚
                    capability candidate matrix
                                  â”‚
                 prompt generation + user prompts
                                  â”‚
                    deterministic experiment plan
                                  â”‚
             â”Œâ”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”
             â”‚ host orchestrator (holds API key)       â”‚
             â”‚ Anthropic client + cost/resume control  â”‚
             â””â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”¬â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
                                  â”‚
             disposable, secret-free project sandboxes
                                  â”‚
             routing events + task outputs + test results
                                  â”‚
                  interaction analysis + optimizer
                                  â”‚
                  JSON + Markdown + local HTML report
```

## Claude execution decision

The v0 default is an Anthropic API evaluation harness, not a dependency on a
globally installed Claude Code CLI. The host process keeps the API key and
implements controlled project tools plus progressive skill discovery/loading.
Sandboxed task commands never receive the API key.

This produces a one-key, one-project-path onboarding flow and permits exact
cost accounting. It measures behavior in the Skills Evaluator harness; the
report must not describe it as native Claude Code behavior.

An engine interface is mandatory from the start:

```python
class EvaluationEngine(Protocol):
    async def run(self, session: SessionSpec) -> SessionResult: ...
```

The first implementation is `AnthropicApiEngine`. A later
`ClaudeCodeEngine` verifies finalist bundles against native filesystem skill
discovery for users who have Claude Code installed. Results from the two
engines are never silently pooled.

Anthropic's Skills API can upload custom skills, but custom uploads are shared
across the API workspace and do not automatically mirror local Claude Code
skills. v0 therefore does not upload or delete user skills in their workspace
without a separate, explicit mode.

## Safety and privacy invariants

1. The original target project is read-only and never used as a task workspace.
2. Every executable trial receives a fresh disposable copy or worktree.
3. The API key exists only in the host orchestrator environment.
4. Skill code never receives host credentials or the user's normal environment.
5. Candidate skill scripts do not run during discovery or routing evaluation.
6. Outcome trials use an explicit command/tool allowlist and blocked network by
   default.
7. Secret-like files and ignored paths are excluded before prompt construction.
8. The plan lists every file or excerpt eligible to be sent to Anthropic.
9. No paid call launches until the estimated maximum is within the cost cap.
10. Every result binds to project, prompt, skill, model, engine, and runner hashes.
11. Resume refuses incompatible inputs rather than mixing observations.
12. Installation is a separate command with a previewed diff and confirmation.

Because API evaluation sends selected project context to Anthropic, â€œlocalâ€
means local orchestration and storage, not that all analysis remains on-device.
The CLI must state this before the first paid run.

## Run artifacts

Each run is append-only under `.skills-evaluator/runs/<run-id>/`:

```text
run-manifest.json       immutable identities, hashes, versions, seed and options
project-profile.json    detected stack and capability evidence
project-files.json      included/excluded context manifest
catalog.json            normalized candidate skills and provenance
security.json           findings, approvals and blocks
prompts.yaml            generated and user-provided evaluation prompts
experiment-plan.json    ordered configurations, repeats and maximum cost
events.jsonl            one durable session result per line
checks.jsonl            deterministic outcome checks
analysis.json           routing, outcome, interaction and uncertainty metrics
recommendation.json     selected bundle and evidence-linked verdicts
report.md               portable human-readable report
report.html             self-contained local interactive report
```

SQLite may index these artifacts for history, but the files are the portable
source of truth. A report can always be regenerated from a run directory.

## Report contract

The first screen answers:

```text
Recommended number of skills: 4
Best observed bundle: frontend-design, webapp-testing, api-review, postgres-review
Outcome improvement over no skills: +18.2 percentage points
Difference from best larger bundle: -0.7 points
Estimated run cost: $2.14
Confidence: medium
```

The report then shows:

- project capabilities and uncovered gaps;
- all candidates and why they entered the experiment;
- a baseline/full/recommended comparison;
- per-skill verdicts and marginal contribution;
- pairwise conflicts and shadowing edges;
- prompt-level results with deterministic versus judged labels;
- cost, token, latency, and failure summaries;
- provenance, security, license, model, and engine disclosures; and
- an exact installation preview for the recommended bundle.

## Implementation sequence

Each task requires tests and fixture-based execution without a real API key.

### 1. Create the separate package contract

- Rename the Python distribution and CLI to `skills-evaluator` in the new repo.
- Add `doctor`, `plan`, `evaluate`, `resume`, and `report` command skeletons.
- Document Python 3.12, `uv`, API-key setup, Windows/macOS/Linux paths, and data
  handling.
- Preserve Apache-2.0 licensing for our code; track skill licenses separately.

Acceptance: a fresh clone reaches `doctor` and a fixture-only demo using the
documented commands.

### 2. Define immutable models and storage

- Add strict Pydantic models for projects, skills, prompts, configurations,
  sessions, checks, analysis, and recommendations.
- Add atomic manifests, append-and-fsync event storage, content hashing, and
  strict resume checks.

Acceptance: interrupted fixture runs resume without duplicate observations.

### 3. Build the project profiler

- Detect stack, manifests, commands, docs, existing skills, and secret-like
  paths.
- Produce the reviewable context manifest and capability evidence.
- Support local directories whether or not they are Git repositories.

Acceptance: fixture profiles are deterministic and never include seeded secrets.

### 4. Build pluggable skill sources

- Load project-installed and user-supplied local skills first.
- Add pinned Git/URL sources.
- Add verified Anthropic, Vercel, and Microsoft adapters.
- Normalize identities, retain source commits, validate `SKILL.md`, and hash all
  bundled files.

Acceptance: duplicate content resolves once and all candidates have provenance.

### 5. Add the static safety gate

- Validate specification fields and referenced files.
- Inventory scripts, tools, network references, absolute paths, and suspicious
  credential/file-access instructions.
- Block severe findings; require explicit override for unresolved findings.

Acceptance: malicious fixtures never reach a live execution plan.

### 6. Generate and import prompts

- Generate bounded project, positive, conflict, and control prompts.
- Import user YAML with strict IDs and expected capability/skill fields.
- Cache generation by project/candidate/model hash.

Acceptance: prompts do not leak seeded secrets or directly name the expected
skill unless explicitly supplied by the user.

### 7. Implement the Anthropic API engine

- Keep credentials in the host client.
- Expose controlled read/search/skill-load tools.
- Stream typed events, usage, costs, tool calls, and termination states.
- Use exact model IDs and store API/engine versions.
- Provide a fake streaming client covering every termination path.

Acceptance: skills can be listed, selected, loaded, and measured without exposing
the key to the task sandbox.

### 8. Build disposable task sandboxes

- Create a clean copy/worktree for every outcome trial.
- Remove credentials and unrelated user/Claude configuration.
- Run only approved build/test commands with network denied by default.
- Capture patches and check results, then destroy the sandbox.

Acceptance: original fixture repositories remain byte-identical after all tests.

### 9. Implement experiment planning

- Create baseline, singleton, full, capability bundle, greedy, leave-one-out,
  targeted pair, replacement, and confirmation configurations.
- Seed and interleave prompt/configuration order.
- Deduplicate equivalent configurations.
- Enforce session, bundle-size, token, time, and dollar caps.

Acceptance: the same inputs and seed produce the same bounded plan; no exhaustive
power-set path exists.

### 10. Add deterministic outcome checks

- Discover safe existing test/lint/type/build commands.
- Support task-specific assertions and user-provided check commands.
- Compare against the baseline sandbox so pre-existing failures are not charged
  to a skill.

Acceptance: fixture regressions and improvements are attributed correctly.

### 11. Add optional blinded judging

- Define project-independent rubrics for tasks without deterministic checks.
- Blind configuration identities and randomize candidate ordering.
- Store judge prompts and raw scores separately.

Acceptance: reports never merge judged scores into deterministic success counts.

### 12. Analyze effects and choose a bundle

- Compute routing, outcome, efficiency, stability, and safety metrics.
- Calculate singleton gains, leave-one-out deltas, pair interactions, shadowing,
  and uncertainty intervals.
- Apply the smallest-within-tolerance optimization rule.

Acceptance: synthetic fixtures recover known essential, redundant, conflicting,
and irrelevant skills.

### 13. Build reports

- Produce JSON, Markdown, local HTML, and concise terminal summaries.
- Link every conclusion to prompt/session/check identifiers.
- Include an â€œinsufficient evidenceâ€ state and all engine/model limitations.

Acceptance: reports regenerate offline from run artifacts alone.

### 14. Add safe installation preview

- Generate the exact project-level skill folders, lockfile, and configuration
  changes needed for the recommended bundle.
- Preview first; apply only from the separate `install` command.
- Never write global skills in v0.

Acceptance: install is idempotent and rollback instructions are generated.

### 15. Validate on fixture and real projects

- Fixtures: React/TypeScript app, FastAPI service, and mixed monorepo.
- Candidate sets include useful, redundant, conflicting, irrelevant, stale, and
  malicious skills.
- Run opt-in live evaluations only after fixture gates pass.

Acceptance: human reviewers agree with at least 8 of 10 fixture verdicts and no
critical safety invariant fails.

### 16. Prepare the public GitHub deliverable

- CI on Windows, macOS, and Linux.
- Locked dependencies, release artifacts, changelog, issue templates, and
  security policy.
- Five-minute quickstart and a fully recorded no-key demo.
- Publish a reproducible example run with costs and limitations.

Acceptance: three developers can clone the repo, evaluate their own project,
and understand the report without maintainer assistance.

## Default cost policy

The evaluator must estimate before executing. Initial defaults:

```text
candidate cap                 12 after static reduction
generated prompts             24 project/positive + 8 conflict + 8 control
confirmation repeats          3 for finalist bundles only
parallel requests             3
default hard cost cap         $5.00
default wall-time cap         30 minutes
default recommended max       8 skills
```

If the planned worst-case cost exceeds the cap, reduce candidates, prompts, or
confirmation repeats and show what was removed. Never silently exceed the cap.

## Success criteria for v0

The product is ready for its first public repository when:

- a fresh clone requires only supported Python tooling, an API key, and a project
  path for the default flow;
- local and user-supplied skills enter the same evaluation pipeline;
- the project remains unchanged unless `install` is separately invoked;
- every live request is bounded, costed, resumable, and attributable;
- the report recommends an exact count and bundle rather than only ranking skills;
- every verdict links to raw observations and distinguishes deterministic checks
  from model judgments;
- unsafe skills are blocked before execution;
- the API key never appears in child environments, logs, artifacts, or reports;
- fixture tests run without network access or paid calls; and
- results clearly state that the default engine measures the evaluator harness,
  with native Claude Code verification remaining a separate engine.

## Decisions still requiring product approval

1. The final GitHub organization and repository URL for `skills-evaluator`.
2. The default Anthropic model and maximum per-run dollar cap.
3. Whether generated prompts require interactive review or only cost confirmation.
4. Whether v0 includes blinded LLM judging or ships deterministic checks first.
5. Whether native Claude Code finalist verification is required for v0 or v0.1.
6. Which official publishers are enabled by default beyond Anthropic.

These decisions change cost, trust boundaries, or the meaning of the reported
results and must not be guessed during implementation.

## Verified platform assumptions

- Anthropic documents both pre-built and custom Agent Skills on the Claude API:
  <https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview>
- Custom skill bundles can be uploaded through the Skills API, but API skills
  and Claude Code filesystem skills are separate surfaces:
  <https://platform.claude.com/docs/en/build-with-claude/skills-guide>
- Custom API skills are workspace-scoped, which is why v0 does not silently
  upload user candidates:
  <https://platform.claude.com/docs/en/api/http/skills/create>
