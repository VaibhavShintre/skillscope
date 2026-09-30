from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import yaml

from skills_evaluator.models import ProjectProfile, SecurityFinding, SkillCandidate

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TOKEN = re.compile(r"[a-z0-9][a-z0-9+.#-]{1,}")


def _folder_hash(folder: Path) -> tuple[str, list[str]]:
    digest = hashlib.sha256()
    files: list[str] = []
    resolved_folder = folder.resolve()
    for path in sorted(item for item in folder.rglob("*") if item.is_file()):
        resolved_path = path.resolve()
        if resolved_folder not in resolved_path.parents:
            raise ValueError(f"Skill file escapes its source folder through a symlink: {path}")
        relative = path.relative_to(folder).as_posix()
        if ".git" in path.parts or path.stat().st_size > 2_000_000:
            continue
        files.append(relative)
        digest.update(relative.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest(), files


def _security_findings(folder: Path, body: str, files: list[str]) -> list[SecurityFinding]:
    findings: list[SecurityFinding] = []
    lowered = body.lower()
    sensitive = ("~/.ssh", "/.ssh", "~/.aws", "/.aws", "credentials.json")
    if any(marker in lowered for marker in sensitive):
        findings.append(
            SecurityFinding(
                severity="block",
                message="Instructions reference a sensitive credential directory or file.",
                path="SKILL.md",
            )
        )
    if "ignore previous" in lowered or "ignore all previous" in lowered:
        findings.append(
            SecurityFinding(
                severity="warning",
                message="Instructions contain an override-style prompt phrase.",
                path="SKILL.md",
            )
        )
    script_files = [name for name in files if name.startswith(("scripts/", "bin/"))]
    if script_files:
        findings.append(
            SecurityFinding(
                severity="info",
                message=(
                    f"Bundle contains {len(script_files)} script file(s); scripts are not executed."
                ),
                path=str(folder),
            )
        )
    return findings


def _relevance(profile: ProjectProfile, name: str, description: str, body: str) -> float:
    project_tokens = set(
        TOKEN.findall(
            " ".join(
                [
                    profile.name,
                    profile.summary,
                    *profile.languages,
                    *profile.technologies,
                    *profile.capabilities,
                ]
            ).lower()
        )
    )
    skill_tokens = set(TOKEN.findall(f"{name} {description} {body[:4000]}".lower()))
    stop = {"this", "that", "with", "from", "when", "skill", "use", "using", "users"}
    overlap = (project_tokens - stop) & (skill_tokens - stop)
    return min(1.0, len(overlap) / max(3, len(project_tokens - stop)))


def load_skill(
    folder: Path, profile: ProjectProfile, source: str, requested: bool
) -> SkillCandidate:
    folder = folder.resolve()
    skill_file = folder / "SKILL.md"
    if not skill_file.is_file():
        raise ValueError(f"No SKILL.md found in {folder}")
    raw = skill_file.read_text(encoding="utf-8")
    match = FRONTMATTER.match(raw)
    if not match:
        raise ValueError(f"SKILL.md has no YAML frontmatter: {skill_file}")
    metadata = yaml.safe_load(match.group(1)) or {}
    name = str(metadata.get("name", "")).strip()
    description = str(metadata.get("description", "")).strip()
    if not SAFE_NAME.fullmatch(name):
        raise ValueError(f"Invalid skill name {name!r} in {skill_file}")
    if not description:
        raise ValueError(f"Skill description is empty in {skill_file}")
    raw_category = metadata.get("category", metadata.get("categories")) or []
    categories = sorted(
        {
            str(item).strip().lower()
            for item in (raw_category if isinstance(raw_category, list) else [raw_category])
            if str(item).strip()
        }
    )
    digest, files = _folder_hash(folder)
    findings = _security_findings(folder, raw, files)
    commit = _git_commit(folder)
    canonical_source = source.replace("\\", "/")
    return SkillCandidate(
        id=f"{canonical_source}@{name}#{commit or digest[:12]}",
        name=name,
        description=description,
        source=canonical_source,
        source_path=str(folder),
        source_commit=commit,
        content_hash=digest,
        body=raw,
        files=files,
        findings=findings,
        blocked=any(item.severity == "block" for item in findings),
        user_requested=requested,
        relevance=_relevance(profile, name, description, raw),
        categories=categories,
    )


def _git_commit(path: Path) -> str | None:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def _clone_repository(repo: str, cache_root: Path, ref: str | None = None) -> Path:
    identity = f"{repo}@{ref}" if ref else repo
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", identity).strip("-")
    target = cache_root / safe
    if target.exists():
        return target
    cache_root.mkdir(parents=True, exist_ok=True)
    url = repo if "://" in repo else f"https://github.com/{repo}.git"
    command = ["git", "clone", "--depth", "1"]
    if ref:
        command.extend(["--branch", ref])
    command.extend([url, str(target)])
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise ValueError(f"Could not fetch skill source {repo}: {completed.stderr.strip()}")
    return target


def _skill_folders(root: Path) -> list[Path]:
    if (root / "SKILL.md").is_file():
        return [root]
    return sorted({path.parent for path in root.rglob("SKILL.md") if ".git" not in path.parts})


def resolve_source(source: str, cache_root: Path) -> list[Path]:
    local = Path(source).expanduser()
    if local.exists():
        return _skill_folders(local.resolve())
    tree_match = re.fullmatch(
        r"https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)/(.*)", source.rstrip("/")
    )
    if tree_match:
        owner, repository, ref, subpath = tree_match.groups()
        root = _clone_repository(f"{owner}/{repository}", cache_root, ref)
        selected = (root / subpath).resolve()
        if root.resolve() not in selected.parents and selected != root.resolve():
            raise ValueError(f"Unsafe GitHub skill path: {source}")
        if not selected.exists():
            raise ValueError(f"GitHub skill path was not found: {source}")
        return _skill_folders(selected)
    if "@" in source and "://" not in source:
        repository, skill_name = source.rsplit("@", 1)
        root = _clone_repository(repository, cache_root)
        matches = [folder for folder in _skill_folders(root) if folder.name == skill_name]
        if not matches:
            raise ValueError(f"Skill {skill_name!r} was not found in {repository}")
        return matches
    root = _clone_repository(source, cache_root)
    return _skill_folders(root)


def discover_candidates(
    profile: ProjectProfile,
    sources: list[str],
    cache_root: Path,
    include_anthropic: bool,
    maximum: int,
) -> list[SkillCandidate]:
    requested_folders: list[tuple[Path, str, bool]] = []
    project_skills = Path(profile.root) / ".claude" / "skills"
    if project_skills.is_dir():
        requested_folders.extend(
            (folder, "project-installed", True) for folder in _skill_folders(project_skills)
        )
    for source in sources:
        requested_folders.extend(
            (folder, source, True) for folder in resolve_source(source, cache_root)
        )
    if include_anthropic:
        official_root = _clone_repository("anthropics/skills", cache_root)
        requested_folders.extend(
            (folder, "anthropics/skills", False)
            for folder in _skill_folders(official_root / "skills")
        )

    candidates: list[SkillCandidate] = []
    seen_hashes: set[str] = set()
    errors: list[str] = []
    for folder, source, requested in requested_folders:
        try:
            candidate = load_skill(folder, profile, source, requested)
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as error:
            errors.append(str(error))
            continue
        if candidate.content_hash in seen_hashes:
            continue
        seen_hashes.add(candidate.content_hash)
        candidates.append(candidate)
    candidates.sort(key=lambda item: (not item.user_requested, -item.relevance, item.name))
    selected = [item for item in candidates if item.user_requested]
    if len(selected) > maximum:
        raise ValueError(
            f"{len(selected)} explicitly requested skills exceed the candidate cap of {maximum}."
        )
    selected_ids = {item.id for item in selected}
    selected.extend(
        item
        for item in candidates
        if item.id not in selected_ids and not item.blocked and item.relevance > 0
    )
    if errors and not selected:
        raise ValueError("No valid skills were loaded. " + " | ".join(errors[:3]))
    return selected[:maximum]
