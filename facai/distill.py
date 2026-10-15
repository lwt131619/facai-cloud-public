"""Distillation pipeline (task book §十一 D): turn finished missions into
reusable skill metadata.

A completed mission often encodes a repeatable recipe — the step DAG, the
prompts that worked, the agent/model binding. This module extracts that
recipe and registers it as immediately usable skill metadata
(``review_status="approved"``): a recipe proven by a DONE mission is a
capability of the station, adopted the moment it exists (§十一.C.6 — no
pending-only adoption gate). The owner can still disable or delete the
entry through the registries at any time.

Nothing here touches a live mission: only DONE missions are eligible,
and the extracted text is truncated to keep registry entries lightweight.
"""
from __future__ import annotations

import re
from typing import Optional

MAX_TEXT = 400
MAX_STEPS = 12

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def _slug(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug[:48]


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def extract_recipe(mission: dict) -> Optional[dict]:
    """Build skill metadata from a DONE mission, or None when not eligible."""
    if mission.get("status") != "DONE":
        return None
    steps = [s for s in (mission.get("steps") or []) if (s.get("title") or "").strip()]
    if not steps:
        return None
    titles = [_clip(s["title"], 80) for s in steps[:MAX_STEPS]]
    # CJK titles reduce to an empty ascii slug; fall back to the mission id so
    # two differently-named Chinese missions can never collide on one key.
    name = _slug(mission.get("title") or "") or f"m-{str(mission.get('id', ''))[:8]}"
    return {
        "name": name,
        "description": _clip(mission.get("original_input") or mission.get("title") or ""),
        "source_url": f"facai://missions/{mission.get('id', '')}",
        "license": "Proprietary",
        "review_status": "approved",
        "steps": titles,
        "agent_id": mission.get("agent_id") or "",
        "model_id": mission.get("model_id") or "",
        "runtime_id": mission.get("runtime_id") or "",
    }


def distill_mission(store, mission_id: str, owner_id: str) -> dict:
    """Register one DONE mission's recipe as a usable skill.

    Idempotent per mission: re-distilling overwrites the same key
    (``distilled-<slug>``) so repeated runs never pile up entries.
    """
    mission = store.get(mission_id)
    recipe = extract_recipe(mission)
    if recipe is None:
        raise ValueError("只有已完成（DONE）且有步骤的任务才能蒸馏")
    key = f"distilled-{recipe['name']}"
    store.registry_register("skills", key, recipe, owner_id=owner_id)
    return {"key": key, "skill": recipe}
