"""§15 Provider Refusal Normalization: nine states, never one MISSION_FAILED.

The frozen baseline (docs/AUTHORITY_ARCHITECTURE.md §14/§15) splits every
terminal backend result into exactly nine states:

    SUCCESS, PROVIDER_REFUSAL, FACAI_DENIED, GRANT_REQUIRED,
    CAPABILITY_MISSING, RESOURCE_UNAVAILABLE, BACKEND_UNAVAILABLE,
    EXECUTION_FAILED, TIMEOUT

Folding them into a single MISSION_FAILED would erase the one fact the
console must be able to show: on a provider refusal THIS BACKEND DID NOT
EXECUTE, while the Owner's mission grant is untouched (§14 sanctions the
pair OWNER_APPROVED + PROVIDER_REFUSAL) and replanning on another backend
is the correct continuation, not an error path. Conversely a local deny
(FACAI_DENIED) never claims the provider said anything at all.

Mapping contract
----------------
``normalize_backend_response`` maps the gateway's upstream outcome
(``facai.gateway.classify_response``) onto the nine states:

- 429 (rate limit) and content refusals           -> PROVIDER_REFUSAL
- 401/403 (auth)                                  -> PROVIDER_REFUSAL + auth hint
- any other 4xx the provider answers with          -> PROVIDER_REFUSAL
  (the provider was reached and declined; claiming EXECUTION_FAILED would
  assert a run that never happened)
- 504 and transport timeouts                       -> TIMEOUT
- other 5xx and transport errors                   -> BACKEND_UNAVAILABLE
- tool errors after the run started                -> EXECUTION_FAILED
- a completed upstream answer                      -> SUCCESS

Local decisions never enter through the HTTP half: ``normalize_local_decision``
maps gate/authority reasons (GRANT_REQUIRED/GRANT_EXPIRED/GRANT_EXHAUSTED ->
GRANT_REQUIRED, everything else -> FACAI_DENIED) and
``outcome_from_adapter_error`` classifies an adapter exception before any
terminal status exists. ``to_user_copy`` is the §14 user-facing wording: a
refusal must never read as "the request could not be understood".
"""

import subprocess

from .authority import (
    GRANT_EXHAUSTED as _GATE_GRANT_EXHAUSTED,
    GRANT_EXPIRED as _GATE_GRANT_EXPIRED,
    GRANT_REQUIRED as _GATE_GRANT_REQUIRED,
    ExecutionOutcome,
)
from .gateway import OUTCOME_LABELS, classify_response, coerce_json_object

SUCCESS = "SUCCESS"
PROVIDER_REFUSAL = "PROVIDER_REFUSAL"
FACAI_DENIED = "FACAI_DENIED"
GRANT_REQUIRED = "GRANT_REQUIRED"
CAPABILITY_MISSING = "CAPABILITY_MISSING"
RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"
BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
EXECUTION_FAILED = "EXECUTION_FAILED"
TIMEOUT = "TIMEOUT"

# The frozen §15 vocabulary, in order. Exactly nine; nothing may be added
# that is actually one of these wearing a different name (e.g. a catch-all
# MISSION_FAILED). GRANT_REQUIRED shares its name and value with the gate
# reason of the same name (facai.authority): both describe the same local
# fact — a valid grant is required and absent.
STATES = (
    SUCCESS,
    PROVIDER_REFUSAL,
    FACAI_DENIED,
    GRANT_REQUIRED,
    CAPABILITY_MISSING,
    RESOURCE_UNAVAILABLE,
    BACKEND_UNAVAILABLE,
    EXECUTION_FAILED,
    TIMEOUT,
)

# Terminal adapter statuses (facai.execution.TERMINAL_STATUSES) -> §15 state.
# OPENCODE_FAILED stays an execution failure: the backend ran and exited
# nonzero, which is not a refusal by anyone.
TERMINAL_EVENT_STATES = {
    "OPENCODE_COMPLETED": SUCCESS,
    "OPENCODE_FAILED": EXECUTION_FAILED,
    "OPENCODE_TIMEOUT": TIMEOUT,
}

