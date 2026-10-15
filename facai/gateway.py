"""Model request boundary and honest attribution of upstream outcomes.

Every terminal result is attributed to the upstream component that produced it.
Facai never rewrites an upstream refusal, timeout, rate limit, authentication,
transport, tool or resource failure as its own decision, and a model response is
never treated as mission success.
"""

import httpx
import os

# The full set of upstream outcomes the gateway can report. Keeping the labels
# explicit lets the console present the precise cause instead of a generic error.
OUTCOMES = (
    "MODEL_COMPLETED",
    "MODEL_REFUSED",
    "MODEL_TIMEOUT",
    "MODEL_RATE_LIMITED",
    "MODEL_AUTH_FAILED",
    "MODEL_TRANSPORT_FAILED",
    "MODEL_TOOL_FAILED",
    "MODEL_RESOURCE_UNAVAILABLE",
    "MODEL_FAILED",
)

# Chinese labels are shown next to the raw status so the owner reads the cause.
OUTCOME_LABELS = {
    "MODEL_COMPLETED": "上游模型已完成",
    "MODEL_REFUSED": "上游模型拒绝回答",
    "MODEL_TIMEOUT": "上游请求超时",
    "MODEL_RATE_LIMITED": "上游限流",
    "MODEL_AUTH_FAILED": "上游认证失败",
    "MODEL_TRANSPORT_FAILED": "网络传输失败",
    "MODEL_TOOL_FAILED": "上游工具调用失败",
    "MODEL_RESOURCE_UNAVAILABLE": "上游资源不可用",
    "MODEL_FAILED": "上游返回错误",
}


def error_text(body):
    """Flatten an upstream error object into lowercase searchable text."""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        return " ".join(str(error[key]) for key in ("type", "code", "message")
                         if error.get(key)).lower()
    if error:
        return str(error).lower()
    return ""


def coerce_json_object(body):
    """Normalize any non-object JSON body into an object.

    Some upstreams return a bare JSON array on failure -- for example Gemini's
    OpenAI-compatible endpoint answers ``[{"error": {...}}]`` instead of an
    object. Treating the parsed body as a dict unconditionally would crash
    classification with ``AttributeError`` and turn an upstream 404 into a
    Facai 500, so the first object element is used and everything else becomes
    an empty object.
    """
    if isinstance(body, dict):
        return body
    if isinstance(body, list):
        for item in body:
            if isinstance(item, dict):
                return item
        return {}
    return {}


def classify_response(status, body):
    """Map an HTTP response to exactly one upstream outcome."""
    if status in (401, 403):
        return "MODEL_AUTH_FAILED"
    if status == 429:
        return "MODEL_RATE_LIMITED"
    if status == 504:
        return "MODEL_TIMEOUT"
    if status in (502, 503, 507):
        return "MODEL_RESOURCE_UNAVAILABLE"
    body = body or {}
    text = error_text(body)
    if "tool" in text:
        return "MODEL_TOOL_FAILED"
    if status >= 400:
        return "MODEL_FAILED"
    choices = body.get("choices") or []
    first = choices[0] if choices else {}
    message = first.get("message") or {}
    finish_reason = first.get("finish_reason")
    if message.get("refusal") or finish_reason in {"content_filter", "refusal"}:
        return "MODEL_REFUSED"
    if finish_reason == "tool_error" or first.get("tool_error") or message.get("tool_error"):
        return "MODEL_TOOL_FAILED"
    if body.get("type") == "error":
        return "MODEL_FAILED"
    if body.get("stop_reason") == "refusal" or any(
            isinstance(block, dict) and block.get("type") == "refusal"
            for block in body.get("content", [])):
        return "MODEL_REFUSED"
    if body.get("stop_reason") == "tool_error":
        return "MODEL_TOOL_FAILED"
    return "MODEL_COMPLETED"


def _is_anthropic(model):
    identity = " ".join(str(model.get(key, "")) for key in
                         ("provider", "provider_id", "id")).lower()
    return "anthropic" in identity or "claude" in identity


def _anthropic_request(endpoint, messages, model_name, max_tokens=8192):
    """Build the Messages API URL and body from Facai's role/content messages."""
    if endpoint.endswith("/v1/messages"):
        url = endpoint
    elif endpoint.endswith("/v1"):
        url = f"{endpoint}/messages"
    else:
        url = f"{endpoint}/v1/messages"
    system = []
    conversation = []
    for message in messages:
        role, content = message.get("role"), message.get("content", "")
        if role == "system":
            if isinstance(content, str):
                system.append(content)
            elif isinstance(content, list):
                system.extend(block.get("text", "") for block in content
                              if isinstance(block, dict) and block.get("type") == "text")
        else:
            conversation.append({"role": role, "content": content})
    try:
        max_tokens = int(max_tokens)
    except (TypeError, ValueError):
        max_tokens = 8192
    payload = {"model": model_name, "messages": conversation,
               "max_tokens": max(1, min(max_tokens, 32768))}
    if system:
        payload["system"] = "\n\n".join(system)
    return url, payload


def _anthropic_content(body):
    blocks = body.get("content") or []
    parts = [block.get("text", "") for block in blocks
             if isinstance(block, dict) and block.get("type") == "text"]
    return "".join(parts) if parts else None


def _anthropic_usage(body):
    usage = body.get("usage")
    if not isinstance(usage, dict):
        return usage
    return {"input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens")}


def _result(status, http_status, content=None, upstream=None, reason=None, usage=None):
    return {"status": status, "label": OUTCOME_LABELS.get(status),
            "http_status": http_status, "content": content,
            "upstream": upstream, "reason": reason, "usage": usage}


async def complete(model, messages, client=None, timeout=30):
    endpoint = model.get("endpoint", "").rstrip("/")
    if not endpoint.startswith(("http://", "https://")):
        raise ValueError("Model endpoint must be HTTP(S)")
    anthropic = _is_anthropic(model)
    headers = {}
    api_key = model.get("api_key") or os.getenv(model.get("api_key_env", ""))
    if anthropic:
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        url, payload = _anthropic_request(endpoint, messages, model["model"],
                                          model.get("max_tokens", 8192))
    else:
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url, payload = f"{endpoint}/chat/completions", {"model": model["model"], "messages": messages}
    own_client = client is None
    if own_client:
        try:
            timeout = float(timeout)
        except (TypeError, ValueError):
            timeout = 30
        client = httpx.AsyncClient(timeout=max(1.0, timeout))
    try:
        try:
            response = await client.post(url, json=payload, headers=headers)
            try:
                body = coerce_json_object(response.json())
            except ValueError:
                body = {}
            status = classify_response(response.status_code, body)
            if anthropic:
                content = _anthropic_content(body)
                if body.get("stop_reason") == "refusal":
                    status = "MODEL_REFUSED"
            else:
                content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
            failed = status != "MODEL_COMPLETED"
            usage = (_anthropic_usage(body) if anthropic else body.get("usage")) if isinstance(body, dict) else None
            return _result(status, response.status_code, content,
                           upstream=body if failed else None,
                           reason=(error_text(body) or status) if failed else None,
                           usage=usage)
        except httpx.TimeoutException as exc:
            return _result("MODEL_TIMEOUT", None, upstream={"error": type(exc).__name__},
                           reason="上游请求超时")
        except httpx.RequestError as exc:
            # Connect/read/proxy errors that are not timeouts are transport failures,
            # not model refusals and not Facai failures.
            return _result("MODEL_TRANSPORT_FAILED", None, upstream={"error": type(exc).__name__},
                           reason="网络传输失败")
    finally:
        if own_client:
            await client.aclose()
