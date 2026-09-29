from __future__ import annotations

import json
import platform
import socket
import threading
import time
import webbrowser

import typer
import uvicorn

from skillscope import __version__
from skillscope.analysis import analyze_project
from skillscope.models import Posture, ProjectInput

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _open_when_ready(url: str, port: int) -> None:
    """Open the browser only after Uvicorn is accepting loopback connections."""
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.05)


@app.command()
def ui(
    port: int = typer.Option(8765, help="Loopback port for the local web application."),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Open the UI in a browser."),
) -> None:
    """Start the privacy-first local web application."""
    url = f"http://127.0.0.1:{port}"
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, port), daemon=True).start()
    typer.echo(f"SkillScope {__version__} — local only")
    typer.echo(f"Opening {url}")
    typer.echo("Press Ctrl+C to stop. No external requests are made by SkillScope.")
    uvicorn.run(
        "skillscope.api:create_app",
        host="127.0.0.1",
        port=port,
        log_level="warning",
        factory=True,
    )


@app.command()
def doctor() -> None:
    """Print read-only runtime and privacy diagnostics."""
    typer.echo(f"skillscope       {__version__}")
    typer.echo(f"python           {platform.python_version()}")
    typer.echo(f"platform         {platform.system()} {platform.release()}")
    typer.echo("web UI           available")
    typer.echo("external network disabled")
    typer.echo("Claude benchmark not connected in the web MVP")


@app.command("analyze")
def analyze_command(
    name: str = typer.Option(..., help="Project name."),
    description: str = typer.Option(..., help="Short project description."),
    stack: str = typer.Option("", help="Comma-separated technologies."),
    posture: Posture = typer.Option(Posture.BALANCED),
) -> None:
    """Print a planning recommendation as JSON without starting the UI."""
    project = ProjectInput(
        name=name,
        description=description,
        stack=[item.strip() for item in stack.split(",") if item.strip()],
        posture=posture,
    )
    typer.echo(json.dumps(analyze_project(project).model_dump(mode="json"), indent=2))


if __name__ == "__main__":
    app()
