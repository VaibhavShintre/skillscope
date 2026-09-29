from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from skills_evaluator.models import ProjectProfile, path_text

IGNORED_PARTS = {
    ".git",
    ".venv",
    ".skillscope",
    ".skills-evaluator",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
}
SECRET_NAMES = {".env", ".env.local", "credentials", "credentials.json", "secrets.yaml"}
CONTEXT_NAMES = {
    "readme.md",
    "pyproject.toml",
    "package.json",
    "cargo.toml",
    "go.mod",
    "requirements.txt",
    "dockerfile",
    "compose.yaml",
    "docker-compose.yml",
}
EXTENSION_LANGUAGE = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".rs": "Rust",
    ".go": "Go",
    ".java": "Java",
    ".cs": "C#",
    ".rb": "Ruby",
    ".php": "PHP",
}
TECH_SIGNALS = {
    "react": "React",
    "next": "Next.js",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "postgres": "PostgreSQL",
    "redis": "Redis",
    "playwright": "Playwright",
    "pytest": "Pytest",
    "stripe": "Stripe",
    "docker": "Docker",
    "tailwind": "Tailwind CSS",
    "anthropic": "Claude API",
}
CAPABILITY_SIGNALS = {
    "test": "testing",
    "auth": "authentication",
    "api": "api",
    "database": "database",
    "postgres": "database",
    "frontend": "frontend",
    "react": "frontend",
    "deploy": "deployment",
    "docker": "deployment",
    "security": "security",
    "accessibility": "accessibility",
    "documentation": "documentation",
}


def _eligible(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    return not any(part in IGNORED_PARTS for part in relative.parts) and (
        path.name.lower() not in SECRET_NAMES
    )


def profile_project(root: Path) -> ProjectProfile:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"Project directory does not exist: {root}")

    language_counts: Counter[str] = Counter()
    context_paths: list[Path] = []
    manifests: list[str] = []
    digest = hashlib.sha256()
    searchable: list[str] = [root.name]

    for path in sorted(root.rglob("*")):
        if not path.is_file() or not _eligible(path, root):
            continue
        language = EXTENSION_LANGUAGE.get(path.suffix.lower())
        if language:
            language_counts[language] += 1
        relative = path.relative_to(root).as_posix()
        lower_name = path.name.lower()
        if lower_name in CONTEXT_NAMES or relative.lower().startswith("docs/"):
            if path.stat().st_size <= 250_000 and len(context_paths) < 80:
                context_paths.append(path)
                if lower_name in CONTEXT_NAMES:
                    manifests.append(relative)
                text = path.read_text(encoding="utf-8", errors="ignore")[:20_000]
                searchable.append(text)
                digest.update(relative.encode())
                digest.update(text.encode())
        elif language and len(context_paths) < 80:
            digest.update(relative.encode())
            digest.update(str(path.stat().st_size).encode())

    corpus = "\n".join(searchable).lower()
    technologies = sorted({value for key, value in TECH_SIGNALS.items() if key in corpus})
    capabilities = sorted({value for key, value in CAPABILITY_SIGNALS.items() if key in corpus})
    languages = [name for name, _ in language_counts.most_common(8)]
    summary = (
        f"{root.name}: languages {', '.join(languages) or 'unknown'}; "
        f"technologies {', '.join(technologies) or 'not detected'}; "
        f"capabilities {', '.join(capabilities) or 'general development'}."
    )
    return ProjectProfile(
        root=path_text(root),
        name=root.name,
        content_hash=digest.hexdigest(),
        languages=languages,
        technologies=technologies,
        capabilities=capabilities,
        manifests=sorted(set(manifests)),
        context_files=[path.relative_to(root).as_posix() for path in context_paths],
        excluded_patterns=sorted(IGNORED_PARTS | SECRET_NAMES),
        summary=summary,
    )
