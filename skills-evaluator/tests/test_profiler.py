from pathlib import Path

from skills_evaluator.profiler import profile_project

FIXTURES = Path(__file__).parent / "fixtures"


def test_profile_detects_stack_and_excludes_secrets() -> None:
    profile = profile_project(FIXTURES / "project")
    assert "TypeScript" in profile.languages
    assert "React" in profile.technologies
    assert "testing" in profile.capabilities
    assert ".env" not in profile.context_files
    assert "must-never-be-profiled" not in profile.summary
