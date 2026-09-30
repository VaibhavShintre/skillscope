# Skills Evaluator

Skills Evaluator is a separate local CLI that examines a software project,
tests candidate Agent Skills in several controlled combinations, and recommends
the smallest observed bundle that performs near the best bundle.

The existing SkillScope website remains in the repository root and is not
required by this package.

## Install

```powershell
cd skills-evaluator
uv sync --extra dev
$env:ANTHROPIC_API_KEY = "your-key"
```

## Quick start

Create a free, reviewable experiment plan:

```powershell
uv run skills-evaluator plan --project C:\path\to\project --offline
```

Evaluate project-installed skills plus a local skill:

```powershell
uv run skills-evaluator evaluate `
  --project C:\path\to\project `
  --skill C:\path\to\skill `
  --prompts C:\path\to\team-prompts.yaml
```

Optional prompt files are YAML lists. `expected_skill` accepts either a loaded
skill's canonical ID or its frontmatter name:

```yaml
- id: verify-checkout
  text: Add a regression test for the checkout flow.
  expected_skill: browser-testing
```

By default, planning also fetches Anthropic's public skill repository. Use
`--offline` to evaluate only project-installed and explicitly supplied local
skills. GitHub shorthand is supported:

```text
--skill anthropics/skills@frontend-design
```

Use `--fake` to exercise the entire planner, runner, resume logic, analysis,
and reports without a key or paid requests:

```powershell
uv run skills-evaluator evaluate --project . --skill tests\fixtures\skills\testing --offline --fake
```

Run artifacts are stored outside the target by default under
`.skills-evaluator/runs/` in the current directory. The target project is never
modified by `plan`, `evaluate`, `resume`, or `report`.

## Labels and categories

Every prompt is `positive` (a set of expected skills), `negative` (no skill should fire), or
`unlabeled`. Only labeled prompts count toward score and precision; the report shows how many
were left out, and if more than half are unlabeled it issues no recommendation.

Prompts are labeled from, in order: explicit YAML labels, `expected_skill`/`expected_skills`,
the control and near-miss prompt kinds, and finally a heuristic that maps a project capability
(for example `database`) to skill categories. Give a skill a category with a `category:` field in
its SKILL.md frontmatter; the 19 skills in `anthropics/skills` are already categorized. A skill
with no category is not judged on heuristic-labeled prompts.

YAML entries can override any label, including a generated prompt's, by `id`:

```yaml
- id: project-05-testing
  label: negative
- id: project-06-frontend
  expected_skills: [frontend-design]
```

Skills added from the public catalog rather than passed with `--skill` are marked
`auto-discovered` in the plan and the report. Pass `--offline` to evaluate only your own.

## Privacy and safety

The API key is read only from `ANTHROPIC_API_KEY`. It is never accepted on the
command line or written to artifacts. Planning is local. Live evaluation sends
the generated prompt and bounded project profile to Anthropic. Candidate
scripts are inventoried but not executed by the current routing evaluator.

This initial implementation measures skill discovery and selection in the
Skills Evaluator API harness. It does not claim to reproduce native Claude Code
activation or execute candidate-authored scripts.
