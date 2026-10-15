"""FACAI Event Bus (ICS 事件层) — the typed vocabulary every client subscribes to.

Operator doctrine (2026-10-13, ICS 平台化阶段): 手机开始任务、电脑看到任务运行、
Telegram 收到进度、Web 显示流水——所有客户端订阅同一条事件流，状态同步靠事件
不靠轮询约定。The durable spine already exists (the events table's global
AUTOINCREMENT sequence, now exposed owner-scoped via ``Store.events_for_owner``);
this module contributes two things on top:

- CANONICAL_TYPES: the ICS event vocabulary (MissionCreated … DeploymentFinished,
  plus the evolution/boundary decisions). New code publishes THESE names so
  clients can switch on type without per-module conventions. Legacy types
  (PROMPT_CONVERTED, FEEDBACK_GATE_OPENED, …) keep flowing unchanged — the bus
  carries them verbatim; it does not gate the past.
- publish(): one validated write helper. A typo'd event type fails fast at the
  publisher, never silently at the subscriber.

This is deliberately vocabulary-plus-helper, not a broker: the store stays the
single source of event truth (same doctrine as boundary.json — state is a file
or a table, never a second copy in memory that can drift).
"""
from __future__ import annotations

# The ICS canonical vocabulary. Values are stable API surface: clients may
# switch on them; renaming one is a breaking change and needs an operator note.
MISSION_LIFECYCLE = (
    "MissionCreated",        # 大白话已转换，任务落库
    "MissionStarted",        # 派发开始（首个作业取得租约）
    "StepCompleted",         # 一个阶段完成
    "StepFailed",            # 一个阶段失败
    "ArtifactGenerated",     # 产出物入库
    "VerificationPassed",    # 验证阶段通过
    "OwnerApproved",         # 所有者裁决：放行
    "DeploymentFinished",    # 交付阶段完成
)
EVOLUTION_LIFECYCLE = (
    "SkillDiscovered",       # 进化管道发现候选
    "SkillAdmitted",         # 新技能经边界闸门入库
    "SkillQuarantined",      # 边界闸门拦截
    "EvolutionApproved",     # 所有者批准版本升级
    "EvolutionRejected",     # 所有者驳回版本升级
)
CANONICAL_TYPES = frozenset(MISSION_LIFECYCLE + EVOLUTION_LIFECYCLE)


class EventBusError(ValueError):
    """Raised when a publish would put a non-canonical type on the bus."""


def publish(store, mission_id, event_type, payload, step_id=None,
            session_id=None, agent_id=None):
    """Append one canonical event. New publishers use this instead of calling
    store.append directly so a misspelled type dies here, at the writer."""
    if event_type not in CANONICAL_TYPES:
        raise EventBusError(
            f"非规范事件类型 {event_type!r}：新代码请使用 CANONICAL_TYPES 中的名称")
    if not isinstance(payload, dict):
        raise EventBusError("事件 payload 必须是对象")
    return store.append(mission_id, event_type, payload,
                        step_id=step_id, session_id=session_id,
                        agent_id=agent_id)
