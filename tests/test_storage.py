from pathlib import Path

from skillscope.analysis import analyze_project
from skillscope.models import MonitoringMode, PrivacySettings, ProjectInput
from skillscope.storage import LocalStore


def test_privacy_defaults_are_opt_out(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "data.db")
    privacy = store.get_privacy()
    assert privacy.monitoring_mode is MonitoringMode.MANUAL
    assert privacy.cloud_sync is False
    assert privacy.anonymous_telemetry is False
    assert privacy.automatic_api_spend is False
    assert privacy.automatic_changes is False


def test_privacy_and_analysis_round_trip_and_delete(tmp_path: Path) -> None:
    store = LocalStore(tmp_path / "data.db")
    privacy = PrivacySettings(
        monitoring_mode=MonitoringMode.LOCAL,
        monitored_scopes=["skills", "planning", "skills"],
    )
    store.save_privacy(privacy)
    loaded = store.get_privacy()
    assert loaded.monitoring_mode is MonitoringMode.LOCAL
    assert loaded.monitored_scopes == ["planning", "skills"]

    result = analyze_project(
        ProjectInput(name="API", description="A secure FastAPI service with PostgreSQL")
    )
    store.save_analysis(result)
    assert store.get_analysis(result.analysis_id) == result
    assert store.summary().analyses == 1

    exported = store.export_all()
    assert len(exported["analyses"]) == 1
    store.delete_all()
    assert store.summary().analyses == 0
    assert store.get_privacy().monitoring_mode is MonitoringMode.MANUAL
