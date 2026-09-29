# SkillScope local web MVP implementation plan

Date: 2026-09-29

## Outcome

Build a usable, privacy-first local web application for greenfield skill
recommendations. A developer can describe a project, paste a PRD and technical
specification, confirm inferred capabilities, choose a catalog posture, receive
ranked and explained Claude skill recommendations, configure monitoring consent,
and export a project report.

The MVP distinguishes planning evidence from measured Claude trigger evidence.
It never labels heuristic matching as a benchmark.

## Delivery shape

- Python 3.12 package and reusable domain core.
- FastAPI local server bound to loopback.
- Static HTML/CSS/JavaScript frontend with no Node build step.
- Local SQLite preferences and analysis history.
- CLI commands: `skillscope ui`, `skillscope analyze`, and `skillscope doctor`.
- Pytest coverage for extraction, ranking, privacy defaults, persistence, and API.

## Privacy rules

- Manual-only operation is the default.
- Local monitoring, cloud sync, telemetry, automatic API spending, and automatic
  skill changes are separate settings and default off.
- The MVP makes no external network request.
- Inputs and reports remain in a local SQLite database.
- Users can export or delete their local data.
- Monitoring consent records its version and update time.
- Monitoring is represented honestly as a preference in this MVP; filesystem
  scheduling is a later adapter and is not silently started.

## Recommendation model

1. Normalize project description, PRD, specification, and declared stack.
2. Extract capabilities through transparent keyword/rule matches.
3. Allow capability weights from 1 (optional) to 5 (critical).
4. Match candidate skills to capabilities and stack signals.
5. Calculate separate components: weighted coverage, criticality, compatibility,
   uniqueness, and health.
6. Incorporate trigger precision, recall, marginal contribution, and conflict
   penalties only when measured evidence exists.
7. Produce an importance score, confidence label, practical tier, reasons, gaps,
   and evidence-source label.
8. Select lean, balanced, or comprehensive catalogs without hiding excluded
   candidates.

## UI screens

1. Project intake: name, description, PRD, technical specification, stack.
2. Recommendation posture: lean, balanced, comprehensive.
3. Capability review: inferred capabilities and importance weights.
4. Results: ranked skills, score components, reasons, coverage, and gaps.
5. Privacy center: independent consent controls and local-data deletion.
6. Export: JSON and Markdown project reports.

## Implementation tasks

- [x] Define package metadata and runtime dependencies.
- [x] Implement project, capability, evidence, ranking, and privacy models.
- [x] Add a source-verified snapshot of Anthropic's public skill catalog with provenance links.
- [x] Implement transparent capability extraction.
- [x] Implement explainable posture-aware ranking.
- [x] Implement local SQLite storage and delete/export operations.
- [x] Implement FastAPI routes and static asset serving.
- [x] Build responsive project intake, recommendation, detail, and privacy UI.
- [x] Add CLI entry points.
- [x] Add unit and API tests.
- [ ] Connect recorded Claude trigger evidence from the runner spike.
- [ ] Add opt-in filesystem monitoring adapter after the privacy UX is tested.
- [ ] Add candidate-skill registry discovery and installation.
- [ ] Add authenticated hosted planning mode only after a separate privacy review.

## Acceptance criteria

- A new user can receive a recommendation without a repository.
- The UI calls recommendations “planning estimates” unless measured evidence was
  supplied.
- Every ranked skill shows component scores and plain-language reasons.
- Lean, balanced, and comprehensive postures return visibly different catalogs.
- All monitoring and data-sharing controls begin disabled.
- No external request is made during analysis.
- Local data can be exported and deleted from the UI.
- The test suite passes on Python 3.12.
