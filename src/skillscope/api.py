from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.requests import Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from skillscope import __version__
from skillscope.analysis import analyze_project
from skillscope.catalog import CATALOG, CATALOG_SOURCE, CATALOG_VERIFIED_AT
from skillscope.models import PrivacySettings, ProjectInput
from skillscope.report import markdown_report
from skillscope.storage import LocalStore


def create_app(database_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="SkillScope", version=__version__)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    store = LocalStore(database_path)
    web_dir = Path(__file__).parent / "web"
    app.mount("/static", StaticFiles(directory=web_dir), name="static")

    @app.middleware("http")
    async def privacy_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(web_dir / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "version": __version__,
            "local_only": True,
            "external_requests": False,
        }

    @app.get("/api/catalog")
    def catalog() -> list[dict[str, object]]:
        return [
            {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "capabilities": sorted(skill.capabilities),
                "publisher": skill.publisher,
                "source_url": skill.source_url,
                "catalog_source": CATALOG_SOURCE,
                "verified_at": CATALOG_VERIFIED_AT,
            }
            for skill in CATALOG
        ]

    @app.post("/api/analyze")
    def analyze(project: ProjectInput):
        result = analyze_project(project)
        store.save_analysis(result)
        return result

    @app.get("/api/analyses/{analysis_id}")
    def get_analysis(analysis_id: str):
        result = store.get_analysis(analysis_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Analysis not found")
        return result

    @app.get("/api/analyses/{analysis_id}/report.md", response_class=PlainTextResponse)
    def report_markdown(analysis_id: str) -> str:
        result = store.get_analysis(analysis_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Analysis not found")
        return markdown_report(result)

    @app.get("/api/analyses/{analysis_id}/report.json")
    def report_json(analysis_id: str) -> JSONResponse:
        result = store.get_analysis(analysis_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Analysis not found")
        return JSONResponse(result.model_dump(mode="json"))

    @app.get("/api/privacy")
    def get_privacy() -> PrivacySettings:
        return store.get_privacy()

    @app.put("/api/privacy")
    def save_privacy(settings: PrivacySettings) -> PrivacySettings:
        updated = settings.model_copy(update={"updated_at": datetime.now(UTC)})
        return store.save_privacy(updated)

    @app.get("/api/local-data")
    def local_data():
        return store.summary()

    @app.get("/api/local-data/export")
    def export_local_data() -> JSONResponse:
        return JSONResponse(store.export_all())

    @app.delete("/api/local-data", status_code=204)
    def delete_local_data() -> None:
        store.delete_all()

    return app
