from __future__ import annotations

from dataclasses import dataclass

CATALOG_SOURCE = "https://github.com/anthropics/skills"
CATALOG_VERIFIED_AT = "2026-09-29"


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    description: str
    capabilities: frozenset[str]
    stack_signals: frozenset[str] = frozenset()
    health: float = 1.0
    publisher: str = "Anthropic"

    @property
    def source_url(self) -> str:
        return f"{CATALOG_SOURCE}/tree/main/skills/{self.id}"


# Verified first-party packages from anthropics/skills. Capability and stack mappings are
# SkillScope's planning metadata; names and source locations correspond to real SKILL.md files.
CATALOG: tuple[SkillDefinition, ...] = (
    SkillDefinition(
        "academy-guide",
        "Academy Guide",
        "Find relevant Claude Academy learning material for Claude products and workflows.",
        frozenset({"claude-learning"}),
        frozenset({"claude", "anthropic"}),
    ),
    SkillDefinition(
        "algorithmic-art",
        "Algorithmic Art",
        "Create original generative artwork with p5.js and seeded, interactive systems.",
        frozenset({"generative-art"}),
        frozenset({"p5.js", "javascript"}),
    ),
    SkillDefinition(
        "brand-guidelines",
        "Brand Guidelines",
        "Apply Anthropic's official visual identity to suitable artifacts.",
        frozenset({"anthropic-brand"}),
    ),
    SkillDefinition(
        "canvas-design",
        "Canvas Design",
        "Create original static visual designs delivered as PNG or PDF artifacts.",
        frozenset({"visual-art"}),
    ),
    SkillDefinition(
        "claude-api",
        "Claude API",
        "Build Claude-powered applications using current Anthropic API and SDK guidance.",
        frozenset({"claude-api"}),
        frozenset({"claude", "anthropic", "mcp", "agent"}),
    ),
    SkillDefinition(
        "discernment-nudge",
        "Discernment Nudge",
        "Add focused questions that help users test important claims and assumptions.",
        frozenset({"decision-support"}),
    ),
    SkillDefinition(
        "doc-coauthoring",
        "Document Co-authoring",
        "Guide collaborative creation and reader-testing of structured documents and specs.",
        frozenset({"word-documents"}),
    ),
    SkillDefinition(
        "docx",
        "DOCX",
        "Create, read, edit, and validate Microsoft Word documents and templates.",
        frozenset({"internal-comms"}),
        frozenset({"docx", "word"}),
    ),
    SkillDefinition(
        "frontend-design",
        "Frontend Design",
        "Create distinctive user interfaces with deliberate typography, layout, and styling.",
        frozenset({"frontend", "styling", "design-system", "accessibility"}),
        frozenset({"react", "next.js", "nextjs", "vue", "svelte", "html", "css"}),
    ),
    SkillDefinition(
        "internal-comms",
        "Internal Communications",
        "Draft status reports, leadership updates, FAQs, newsletters, and incident reports.",
        frozenset({"documentation"}),
    ),
    SkillDefinition(
        "mcp-builder",
        "MCP Builder",
        "Build high-quality Model Context Protocol servers for external services.",
        frozenset({"mcp"}),
        frozenset({"mcp", "fastmcp", "typescript", "python"}),
    ),
    SkillDefinition(
        "pdf",
        "PDF",
        "Create, read, edit, combine, extract, OCR, and validate PDF files.",
        frozenset({"pdf"}),
        frozenset({"pdf"}),
    ),
    SkillDefinition(
        "pptx",
        "PPTX",
        "Create, read, edit, and validate PowerPoint presentations and templates.",
        frozenset({"presentations"}),
        frozenset({"pptx", "powerpoint", "slides"}),
    ),
    SkillDefinition(
        "skill-creator",
        "Skill Creator",
        "Create, improve, evaluate, and benchmark Agent Skills and their triggering behavior.",
        frozenset({"skill-authoring"}),
        frozenset({"claude", "anthropic", "agent skills"}),
    ),
    SkillDefinition(
        "slack-gif-creator",
        "Slack GIF Creator",
        "Create and validate animated GIFs optimized for Slack.",
        frozenset({"slack-gif"}),
        frozenset({"slack", "gif"}),
    ),
    SkillDefinition(
        "theme-factory",
        "Theme Factory",
        "Apply or generate coordinated color and typography themes for artifacts.",
        frozenset({"theming"}),
    ),
    SkillDefinition(
        "web-artifacts-builder",
        "Web Artifacts Builder",
        "Build complex Claude.ai web artifacts with React, Tailwind CSS, and shadcn/ui.",
        frozenset({"claude-artifact"}),
        frozenset({"react", "tailwind", "tailwindcss", "shadcn"}),
    ),
    SkillDefinition(
        "webapp-testing",
        "Web Application Testing",
        "Test and debug local web applications with Playwright-driven browser automation.",
        frozenset({"testing"}),
        frozenset({"playwright", "react", "next.js", "nextjs", "web app"}),
    ),
    SkillDefinition(
        "xlsx",
        "XLSX",
        "Create, read, edit, calculate, chart, and validate spreadsheet files.",
        frozenset({"spreadsheets"}),
        frozenset({"xlsx", "excel", "spreadsheet", "csv"}),
    ),
)
