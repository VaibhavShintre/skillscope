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

## Cost and noise

The plan prints two cost figures. The **expected cost** is what a typical session costs (about 520
output tokens, skills loaded in about 60% of sessions). The **worst-case bound** is built from the
actual system prompt, tool definitions, prompt and skill text, and the harness's 3-turn and
`max_tokens` limits; the `--cost-cap` is held to that bound, session by session. Token counts are
estimated from characters, so both are estimates. The final report shows expected, actual and the
bound side by side.

Configurations are planned in priority order. The baseline, every singleton, the full bundle
and every leave-one-out (the full bundle minus one skill) come first and are never cut to save
budget, so every skill gets a measured leave-one-out. Greedy prefixes and pairs are extras that
fill what is left. If the required set does not fit the caps, the planner drops auto-discovered
skills (lowest relevance first) and says which; skills you name with `--skill` are never dropped,
so too small a cap is an error instead.

A run scores each configuration on a handful of prompts, so one prompt is a large step (for
example 6.7 points on 15 prompts). The tolerance used to compare the baseline and the bundles is
therefore never smaller than one prompt: a one-prompt lead over "no skills" is reported as
**inconclusive, gap within noise**, not as a win.

## Scores, ties and the decoy control

The score is **balanced**: the average of the positive-prompt score (prompts that expect a skill)
and the negative-prompt score (prompts that should stay silent). The report shows both next to
the headline, so a configuration that never fires scores 50% however many negative prompts a run
has, and staying silent cannot win by default.

The tolerance used to compare configurations is never below one prompt's worth of score, and a
gap equal to it is a tie. A recommended bundle must itself lead "no skills" by more than that:
being the smallest bundle near the best is not enough, because it can lead the baseline by only
one prompt. If nothing does, the report says "no skills", and "inconclusive — gap within noise"
when even the best bundle is within the noise floor.

`--decoy` adds a built-in control skill with a plausible description and a useless body, plus two
configurations (the decoy alone, and the full bundle with the decoy listed). It is never
recommended. The report shows how often it fires (every firing is wrong), the most that merely
listing it lifted a score, and flags any bundle whose lead over no skills is no larger than that.

## Privacy and safety

The API key is read only from `ANTHROPIC_API_KEY`. It is never accepted on the
command line or written to artifacts. Planning is local. Live evaluation sends
the generated prompt and bounded project profile to Anthropic. Candidate
scripts are inventoried but not executed by the current routing evaluator.

This initial implementation measures skill discovery and selection in the
Skills Evaluator API harness. It does not claim to reproduce native Claude Code
activation or execute candidate-authored scripts.
