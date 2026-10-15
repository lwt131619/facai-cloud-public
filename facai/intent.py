"""Lossless Intent Compiler data spine (docs/HUMAN_INTERACTION_LAYER.md).

The user speaks plainly; Facai compiles professionally. This module holds
the state contract the whole Human Interaction Layer projects to the three
UIs (Simple / Pro / Developer):

- :class:`RawRequestStore` — append-only. The user's words can never be
  overwritten by a normalized reading (INVARIANT 1).
- :class:`IntentObject` — what Facai understood. ``compiler_status ==
  SUCCESS`` means "understood", nothing more (INVARIANT 3): it is never
  mapped onto mission, authorization, backend or execution states.
- :class:`UserTaskState` — the sixteen user-visible task states (§25) with
  their Chinese copy, kept distinct from Authority Core states on purpose:
  ``GRANT_REQUIRED`` / ``PROVIDER_REFUSAL`` are imported from
  facai.authority so the UI cannot blur an authorization problem into an
  understanding problem.
- ``project_intent`` — one Intent object, three projections.

No model calls here: the compiler's semantic parsing rides the existing
prompting/gateway path and lands in these structures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# Authority Core states the UX must never disguise as intent problems
# (INVARIANT 3 / §13-15). Imported, not redefined.
from .authority import (
    DENY as _AUTH_DENY,  # noqa: F401  (re-exported for UX translators)
    GRANT_EXHAUSTED,
    GRANT_EXPIRED,
    GRANT_REQUIRED,
    PROVIDER_REFUSAL,
)

__all__ = [
    "GRANT_REQUIRED", "GRANT_EXPIRED", "GRANT_EXHAUSTED", "PROVIDER_REFUSAL",
    "INTENT_PARSE_FAILED", "MISSION_COMPILE_FAILED", "EXECUTION_FAILED",
    "COMPILER_SUCCESS", "COMPILER_NEEDS_CLARIFICATION", "COMPILER_FAILED",
    "UserTaskState", "RawRequestStore", "IntentObject", "compile_intent",
    "project_intent", "state_copy",
]

#INVARIANT 3 — five different failures, five different names. The tuples
# below double as a runtime uniqueness check (see tests).
INTENT_PARSE_FAILED = "INTENT_PARSE_FAILED"
MISSION_COMPILE_FAILED = "MISSION_COMPILE_FAILED"
EXECUTION_FAILED = "EXECUTION_FAILED"

COMPILER_SUCCESS = "SUCCESS"
COMPILER_NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
COMPILER_FAILED = "FAILED"


class UserTaskState:
    """The user-visible task lifecycle (§25). UI truth only: each state must
    be backed by a real store/event state — never faked (INVARIANT 4/5)."""

    RECEIVED = "RECEIVED"
    UNDERSTANDING = "UNDERSTANDING"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    INTENT_READY = "INTENT_READY"
    EXPANDING = "EXPANDING"
    PLANNING = "PLANNING"
    MISSION_READY = "MISSION_READY"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    WAITING_PERMISSION = "WAITING_PERMISSION"
    ROUTING = "ROUTING"
    EXECUTING = "EXECUTING"
    REPLANNING = "REPLANNING"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"

ALL_STATES = (
    UserTaskState.RECEIVED, UserTaskState.UNDERSTANDING,
    UserTaskState.NEEDS_CLARIFICATION, UserTaskState.INTENT_READY,
    UserTaskState.EXPANDING, UserTaskState.PLANNING,
    UserTaskState.MISSION_READY, UserTaskState.WAITING_CONFIRMATION,
    UserTaskState.WAITING_PERMISSION, UserTaskState.ROUTING,
    UserTaskState.EXECUTING, UserTaskState.REPLANNING,
    UserTaskState.WAITING_EXTERNAL, UserTaskState.VERIFYING,
    UserTaskState.COMPLETED, UserTaskState.PARTIAL, UserTaskState.FAILED,
)

STATE_COPY = {
    UserTaskState.RECEIVED: "收到了",
    UserTaskState.UNDERSTANDING: "发财正在理解你的需求",
    UserTaskState.NEEDS_CLARIFICATION: "还需要你补充一点信息",
    UserTaskState.INTENT_READY: "发财已经听懂了",
    UserTaskState.EXPANDING: "正在把你的想法整理成完整任务",
    UserTaskState.PLANNING: "正在想怎么完成",
    UserTaskState.MISSION_READY: "已经准备好了",
    UserTaskState.WAITING_CONFIRMATION: "等你确认后开始",
    UserTaskState.WAITING_PERMISSION: "需要你的允许",
    UserTaskState.ROUTING: "正在安排最合适的方式",
    UserTaskState.EXECUTING: "发财正在处理",
    UserTaskState.REPLANNING: "发财正在换一种办法",
    UserTaskState.WAITING_EXTERNAL: "正在等待外部服务",
    UserTaskState.VERIFYING: "发财正在检查结果",
    UserTaskState.COMPLETED: "已完成",
    UserTaskState.PARTIAL: "已完成一部分",
    UserTaskState.FAILED: "当前任务没有完成",
}


def state_copy(state: str) -> str:
    return STATE_COPY.get(state, state)


class RawRequestStore:
    """Append-only memory of the user's actual words (INVARIANT 1).

    There is deliberately no update or delete: a normalized reading may be
    revised, the user's sentence may not.
    """

    def __init__(self) -> None:
        self._requests: list[dict] = []

    def add(self, request_id: str, raw_text: str, source: str = "web") -> dict:
        if not raw_text or not raw_text.strip():
            raise ValueError("raw request text must be non-empty")
        row = {"id": request_id, "raw": raw_text, "source": source}
        self._requests.append(row)
        return dict(row)

    def raw(self, request_id: str) -> Optional[str]:
        for row in self._requests:
            if row["id"] == request_id:
                return row["raw"]
        return None

    def all(self) -> list[dict]:
        return [dict(row) for row in self._requests]


@dataclass
class IntentObject:
    """What Facai understood — never a replacement for what the user said."""

    request_id: str
    raw_request: str
    normalized_goal: Optional[str]
    intent_preserved: bool = True
    ambiguities: list = field(default_factory=list)
    compiler_status: str = COMPILER_SUCCESS
    semantic_state: str = "CLEAR"        # CLEAR | VAGUE
    target_state: str = "RESOLVED"       # RESOLVED | UNRESOLVED
    constraint_state: str = "PARTIAL"    # FULL | PARTIAL | NONE

    def __post_init__(self) -> None:
        if self.compiler_status == COMPILER_SUCCESS and not self.normalized_goal:
            # SUCCESS without a goal would be a fake understanding.
            raise ValueError("compiler SUCCESS requires a normalized_goal")
        if self.compiler_status == COMPILER_NEEDS_CLARIFICATION and not self.ambiguities:
            raise ValueError("NEEDS_CLARIFICATION requires listed ambiguities")


def compile_intent(request_id: str, raw_text: str, normalized_goal: Optional[str],
                   ambiguities: Optional[list] = None) -> IntentObject:
    """Assemble the compiler result. The semantic parsing itself happens on
    the model path; this is the lossless contract around it.

    No goal can never masquerade as SUCCESS: an unparseable-but-present
    request becomes NEEDS_CLARIFICATION with at least one stated ambiguity
    (the compiler says what it is missing, it does not invent a goal)."""
    ambiguities = list(ambiguities or [])
    if normalized_goal is None:
        if not ambiguities:
            ambiguities = ["无法确定具体目标"]
        return IntentObject(request_id=request_id, raw_request=raw_text,
                            normalized_goal=None, intent_preserved=True,
                            ambiguities=ambiguities,
                            compiler_status=COMPILER_NEEDS_CLARIFICATION,
                            semantic_state="VAGUE", target_state="UNRESOLVED")
    return IntentObject(request_id=request_id, raw_request=raw_text,
                        normalized_goal=normalized_goal, intent_preserved=True,
                        ambiguities=ambiguities, compiler_status=COMPILER_SUCCESS)


def project_intent(intent: IntentObject) -> dict:
    """One intent, three projections (§3): simple for customers, pro for
    operators, developer for the raw object."""
    understood = intent.compiler_status == COMPILER_SUCCESS
    simple = {
        "title": "发财听懂了" if understood else "发财大概明白了，但还差一点",
        "你的原话": intent.raw_request,
        "你的目标": intent.normalized_goal or "（待补充）",
        "理解情况": ["✓ 已理解"] if understood and not intent.ambiguities
                    else ([f"还需补充：{a}" for a in intent.ambiguities]
                          if intent.ambiguities else ["理解未完成"]),
    }
    pro = {
        "Raw Request": intent.raw_request,
        "Normalized Goal": intent.normalized_goal,
        "Intent Preservation": intent.intent_preserved,
        "Ambiguities": intent.ambiguities or "NONE",
        "Compiler": intent.compiler_status,
    }
    return {"simple": simple, "pro": pro, "developer": intent.__dict__.copy()}
