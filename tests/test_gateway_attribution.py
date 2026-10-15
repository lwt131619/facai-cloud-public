"""The gateway must attribute every upstream outcome precisely and transparently."""

import asyncio

import httpx
import pytest

from facai.gateway import OUTCOME_LABELS, classify_response, complete


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def run(handler, model=None):
    async def go():
        async with _client(handler) as client:
            return await complete(model or {"endpoint": "https://upstream.test/v1",
                                             "model": "sample"}, [{"role": "user", "content": "hi"}], client)
    return asyncio.run(go())


def test_completed_is_attributed_to_upstream_model():
    outcome = run(lambda request: httpx.Response(
        200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}))
    assert outcome["status"] == "MODEL_COMPLETED"
    assert outcome["content"] == "ok"
    assert outcome["upstream"] is None
    assert outcome["http_status"] == 200


def test_refusal_keeps_upstream_body():
    outcome = run(lambda request: httpx.Response(200, json={
        "choices": [{"message": {"refusal": "declined"}, "finish_reason": "refusal"}]}))
    assert outcome["status"] == "MODEL_REFUSED"
    assert outcome["upstream"]["choices"][0]["message"]["refusal"] == "declined"


def test_timeout_exception_is_a_transport_timeout():
    def handler(request):
        raise httpx.ConnectTimeout("timed out")
    outcome = run(handler)
    assert outcome["status"] == "MODEL_TIMEOUT"
    assert outcome["http_status"] is None


def test_gateway_timeout_status_is_reported_as_timeout():
    outcome = run(lambda request: httpx.Response(504, json={"error": {"message": "gateway timeout"}}))
    assert outcome["status"] == "MODEL_TIMEOUT"


def test_rate_limit_is_distinct_from_generic_failure():
    outcome = run(lambda request: httpx.Response(429, json={"error": {"type": "rate_limit"}}))
    assert outcome["status"] == "MODEL_RATE_LIMITED"
    assert outcome["http_status"] == 429


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failure_is_attributed(status):
    outcome = run(lambda request: httpx.Response(status, json={"error": {"type": "auth"}}))
    assert outcome["status"] == "MODEL_AUTH_FAILED"


def test_transport_error_is_not_a_model_failure():
    def handler(request):
        raise httpx.ConnectError("connection refused")
    outcome = run(handler)
    assert outcome["status"] == "MODEL_TRANSPORT_FAILED"
    assert outcome["upstream"]["error"] == "ConnectError"


def test_tool_failure_is_attributed_from_error_body():
    outcome = run(lambda request: httpx.Response(400, json={
        "error": {"type": "tool_error", "message": "tool search crashed"}}))
    assert outcome["status"] == "MODEL_TOOL_FAILED"


def test_tool_failure_is_attributed_from_finish_reason():
    outcome = run(lambda request: httpx.Response(200, json={
        "choices": [{"message": {}, "finish_reason": "tool_error"}]}))
    assert outcome["status"] == "MODEL_TOOL_FAILED"


@pytest.mark.parametrize("status", [502, 503, 507])
def test_resource_unavailability_is_distinct(status):
    outcome = run(lambda request: httpx.Response(status, json={"error": {"message": "overloaded"}}))
    assert outcome["status"] == "MODEL_RESOURCE_UNAVAILABLE"


def test_other_error_status_is_generic_upstream_failure():
    outcome = run(lambda request: httpx.Response(500, json={"error": {"message": "boom"}}))
    assert outcome["status"] == "MODEL_FAILED"


def test_classify_precedence_and_labels_are_complete():
    assert classify_response(401, {}) == "MODEL_AUTH_FAILED"
    assert classify_response(429, {}) == "MODEL_RATE_LIMITED"
    assert classify_response(503, {"error": {"message": "tool down"}}) == "MODEL_RESOURCE_UNAVAILABLE"
    assert classify_response(400, {"error": {"message": "tool down"}}) == "MODEL_TOOL_FAILED"
    for status in ("MODEL_COMPLETED", "MODEL_REFUSED", "MODEL_TIMEOUT", "MODEL_RATE_LIMITED",
                   "MODEL_AUTH_FAILED", "MODEL_TRANSPORT_FAILED", "MODEL_TOOL_FAILED",
                   "MODEL_RESOURCE_UNAVAILABLE", "MODEL_FAILED"):
        assert status in OUTCOME_LABELS
