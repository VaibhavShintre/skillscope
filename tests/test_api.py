from pathlib import Path

from fastapi.testclient import TestClient

from skillscope.api import create_app


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "api.db"))


def test_index_and_health(tmp_path: Path) -> None:
    web = client(tmp_path)
    response = web.get("/")
    assert response.status_code == 200
    assert "Choose skills from project intent" in response.text
    assert response.headers["x-frame-options"] == "DENY"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    health = web.get("/api/health").json()
    assert health["local_only"] is True
    assert health["external_requests"] is False
    catalog = web.get("/api/catalog").json()
    assert len(catalog) == 19
    assert all(item["publisher"] == "Anthropic" for item in catalog)
    assert all(
        item["source_url"].startswith("https://github.com/anthropics/skills/")
        for item in catalog
    )


def test_analysis_and_exports(tmp_path: Path) -> None:
    web = client(tmp_path)
    response = web.post(
        "/api/analyze",
        json={
            "name": "Acme",
            "description": "A multi-tenant Next.js SaaS dashboard",
            "prd": "Stripe subscriptions, roles, permissions, and browser testing",
            "technical_spec": "React, TypeScript, Drizzle, PostgreSQL and Playwright",
            "stack": ["Next.js", "React", "Stripe", "Drizzle", "Playwright"],
            "posture": "balanced",
            "capability_weights": {},
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["selected_count"] > 0
    assert result["recommendations"][0]["reasons"]

    report = web.get(f"/api/analyses/{result['analysis_id']}/report.md")
    assert report.status_code == 200
    assert "# SkillScope recommendation: Acme" in report.text
    exported = web.get(f"/api/analyses/{result['analysis_id']}/report.json")
    assert exported.status_code == 200
    assert exported.json()["project"]["name"] == "Acme"


def test_privacy_is_separate_and_deletable(tmp_path: Path) -> None:
    web = client(tmp_path)
    initial = web.get("/api/privacy").json()
    assert initial["monitoring_mode"] == "manual"
    assert initial["cloud_sync"] is False

    initial.update(
        {
            "monitoring_mode": "local",
            "monitored_scopes": ["planning", "skills"],
            "cloud_sync": False,
            "anonymous_telemetry": False,
        }
    )
    saved = web.put("/api/privacy", json=initial)
    assert saved.status_code == 200
    assert saved.json()["monitoring_mode"] == "local"

    deleted = web.delete("/api/local-data")
    assert deleted.status_code == 204
    assert web.get("/api/privacy").json()["monitoring_mode"] == "manual"