# §14 user copy. The refusal line is deliberate: the request WAS understood;
# the model simply stopped, and the platform keeps looking for another way.
# No state may tell the owner "无法理解" — that would fold a backend state
# back into a comprehension verdict.
USER_COPY = {
    SUCCESS: "这一步已完成。",
    PROVIDER_REFUSAL: "当前使用的模型没有继续处理这一步，正在检查其他可用方式。",
    FACAI_DENIED: "这一步被平台权限规则拒绝，未调用任何执行后端。",
    GRANT_REQUIRED: "这一步缺少有效授权，已暂停且未调用执行后端。",
    CAPABILITY_MISSING: "当前环境缺少这一步所需的执行能力，已跳过该方式。",
    RESOURCE_UNAVAILABLE: "这一步所需的本地资源当前不可用，已暂停该方式。",
    BACKEND_UNAVAILABLE: "这一步的后端暂时不可用，正在检查其他可用方式。",
    EXECUTION_FAILED: "这一步在执行过程中失败，可以重试或改用其他方式。",
    TIMEOUT: "这一步等待超时，暂时没有结果，可以重试。",
}

# The gate's grant reasons (facai.authority) all mean the same §15 fact: a
# valid grant is required and absent. They are local facts, not refusals by
# any remote provider.
_GRANT_REASONS = frozenset({_GATE_GRANT_REQUIRED, _GATE_GRANT_EXPIRED,
                            _GATE_GRANT_EXHAUSTED})

# Hint attached to auth-type refusals so the operator knows what to check —
# and that only THIS backend is affected.
AUTH_HINT = "请检查该后端的 API Key / 凭据配置；该拒绝只影响这一后端。"


def to_user_copy(state):
    """The §14 owner-facing sentence for one §15 state.

    Raises on an unknown state: a state typo must crash here rather than
    render as an empty or misleading message in the console.
    """
    try:
        return USER_COPY[state]
    except KeyError:
        raise ValueError(f"unknown outcome state: {state!r}") from None


def _named(backend):
    return f"{backend} 后端" if backend else "当前后端"


def _detail(state, upstream, status_code, backend):
    """Audit-facing detail line; the user reads to_user_copy, not this."""
    name = _named(backend)
    label = OUTCOME_LABELS.get(upstream, upstream)
    if upstream == "MODEL_AUTH_FAILED":
        return (f"{name}认证被拒绝（HTTP {status_code}）：{AUTH_HINT}")
    if state == SUCCESS:
        return f"{name}已完成这一步（HTTP {status_code}）。"
    if state == TIMEOUT:
        return f"{name}请求超时，未得到结果（{label}）。"
    if state == BACKEND_UNAVAILABLE:
        return f"{name}暂时不可用（{label}，HTTP {status_code}）。"
    if state == EXECUTION_FAILED:
        return f"{name}在执行中失败（{label}）。"
    return f"{name}没有执行这一步（{label}，HTTP {status_code}）。"


