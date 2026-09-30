"""A built-in decoy skill: a plausible description over a body that says nothing useful.

If a model loads it anyway, that measures how strongly a skill listing alone pulls a model in.
Any score lift a bundle gets from also listing the decoy shows how much of a bundle's advantage
can come from merely listing another skill, not from what the skills contain.
"""

from __future__ import annotations

import hashlib

from skills_evaluator.models import SkillCandidate

DECOY_NAME = "project-conventions"
DECOY_DESCRIPTION = (
    "Apply this project's coding conventions, architecture decisions and review checklist "
    "when implementing, reviewing or planning changes to its code."
)
DECOY_BODY = f"""---
name: {DECOY_NAME}
description: {DECOY_DESCRIPTION}
---

# Project conventions

This skill defines no conventions and adds no guidance. Carry on with the task using your
normal judgment.
"""


def build_decoy() -> SkillCandidate:
    digest = hashlib.sha256(DECOY_BODY.encode("utf-8")).hexdigest()
    return SkillCandidate(
        id=f"builtin/decoy@{DECOY_NAME}#{digest[:12]}",
        name=DECOY_NAME,
        description=DECOY_DESCRIPTION,
        source="builtin:decoy",
        source_path="",
        content_hash=digest,
        body=DECOY_BODY,
        # Categorized, so it is judged everywhere: any firing on any prompt is a false positive.
        categories=["decoy"],
        user_requested=True,
        decoy=True,
    )
