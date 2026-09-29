from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Annotated

import typer

from skills_evaluator import __version__
from skills_evaluator.analysis import analyze
from skills_evaluator.engine import AnthropicApiEngine, FakeEngine, session_reservation
from skills_evaluator.models import Recommendation
from skills_evaluator.planner import build_plan
from skills_evaluator.profiler import profile_project
from skills_evaluator.report import markdown_report, write_reports
from skills_evaluator.skills import load_skill
from skills_evaluator.storage import append_result, load_plan, load_results, session_order

app = typer.Typer(add_completion=False, no_args_is_help=True)

ProjectOption = Annotated[Path, typer.Option("--project", exists=True, file_okay=False)]
SkillOption = Annotated[list[str] | None, typer.Option("--skill")]


def _runs_root() -> Path:
    return Path.cwd() / ".skills-evaluator" / "runs"


def _make_plan(
    project: Path,
    skills: list[str],
    offline: bool,
    out: Path,
    maximum_candidates: int,
    max_sessions: int,
    cost_cap: float,
    model: str,
    seed: int,
    prompts: Path | None,
):
    out.mkdir(parents=True, exist_ok=True)
    return build_plan(
        project=project,
        sources=skills,
        run_root=out,
        include_anthropic=not offline,
        maximum_candidates=maximum_candidates,
        max_sessions=max_sessions,
        max_cost_usd=cost_cap,
        model=model,
        seed=seed,
        prompt_file=prompts,
    )


def _print_plan(plan, run_dir: Path) -> None:
    typer.echo(f"Run:              {plan.run_id}")
    typer.echo(f"Project:          {plan.project.root}")
    typer.echo(f"Candidates:       {len(plan.candidates)}")
    typer.echo(f"Prompts:          {len(plan.prompts)}")
    typer.echo(f"Configurations:   {len(plan.configurations)}")
    typer.echo(f"Planned sessions: {plan.planned_sessions}")
    typer.echo(f"Reserved maximum: ${plan.estimated_max_cost_usd:.2f}")
    typer.echo(f"Hard cost cap:    ${plan.max_cost_usd:.2f}")
    typer.echo(f"Artifacts:        {run_dir.resolve()}")
    for candidate in plan.candidates:
        label = "BLOCKED" if candidate.blocked else f"fit {candidate.relevance:.0%}"
        typer.echo(f"  - {candidate.name}: {label} ({candidate.source})")


def _execute(run_dir: Path, fake: bool) -> Recommendation:
    plan = load_plan(run_dir)
    current_profile = profile_project(Path(plan.project.root))
    if current_profile.content_hash != plan.project.content_hash:
        raise ValueError("The target project changed after planning; create a new run.")
    for candidate in plan.candidates:
        current = load_skill(
            Path(candidate.source_path), plan.project, candidate.source, candidate.user_requested
        )
        if current.content_hash != candidate.content_hash:
            raise ValueError(f"Candidate skill changed after planning: {candidate.name}")
    results = load_results(run_dir)
    completed_keys = {item.session_key for item in results}
    current_cost = sum(item.cost_usd for item in results)
    engine = FakeEngine() if fake else AnthropicApiEngine(plan.model)
    reservation = 0 if fake else session_reservation(plan.model)
    configs = {item.id: item for item in plan.configurations}
    prompts = {item.id: item for item in plan.prompts}
    candidates = {item.id: item for item in plan.candidates}

    for config_id, prompt_id in session_order(run_dir):
        key = f"{config_id}::{prompt_id}"
        if key in completed_keys:
            continue
        if current_cost + reservation > plan.max_cost_usd:
            typer.echo(
                f"Cost cap reached before {key}; run is partial and can be resumed with a new plan."
            )
            break
        config = configs[config_id]
        prompt = prompts[prompt_id]
        available = [candidates[skill_id] for skill_id in config.skill_ids]
        result = engine.run(key, config_id, prompt, plan.project, available)
        append_result(run_dir, result)
        results.append(result)
        completed_keys.add(key)
        current_cost += result.cost_usd
        marker = "ok" if result.outcome == "completed" else "error"
        typer.echo(f"[{len(results)}/{plan.planned_sessions}] {marker} {config_id} / {prompt_id}")
        if result.error and "authentication" in result.error.lower():
            typer.echo("Authentication error; stopping without retrying.")
            break

    recommendation = analyze(plan, results)
    write_reports(run_dir, plan, recommendation)
    return recommendation


@app.command()
def doctor() -> None:
    """Check the local runtime without making network or paid calls."""
    typer.echo(f"skills-evaluator {__version__}")
    typer.echo(f"python           {platform.python_version()}")
    typer.echo(f"platform         {platform.system()} {platform.release()}")
    typer.echo(f"git              {'available' if shutil.which('git') else 'missing'}")
    typer.echo(f"ANTHROPIC_API_KEY {'set' if os.environ.get('ANTHROPIC_API_KEY') else 'not set'}")


