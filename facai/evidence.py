"""执行证据链 (evidence chain): one checkable proof bundle per mission.

``build_proof`` assembles what a mission actually did — grants, backend
attempts, step outcomes, cost and artifacts — from persistent evidence only.
The AuthorizationEngine's decision log lives in process memory (the frozen P0
core has no storage), so it is deliberately NOT read here: the durable event
log is the evidence source instead.

约束 (frozen by the standard this module implements):

- 全部字段来自持久层：store 的事件流 + missions/steps/costs/artifacts 表。
  无任何进程内存依赖（engine.decisions、scheduler 状态都不读）。
- 无编造字段：某节缺持久证据就如实返回空数组；步骤没有落过带
  ``outcome_state`` 的事件，``outcome_state`` 就是 None。
- ``verified`` 是严格合取：全部步骤终态 且 成本账本至少一行。它核对的是
  证据链的完整性，不是任务成功与否——成功与否由 ``mission.status`` 如实
  携带（FAILED 也是终态）。

Section-to-event mapping (the ask's evidence sources):

- grants:   MISSION_DENIED 事件（门拒签的持久记录；每条含 reason/mode/
  outcome_state，可对照 authority_gate.DENY_COPY）。
- backends: BACKEND_ATTEMPT 与 MISSION_REROUTED 事件，按事件流顺序——
  哪个后端试过、§15 状态、路由去向。
- steps:    steps 表给出 ``state``；``outcome_state`` 取该步骤事件流里
  最后一条带 ``payload.outcome_state`` 的事件（OPENCODE_COMPLETED/
  FAILED/TIMEOUT、MISSION_DENIED 等九态落点）。JOB_REQUEUED/OPENCODE_*
  等其余执行事件保留在既有 /events 时间线里，这里不复制，避免同一事实
  出现两种权威形状。
"""

# Authorization-related event types: the durable stand-ins for the gate's
# in-memory decision log. MISSION_DENIED is the only one emitted today; the
# tuple keeps the proof's grant section pinned to exactly these records.
GRANT_EVENT_TYPES = ("MISSION_DENIED",)

# Backend-execution event types for the backends sequence, in log order.
BACKEND_EVENT_TYPES = ("BACKEND_ATTEMPT", "MISSION_REROUTED")

# Step statuses that end a step's life. WAITING/RUNNING mean unfinished work,
# so a mission carrying either is not fully evidenced yet.
TERMINAL_STEP_STATES = frozenset({"COMPLETED", "FAILED", "BLOCKED", "CANCELLED"})


def build_proof(store, mission_id):
    """Assemble one mission's evidence bundle from persisted state only.

    Raises ``KeyError`` for a mission the store does not hold (the API layer
    turns that into the same 404 as every other per-mission read).
    """
    mission = store.get(mission_id)
    if mission is None:
        raise KeyError(mission_id)
    events = store.events(mission_id)

    # 全部来自持久事件：授权节只认 MISSION_DENIED（门拒签），后端节只认
    # BACKEND_ATTEMPT/MISSION_REROUTED。整行返回（sequence/occurred_at/
    # payload 原样），审计方可拿 sequence 回 /events 逐条核对。
    grants = [event for event in events if event["type"] in GRANT_EVENT_TYPES]
    backends = [event for event in events if event["type"] in BACKEND_EVENT_TYPES]

    # 每步的 §15 结果：该步骤事件流里最后一条带 outcome_state 的事件说了算；
    # 从没执行过的步骤如实为 None，绝不推断。
    outcome_by_step = {}
    for event in events:
        step_id = event.get("step_id")
        state = (event.get("payload") or {}).get("outcome_state")
        if step_id and state is not None:
            outcome_by_step[step_id] = state
    steps = [{"id": step["id"], "state": step["status"],
              "outcome_state": outcome_by_step.get(step["id"])}
             for step in mission["steps"]]

    # 成本行与产物清单：直接读表（store.costs 为只读查询），缺则空数组。
    costs = store.costs(mission_id)
    artifacts = mission["artifacts"]

    verified = (all(step["state"] in TERMINAL_STEP_STATES for step in steps)
                and bool(costs))
    return {
        "mission": {"id": mission["id"], "status": mission["status"],
                    "owner": mission["owner_id"], "created": mission["created_at"]},
        "grants": grants,
        "backends": backends,
        "steps": steps,
        "costs": costs,
        "artifacts": artifacts,
        "verified": verified,
    }
