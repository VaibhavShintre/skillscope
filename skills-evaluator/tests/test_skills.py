from pathlib import Path

from skills_evaluator.profiler import profile_project
from skills_evaluator.skills import discover_candidates, load_skill

FIXTURES = Path(__file__).parent / "fixtures"


def test_loads_valid_skill_with_provenance() -> None:
    profile = profile_project(FIXTURES / "project")
    skill = load_skill(FIXTURES / "skills" / "testing", profile, "fixture", True)
    assert skill.name == "browser-testing"
    assert skill.user_requested is True
    assert skill.content_hash
    assert skill.blocked is False


def test_blocks_sensitive_instruction() -> None:
    profile = profile_project(FIXTURES / "project")
    skill = load_skill(FIXTURES / "skills" / "unsafe", profile, "fixture", True)
    assert skill.blocked is True
    assert any(item.severity == "block" for item in skill.findings)


def test_discovers_explicit_local_sources_without_network(tmp_path: Path) -> None:
    profile = profile_project(FIXTURES / "project")
    candidates = discover_candidates(
        profile,
        [str(FIXTURES / "skills" / "testing"), str(FIXTURES / "skills" / "frontend")],
        tmp_path / "cache",
        include_anthropic=False,
        maximum=12,
    )
    assert {item.name for item in candidates} == {"browser-testing", "frontend-design"}