@app.command("plan")
def plan_command(
    project: ProjectOption,
    skill: SkillOption = None,
    offline: bool = typer.Option(False, help="Do not fetch the default Anthropic catalog."),
    out: Path = typer.Option(None, help="Run artifact root."),
    maximum_candidates: int = typer.Option(12, min=1, max=30),
    max_sessions: int = typer.Option(300, min=1),
    cost_cap: float = typer.Option(5.0, min=0.01),
    model: str = typer.Option("claude-haiku-4-5-20251001"),
    seed: int = typer.Option(1729),
    prompts: Path | None = typer.Option(None, exists=True, dir_okay=False),
) -> None:
    """Profile a project and create a free, reviewable experiment plan."""
    try:
        plan, run_dir = _make_plan(
            project,
            skill or [],
            offline,
            out or _runs_root(),
            maximum_candidates,
            max_sessions,
            cost_cap,
            model,
            seed,
            prompts,
        )
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    _print_plan(plan, run_dir)


@app.command()
def evaluate(
    project: ProjectOption,
    skill: SkillOption = None,
    offline: bool = typer.Option(False, help="Do not fetch the default Anthropic catalog."),
    fake: bool = typer.Option(False, help="Use deterministic fixture behavior; no API key."),
    yes: bool = typer.Option(False, "--yes", help="Accept the shown plan non-interactively."),
    out: Path = typer.Option(None, help="Run artifact root."),
    maximum_candidates: int = typer.Option(12, min=1, max=30),
    max_sessions: int = typer.Option(300, min=1),
    cost_cap: float = typer.Option(5.0, min=0.01),
    model: str = typer.Option("claude-haiku-4-5-20251001"),
    seed: int = typer.Option(1729),
    prompts: Path | None = typer.Option(None, exists=True, dir_okay=False),
) -> None:
    """Plan and execute a bounded skill-combination evaluation."""
    try:
        plan, run_dir = _make_plan(
            project,
            skill or [],
            offline,
            out or _runs_root(),
            maximum_candidates,
            max_sessions,
            cost_cap,
            model,
            seed,
            prompts,
        )
        _print_plan(plan, run_dir)
        if not fake:
            typer.echo("Selected project context and prompts will be sent to Anthropic.")
        if not yes and not fake and not typer.confirm("Run this paid evaluation?"):
            typer.echo(f"Plan saved at {run_dir}; no API calls were made.")
            raise typer.Exit()
        recommendation = _execute(run_dir, fake)
    except (OSError, RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Recommended skills: {recommendation.recommended_count}")
    typer.echo(f"Report: {(run_dir / 'report.html').resolve()}")


@app.command()
def resume(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    fake: bool = typer.Option(False, help="Use deterministic fixture behavior."),
) -> None:
    """Resume incomplete sessions from an immutable run directory."""
    try:
        recommendation = _execute(run_dir, fake)
    except (OSError, RuntimeError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Recommended skills: {recommendation.recommended_count}")
    typer.echo(f"Report: {(run_dir / 'report.html').resolve()}")


@app.command("report")
def report_command(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
) -> None:
    """Regenerate and print a report without network access."""
    plan = load_plan(run_dir)
    recommendation = analyze(plan, load_results(run_dir))
    write_reports(run_dir, plan, recommendation)
    typer.echo(markdown_report(plan, recommendation))


@app.command("inspect-skill")
def inspect_skill(
    source: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    project: ProjectOption = Path.cwd(),
) -> None:
    """Validate and inspect one local skill without executing it."""
    from skills_evaluator.profiler import profile_project

    candidate = load_skill(source, profile_project(project), str(source), True)
    typer.echo(candidate.model_dump_json(indent=2, exclude={"body"}))


@app.command()
def install(
    run_dir: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    project: ProjectOption,
    apply: bool = typer.Option(False, "--apply", help="Apply the previewed project-level copy."),
) -> None:
    """Preview or install the recommended bundle at project scope."""
    plan = load_plan(run_dir)
    recommendation = Recommendation.model_validate_json(
        (run_dir / "recommendation.json").read_text(encoding="utf-8")
    )
    by_id = {item.id: item for item in plan.candidates}
    target_root = project.resolve() / ".claude" / "skills"
    for skill_id in recommendation.recommended_skill_ids:
        candidate = by_id[skill_id]
        target = target_root / candidate.name
        typer.echo(f"{candidate.source_path} -> {target}")
        if apply:
            if target.exists():
                raise typer.BadParameter(f"Target already exists; refusing to overwrite: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(candidate.source_path, target)
    typer.echo(
        "Installed project-level skills."
        if apply
        else "Preview only; pass --apply to copy."
    )


if __name__ == "__main__":
    app()