def normalize_backend_response(status_code, body, backend=""):
    """Map one upstream HTTP response to a §15 state for *backend*.

    Runs the gateway's frozen classifier first (so both layers can never
    disagree about what the provider said), then translates the upstream
    outcome into the nine-state vocabulary. Returns an
    ``authority.ExecutionOutcome`` — a backend state, never a mission
    verdict: ``mission_failed`` is False for every state this function can
    produce, including PROVIDER_REFUSAL.
    """
    status = None if status_code is None else int(status_code)
    if status is None:
        # No HTTP response ever existed: gateway.complete's timeout/transport
        # paths return http_status None with the exception name in
        # body["error"]. classify_response only sees real responses, so this
        # half is handled here: timeout markers -> TIMEOUT, anything else is
        # an unreachable backend -> BACKEND_UNAVAILABLE. Neither is a refusal:
        # the provider never said a word.
        text = str(coerce_json_object(body).get("error", ""))
        timed_out = "timeout" in text.lower()
        state = TIMEOUT if timed_out else BACKEND_UNAVAILABLE
        upstream = "MODEL_TIMEOUT" if timed_out else "MODEL_TRANSPORT_FAILED"
        return ExecutionOutcome(state=state,
                                detail=_detail(state, upstream, None, backend))
    upstream = classify_response(status, coerce_json_object(body))
    if upstream == "MODEL_COMPLETED":
        state = SUCCESS
    elif upstream == "MODEL_TIMEOUT":
        # 504 and httpx timeouts. Timeout outranks the 5xx rule: "the wait
        # exceeded the limit" is more precise than "backend unavailable".
        state = TIMEOUT
    elif status is not None and status >= 500:
        # 500/501 and the 502/503/507 the gateway groups separately: the
        # backend answered but cannot serve this request right now.
        state = BACKEND_UNAVAILABLE
    elif upstream == "MODEL_TRANSPORT_FAILED":
        state = BACKEND_UNAVAILABLE
    elif upstream == "MODEL_TOOL_FAILED":
        # The run started and a tool call inside it failed.
        state = EXECUTION_FAILED
    else:
        # MODEL_REFUSED / MODEL_RATE_LIMITED (429) / MODEL_AUTH_FAILED
        # (401/403) / MODEL_FAILED (other 4xx, error bodies): the provider
        # was reached and declined to execute. THIS BACKEND DID NOT EXECUTE.
        state = PROVIDER_REFUSAL
    return ExecutionOutcome(state=state,
                            detail=_detail(state, upstream, status, backend))


def normalize_local_decision(reason, backend=""):
    """Map a local authority/gate decision to a §15 state.

    A local deny is the owner's platform saying no before anything runs; it
    must never be reported as a provider refusal (§14: the provider said
    nothing) and never as MISSION_FAILED. Grant-shaped reasons (required,
    expired, exhausted) are all GRANT_REQUIRED; any other local refusal is
    FACAI_DENIED.
    """
    if reason in _GRANT_REASONS:
        state = GRANT_REQUIRED
        detail = f"{_named(backend)}缺少有效授权（{reason}），未执行。"
    else:
        state = FACAI_DENIED
        detail = f"平台拒绝了这一步（{reason}），未调用 {_named(backend)}。"
    return ExecutionOutcome(state=state, detail=detail)


def outcome_from_adapter_error(exc, backend=""):
    """Classify an adapter exception raised before a terminal result exists.

    - timeout (subprocess or asyncio flavour)          -> TIMEOUT
    - "not installed" / capability complaints          -> CAPABILITY_MISSING
    - missing files / resources                        -> RESOURCE_UNAVAILABLE
    - anything else                                    -> EXECUTION_FAILED
      (the honest default: something in the backend's execution went wrong;
      inventing a refusal here would blame the provider without evidence)
    """
    if isinstance(exc, (subprocess.TimeoutExpired, TimeoutError)):
        state = TIMEOUT
        detail = f"{_named(backend)}执行等待超时。"
    else:
        text = str(exc).lower()
        if "not installed" in text or "capability" in text or "能力" in str(exc):
            state = CAPABILITY_MISSING
            detail = f"{_named(backend)}缺少所需执行能力：{exc}"
        elif isinstance(exc, FileNotFoundError) or "no such file" in text \
                or "resource" in text or "资源" in str(exc):
            state = RESOURCE_UNAVAILABLE
            detail = f"{_named(backend)}所需资源不可用：{exc}"
        else:
            state = EXECUTION_FAILED
            detail = f"{_named(backend)}执行失败：{type(exc).__name__}: {exc}"
    return ExecutionOutcome(state=state, detail=detail)
