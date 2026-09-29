# skillscope iteration 1: terminal UI

Date: 2026-09-29
Status: proposed UI contract for the revised runner spike

## Character

The interface should feel like an instrument panel, not a chatbot. It is quiet
by default, explicit about what will cost money, and careful to separate a model
selection result from a technical failure.

Rich supplies tables, restrained color, and an updating progress region.
Everything remains understandable without color and when redirected to a log.

Color semantics:

- green: valid or pass;
- yellow: partial, warning, or unverified;
- red: invalid, blocked, or failed preflight;
- cyan: paths, versions, and neutral measurements.

No result depends on color alone. Symbols always have adjacent text.

## `skillscope doctor`

```text
$ skillscope doctor

skillscope doctor

Runtime
  platform        Linux 6.6.87 (WSL2)                         OK
  python          3.12.7                                      OK
  uv              0.8.17                                      OK
  claude          2.1.280                                     OK
  API key         present                                     OK

Runner contract
  isolation       project sources + isolated config           PROBED
  model           claude-haiku-4-5-20251001                   PINNED
  Skill input     input.skill                                  PROBED
  Skill result    emitted before skill body                    PROBED
  process cleanup descendant termination                       OK

Ready for a dry run.
```

Blocked example:

```text
$ skillscope doctor

skillscope doctor

Runtime
  platform        Windows 11                                  UNSUPPORTED
  uv              not found                                   MISSING
  claude          not found                                   MISSING
  API key         not checked

Iteration 1 requires Linux or WSL2 so process trees can be terminated safely.
Open this repository in WSL2, then run: scripts/check-env.sh
```

`doctor` never displays the API key and never makes an API call.

## Dry run

```text
$ skillscope scan gate/repo -p gate/prompts.yaml -s gate/skills \
    -c gate/configs.yaml -r 3 -j 4 --seed 20260929 --dry-run \
    -o .skillscope/runs/gate

skillscope scan — dry run

Inputs
  repository      nextjs/saas-starter @ 90d80b4
  prompts         40 (30 task, 10 control)
  skills          30 canonical names from 30 folders
  configurations  minimal 5 · medium 15 · full 30
  repeats         3
  ordering seed   20260929
  model           claude-haiku-4-5-20251001

Isolation
  Claude config   unique per session
  project config  stripped; measured skills installed explicitly
  tools           Read, Grep, Glob, Skill
  workspace links no external symlinks

Plan
  sessions        360
  parallel        4
  session budget  $0.05
  hard launch cap $5.00
  estimate        $4.32 based on 2026-09-29 smoke calibration
  worst case      $18.00; cap may stop the run before completion

Manifest preview
  hash            5af63b7c…
  output          .skillscope/runs/gate

DRY RUN — no API calls made and no paid-session state created.
```

If the configured cap cannot plausibly finish, make that visually prominent:

```text
WARNING  Estimated cost $6.14 exceeds the $5.00 hard launch cap.
         Increase --cost-cap or reduce the plan before starting.
```

## Validation failure

```text
Cannot create run: 3 input errors

  gate/prompts.yaml:17  expected skill "nextjs" does not resolve
  gate/configs.yaml:3   medium must contain every minimal skill
  gate/skills/deploy    disable-model-invocation is true

No run directory was created. No API calls were made.
```

## Resume mismatch

```text
Cannot resume .skillscope/runs/gate

Immutable inputs differ from run-manifest.json:

  prompts_sha256                 8a17…  ->  c204…
  skills/react-components hash   1bd0…  ->  3f96…
  model_id                       claude-haiku-4-5-20251001
                              -> claude-sonnet-5

Start a separate run with --new-run or restore the recorded inputs.
Existing results were not modified. No API calls were made.
```

## Interactive run

The top section is printed once. The progress region updates in place.

```text
skillscope scan

Run       gate
Model     claude-haiku-4-5-20251001 via Claude Code 2.1.280
Plan      360 sessions · 4 parallel · seed 20260929
Cost      $0.00 observed · $0.20 reserved · $5.00 hard cap
Output    .skillscope/runs/gate

Running  [████████████████░░░░░░░░░░░░░░░░] 192/360  53%  08:14

minimal   64/120   selected 38   no selection 24   technical 2
medium    64/120   selected 35   no selection 27   technical 2
full      64/120   selected 29   no selection 32   technical 3

Workers
  1  full     dashboard-loading   repeat 2   waiting for Skill result
  2  minimal  pricing-component   repeat 1   reading stream
  3  medium   control-rust-toml   repeat 0   reading stream
  4  full     auth-playwright     repeat 2   terminating

Last       full/dashboard-loading/r1 selected react-components (unexpected)
Cost       $2.31 observed · $0.20 reserved · $5.00 hard cap
```

