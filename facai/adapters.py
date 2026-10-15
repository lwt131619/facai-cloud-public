"""[8] Adapter SDK — §八 AgentAdapter/ProviderAdapter 契约形式化 (P0-7/P0-8).

The hard sequence (HANDOFF-NEXT.md §十三) puts "ADAPTER SDK:
AgentAdapter/ProviderAdapter 接口化" at [8]. This module makes the contract
checkable: a third-party agent CLI and a Facai Native planner are
interchangeable at the contract layer — same capabilities rows, same
proposal type (ActionIR), same brokered execution handshake, same §15
outcome vocabulary.

Two scope constraints, both operator-stated for this batch:

- The SDK exists FIRST as a wrapping layer. facai/opencode.py and
  facai/goose.py are untouched; the existing execution chain
  (facai/execution.py:execute_mission_task) is not switched over here.
- Adapters never bypass the Gate: ``execute_via_broker`` MUST obtain its
  scoped capability through :func:`facai.broker.request_capability`, whose
  single judgment is ``AuthorizationEngine.authorize`` (facai/broker.py).
  A DENY is therefore a *result*, not an exception path: it surfaces as an
  ``authority.ExecutionOutcome`` in the §15 local-decision vocabulary
  (facai/outcomes.py:normalize_local_decision — grant-shaped reasons are
  GRANT_REQUIRED, every other local refusal is FACAI_DENIED; the exact
  convention facai/execution.py already applies to gate verdicts).

No I/O and no storage: like facai/authority.py this module is policy plus
pure construction, so the whole contract is exercisable offline.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from . import broker as facai_broker
from .authority import ActionIR, ExecutionOutcome
from .broker import CapabilityDenied
from .capabilities import L2_ADAPTED, Capability, CapabilityRegistry
from .opencode import OpenCodeAdapter
from .outcomes import (
    RESOURCE_UNAVAILABLE,
    TERMINAL_EVENT_STATES,
    EXECUTION_FAILED,
    normalize_backend_response,
    normalize_local_decision,
    outcome_from_adapter_error,
)
from .registries import DEFAULTS as REGISTRY_DEFAULTS

# The L4 seed's on-record license: Facai's own implementation, nothing
# third-party is redistributed. capabilities._validate only blocks the
# literal UNKNOWN at L3/L4; "Proprietary" states the shipping truth.
FACAI_NATIVE_LICENSE = "Proprietary"


class AdapterContractError(ValueError):
    """An adapter violates the [8] contract: refused before it can register.

    register_adapter is the only door into the CapabilityRegistry for
    adapters, and it is a *checked* door: an adapter that cannot declare
    capabilities, cannot propose an ActionIR, or whose deny path does not
    really traverse the engine is exactly the adapter that would grow into
    a second authority root if it were waved through.
    """


@dataclass(frozen=True)
class AgentCapability:
    """One row an agent adapter contributes to the CapabilityRegistry.

    ``name`` IS the registry row id and must therefore already satisfy the
    namespaced lowercase lexicon ``<family>.<ability>`` (facai/capabilities
    _ID_RE); ``description`` is the human-readable row name. Keeping the
    row format this small is the point: derived and declared capabilities
    register through exactly the same path.
    """

    name: str
    description: str


@runtime_checkable
class AgentAdapter(Protocol):
    """The §八 agent contract: what every agent adapter must expose.

    Implementations may subclass (inheriting the brokered handshake below)
    or duck-type; :func:`register_adapter` checks behaviour either way.
    """

    name: str
    agent_id: str

    def capabilities(self) -> list:
        """Non-empty list of AgentCapability rows for the registry."""
        ...

    def plan(self, prompt, context=None) -> ActionIR:
        """Propose ONE step as an ActionIR. Pure construction — no I/O."""
        ...

    def _broker_scope(self, action: ActionIR) -> dict:
        """Extra scope carried on the capability request. Default: none."""
        return {}

    def _execute_granted(self, capability: dict, action: ActionIR) -> ExecutionOutcome:
        """Run the granted step; return an honest outcome, never a verdict."""
        ...

    def execute_via_broker(self, broker, engine, action: ActionIR) -> ExecutionOutcome:
        """The only execution door: request_capability first, always.

        约束（本批次的硬规则）：内部必经 request_capability——裁决只属于
        engine；DENY 不外泄异常，而是归一为九态 Outcome（grant 形理由
        GRANT_REQUIRED，其余本地拒绝 FACAI_DENIED，与 execution.py 对 Gate
        裁决的既有归一同一套）。ALLOW 时才委派 ``_execute_granted`` 真正执行。
        ``broker`` 是任何暴露 ``request_capability`` 的对象——生产路径传
        facai.broker 模块本身，自检也用它，适配器无法用假 broker 混过检查。
        """
        try:
            granted = broker.request_capability(
                engine, self.agent_id, action.backend, action.action,
                resource=action.resource, mission_id=action.mission_id,
                scope=self._broker_scope(action))
        except CapabilityDenied as denied:
            # DENY 是裁决结果，不是失败路径：按 §15 本地裁决归一成 Outcome。
            # 拒绝的留痕在 engine.decisions，本方法不重复记账。
            return normalize_local_decision(denied.reason, backend=action.backend)
        return self._execute_granted(granted, action)


@runtime_checkable
class ProviderAdapter(Protocol):
    """The §八 provider contract: interchangeable model backends.

    ``complete`` MUST funnel its upstream answer through
    :func:`facai.outcomes.normalize_backend_response` (via
    :func:`provider_outcome` below) so a native module and an external
    provider are indistinguishable above the line: one §15 state per
    response, never a raw HTTP status leaking upward.
    """

    name: str

    def complete(self, model_ref, messages) -> ExecutionOutcome:
        ...


def provider_outcome(status_code, body, backend="") -> ExecutionOutcome:
    """The single normalization exit for ProviderAdapter.complete.

    Wraps normalize_backend_response so "provider answers are §15-normalized"
    is one enforceable call site instead of a convention each adapter
    re-implements (and eventually skips).
    """
    return normalize_backend_response(status_code, body, backend)


class _DenyAllEngine:
    """契约自检用假引擎：记录每次 authorize 调用并全部 DENY。

    理由特意取非 grant 形（SELF_CHECK_DENY），使归一结果必为 FACAI_DENIED，
    从而把"拒绝路径真的走了引擎"变成可断言的事实：若适配器私短路，calls
    就不会增长；若适配器把 DENY 当异常外泄，execute_via_broker 就会抛。
    """

    def __init__(self):
        self.calls = []

    def authorize(self, action: ActionIR) -> dict:
        self.calls.append(action)
        return {"decision": "DENY", "reason": "SELF_CHECK_DENY",
                "grant_id": None, "backend": action.backend}


# Self-check probe context: plan() is pure construction by contract, so a
# sentinel workspace string exercises the proposal path without touching disk.
_SELF_CHECK_CONTEXT = {"workspace": "__selfcheck__", "mission_id": "__selfcheck__"}


def register_adapter(registry: CapabilityRegistry, adapter, license: str,
                     level: str = L2_ADAPTED, provider: str = "AgentProvider") -> list:
    """契约自检 + 登记：第三方适配器进 CapabilityRegistry 的唯一门。

    自检两件事（缺一拒登记，抛 AdapterContractError）：
    1) capabilities() 非空——没有能力行的适配器无从考核，也无从登记；
    2) execute_via_broker 的拒绝路径真的走 engine——用假引擎全 DENY，
       断言 (a) engine.authorize 恰被调用一次（没绕 Gate），(b) DENY 以
       FACAI_DENIED Outcome 返回而非异常（§九态归一生效）。

    登记本身沿用 CapabilityRegistry 的校验（行 id 词法、L3/L4 license）；
    level 默认 L2_ADAPTED（第三方包装层），license 必填。返回登记的行。
    """
    name = str(getattr(adapter, "name", "") or type(adapter).__name__)
    capabilities_method = getattr(adapter, "capabilities", None)
    if not callable(capabilities_method):
        raise AdapterContractError(f"{name}: 缺 capabilities()，不是 AgentAdapter 契约实现")
    if not str(getattr(adapter, "agent_id", "") or "").strip():
        raise AdapterContractError(f"{name}: 缺 agent_id，无法作为 capability 请求主体")
    if not str(license or "").strip():
        # "license 必填"：包装层也在登记时表态，不允许 UNKNOWN 混过默认关卡。
        raise AdapterContractError(f"{name}: 登记必须声明 license")

    caps = list(capabilities_method() or [])
    if not caps:
        raise AdapterContractError(f"{name}: 契约自检失败——capabilities() 为空，无可登记能力行")

    # 拒绝路径自检：真 broker + 假引擎。probe 是纯构建（无 IO），因此任何
    # 合法适配器都应能离线提出一个步提案。
    try:
        probe = adapter.plan("__selfcheck__", dict(_SELF_CHECK_CONTEXT))
    except AdapterContractError:
        raise
    except Exception as exc:
        raise AdapterContractError(
            f"{name}: 自检提案 plan() 失败（{type(exc).__name__}: {exc}）"
            "——步提案必须可离线纯构建") from exc
    if not isinstance(probe, ActionIR):
        raise AdapterContractError(f"{name}: plan() 必须产出 ActionIR 提案，得到 {type(probe).__name__}")
    engine = _DenyAllEngine()
    try:
        outcome = adapter.execute_via_broker(facai_broker, engine, probe)
    except Exception as exc:
        raise AdapterContractError(
            f"{name}: execute_via_broker 把 DENY 当异常外泄（{type(exc).__name__}: {exc}）"
            "——DENY 必须归一为 Outcome") from exc
    if len(engine.calls) != 1:
        raise AdapterContractError(
            f"{name}: 拒绝路径未经过引擎裁决（engine.authorize 调用 {len(engine.calls)} 次）"
            "——适配器永不绕 Gate")
    if getattr(outcome, "state", None) != "FACAI_DENIED":
        raise AdapterContractError(
            f"{name}: 自检 DENY 应归一为 FACAI_DENIED Outcome，得到 {getattr(outcome, 'state', None)!r}")

    rows = []
    for cap in caps:
        if not isinstance(cap, AgentCapability):
            raise AdapterContractError(
                f"{name}: capabilities() 必须产出 AgentCapability 行，得到 {type(cap).__name__}")
        # 行 id 词法 / level+license 不变式由 capabilities._validate 把关；
        # 这里只负责把行格式翻成 Capability 行并署名来源（自有实现可经
        # source_name 按 doctrine 署 "facai-native"）。
        rows.append(registry.register(Capability(
            id=cap.name, name=cap.description, level=level, provider=provider,
            source=str(getattr(adapter, "source_name", "") or name), license=license)))
    return rows


class FacaiNativePlanner(AgentAdapter):
    """Facai Native 样板：Native Capability Coverage 的第一个 L4 种子。

    plan() 返回简单单步 ActionIR——一个 prompt 恰好一个步提案：向 provider
    发起一次调用（provider.invoke 在冻结动作族 facai/broker.CAPABILITY_ACTIONS
    内，backend facai-native 是 T0 注册后端，引擎可裁决）。
    """

    name = "facai-native-planner"
    agent_id = "facai-native-planner"
    # 登记署名：自有实现按 doctrine 记 "facai-native"（capabilities.py source 约定）。
    source_name = "facai-native"

    def capabilities(self) -> list:
        return [AgentCapability(
            "planning.native_single_step",
            "Facai 自有单步规划：把一个 prompt 变成一个可裁决的 ActionIR 步提案")]

    def plan(self, prompt, context=None) -> ActionIR:
        context = context or {}
        return ActionIR(actor=self.agent_id, backend="facai-native",
                        action="provider.invoke",
                        resource=str(context.get("model_ref") or "facai-native/plan-step"),
                        mission_id=str(context.get("mission_id") or ""))

    # propose 与 plan 同义：步提案在 §八里只有一个动作，两个名字一个契约。
    propose = plan

    def _execute_granted(self, capability: dict, action: ActionIR) -> ExecutionOutcome:
        # 样例语义（诚实边界）：Native planner 的"执行"就是产出这一定步计划
        # 本身——这是本调用里真实完成的工作，EXECUTED 取 authority 的本地执行
        # 词汇（authority.ExecutionOutcome.executed）。真实 provider 调用属
        # 执行链切换（后续批次），此处不得伪装已经调用过上游。
        return ExecutionOutcome(
            state="EXECUTED",
            detail=f"facai-native 单步计划已产出：{action.backend}:{action.action}:{action.resource}")


class OpenCodeSDKAdapter(AgentAdapter):
    """第三方样板：把既有 OpenCodeAdapter 包进 §八契约（opencode.py 零改动）。

    - plan/propose 复用 inner 的 command 构建：提案必须建立在真实 CLI 契约上，
      不允许 SDK 私造第二条命令语法；
    - capabilities 从模型注册表 opencode_model 域推导：每台注册的 OpenCode
      模型是一行 model.* 能力，外加一行 agent.opencode.run 基础能力；ref 里
      的 "/"、"-" 不满足行 id 词法，归一为 "_"（人读的 ref 留在 description）。
    """

    def __init__(self, opencode: OpenCodeAdapter = None, models: dict = None,
                 agent_id: str = "opencode"):
        self._inner = opencode or OpenCodeAdapter()
        self._models = REGISTRY_DEFAULTS["models"] if models is None else models
        self.agent_id = agent_id
        self.name = "opencode-sdk"
        self._scope = {}

    def capabilities(self) -> list:
        caps = [AgentCapability("agent.opencode.run",
                                "在任务工作区内经 OpenCode CLI 运行一步代理任务")]
        for key in sorted(self._models or {}):
            entry = self._models.get(key) or {}
            ref = entry.get("opencode_model")
            if not ref:
                continue  # 不经 OpenCode 调用的模型不产生能力行
            slug = re.sub(r"[^a-z0-9_]", "_", str(ref).lower())
            caps.append(AgentCapability(f"model.opencode.{slug}",
                                        f"经 OpenCode CLI 调用注册模型 {key}（ref={ref}）"))
        return caps

    def plan(self, prompt, context=None) -> ActionIR:
        context = context or {}
        workspace = str(context.get("workspace") or "")
        if not workspace:
            # 没有目标工作区的 OpenCode 提案无法成为合法 shell.exec 提案。
            raise AdapterContractError(
                "OpenCodeSDKAdapter.plan 需要 context['workspace']（纯路径字符串，不要求已存在）")
        # opencode.py 零改动的包装保证：命令构建失败（如二进制缺失）在此暴露，
        # 而不是拖到执行期才变成一个来路不明的失败。
        command = self._inner.command(prompt, workspace, context.get("model_ref"))
        if not command:
            raise AdapterContractError("inner command 构建产出为空，拒绝提案")
        # prompt/model_ref 随 scope 走 scoped capability，不进 ActionIR、不落盘。
        self._scope = {"prompt": prompt, "model_ref": context.get("model_ref") or ""}
        return ActionIR(actor=self.agent_id, backend="opencode", action="shell.exec",
                        resource=workspace, mission_id=str(context.get("mission_id") or ""))

    propose = plan  # 与 plan 同义：步提案只有一个契约动作。

    def _broker_scope(self, action: ActionIR) -> dict:
        return dict(self._scope)

    def _execute_granted(self, capability: dict, action: ActionIR) -> ExecutionOutcome:
        # 执行链切换属后续批次：本样板在 ALLOW 后仍经既有 inner.execute 通道
        # 真实运行一次 CLI，并把其终态经冻结映射翻成 §15（SUCCESS /
        # EXECUTION_FAILED / TIMEOUT）。不伪造成功，也不把退出码吞成异常。
        scope = (capability or {}).get("scope") or {}
        workspace = action.resource
        if not workspace:
            return ExecutionOutcome(state=RESOURCE_UNAVAILABLE,
                                    detail="提案未携带 workspace，OpenCode 未运行。")
        mission = action.mission_id or "adapter-sdk"
        # resource 约定即 <root>/workspaces/<mission>，root 由此还原；
        # 结构不符时 inner.workspace 会拒绝，归一为 EXECUTION_FAILED。
        root = str(Path(workspace).parent.parent)
        try:
            result = self._inner.execute(mission, root, scope.get("prompt", ""),
                                         scope.get("model_ref") or None)
        except Exception as exc:
            return outcome_from_adapter_error(exc, backend=self.agent_id)
        state = TERMINAL_EVENT_STATES.get(result.get("status"), EXECUTION_FAILED)
        return ExecutionOutcome(
            state=state,
            detail=f"OpenCode CLI 终态 {result.get('status')}（exit_code={result.get('exit_code')}，"
                   f"workspace={workspace}）")