When stdout is not a TTY, print one stable line per completed record:

```text
192/360 config=full prompt=dashboard-loading repeat=1 outcome=selected skill=react-components matched=false cost=0.0118
```

## Completed report

```text
skillscope report .skillscope/runs/gate

Run integrity
  manifest         5af63b7c…                                  OK
  sessions         360/360                                    COMPLETE
  catalog sizes    minimal 5 · medium 15 · full 30             OK
  model/version    one model · one Claude Code version         OK
  auth failures    0                                           OK
  error + timeout  minimal 0.0% · medium 0.8% · full 0.8%      OK

Selection by expected skill
┌───────────────────────┬────────────────┬────────────────┬────────────────┐
│ skill                 │ minimal        │ medium         │ full           │
├───────────────────────┼────────────────┼────────────────┼────────────────┤
│ nextjs-app-router     │ 17/18 (+0)     │ 15/18 (+1)     │ 11/18 (+2)     │
│ react-components      │ 16/18 (+1)     │ 14/18 (+3)     │ 10/18 (+7)     │
│ tailwind              │ 18/18 (+0)     │ 16/18 (+2)     │ 13/18 (+3)     │
│ playwright            │ 15/18 (+0)     │ 14/18 (+1)     │ 12/18 (+2)     │
│ typescript-style      │ 16/18 (+1)     │ 13/18 (+3)     │ 10/18 (+5)     │
└───────────────────────┴────────────────┴────────────────┴────────────────┘
cell = correct selections / expected sessions (+unexpected selections)

Configuration summary
┌─────────┬───────────────┬─────────────┬────────────────┬──────────────┐
│ config  │ wrong-or-none │ per repeat  │ control fire   │ vs minimal   │
├─────────┼───────────────┼─────────────┼────────────────┼──────────────┤
│ minimal │  8.9%         │ 7 · 10 · 10 │  0.0%          │ —            │
│ medium  │ 17.8%         │ 17 · 20 · 17│  6.7%          │ +8.9 pp      │
│ full    │ 27.8%         │ 27 · 30 · 27│ 13.3%          │ +18.9 pp     │
└─────────┴───────────────┴─────────────┴────────────────┴──────────────┘

Week-2 gate                                                    PASS
  paired full − minimal     +18.9 percentage points   threshold +15.0
  monotonic size ladder     8.9 <= 17.8 <= 27.8       yes
  positive repeat slices    3 of 3                    required 2 of 3
  95% prompt bootstrap CI   +11.2 to +26.7 pp         descriptive

Artifacts
  manifest       .skillscope/runs/gate/run-manifest.json
  events         .skillscope/runs/gate/events.jsonl
  machine result .skillscope/runs/gate/gate-result.json
  raw streams    .skillscope/runs/gate/raw/
```

## Partial or ineligible report

```text
Week-2 gate                                             INELIGIBLE

Reason
  full catalog size was 29 in 7 sessions; expected 30.

The measurements are preserved, but no PASS/FAIL conclusion was computed.
Inspect: .skillscope/runs/gate/raw/full_dashboard-loading_1.ndjson
Resume after correcting the environment only if the manifest still matches.
```

Cost-cap termination is `PARTIAL`, not `INELIGIBLE`, until resumed:

```text
Run stopped safely at the cost cap.
  completed    328/360
  observed     $4.86
  unstarted    32

Resume with the same command and a higher --cost-cap. Completed sessions will
not run again.
```

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Command succeeded; inspect reported gate status separately |
| 2 | Invalid input, unsupported environment, auth preflight, or resume mismatch |
| 3 | Partial run caused by cost cap or interruption |
| 4 | Run completed but gate is ineligible |
| 5 | Internal integrity failure |

`PASS` versus `FAIL` is data, not shell success versus failure. Both produce a
valid report and exit 0.
