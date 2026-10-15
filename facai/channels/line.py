"""LINE Messaging API channel adapter; honest about credentials and outcomes.

Structure mirrors the Telegram/WhatsApp adapters: an inbound webhook parser, a
signature check and an outbound push sender. Credentials are read from the
process environment only (operator supplies them via the classified provider
env file); with any of them missing the adapter reports ``UNCONFIGURED`` and
refuses to send -- it never fabricates a live connection and never exposes a
secret value.

``send`` reports ``DELIVERED`` only when the push API answers a 2xx with a
concrete ``sentMessages[0].id``; every other outcome (upstream rejection,
malformed body, timeout, transport failure) carries a stable, non-secret
reason code using the same vocabulary as the Telegram adapter, so Mission
Control can tell an auth problem from rate limiting from a transient error.
"""

import base64
import hashlib
import hmac
from datetime import datetime, timezone

import httpx

from .base import ChannelProvider, InboundMessage, secure_equals, utcnow

# LINE Messaging API hard limit per text message. Beyond it the push is
# refused honestly (TEXT_TOO_LONG) instead of silently truncated or split.
MAX_MESSAGE_CHARS = 5000

PUSH_URL = "https://api.line.me/v2/bot/message/push"


class LineProvider(ChannelProvider):
    name = "line"
    implemented = True
    required_env = ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET")

    # --- webhook verification ---------------------------------------------

    def verify_signature(self, body, headers):
        """Compare the ``X-Line-Signature`` header (base64 HMAC-SHA256 of the
        raw body with the channel secret) in constant time. Missing secret,
        missing header or an unconfigured channel fails closed."""
        secret = self.setting("LINE_CHANNEL_SECRET")
        supplied = next((v for k, v in headers.items()
                         if k.lower() == "x-line-signature"), "")
        if not secret or not supplied or not self.configured():
            return False
        expected = base64.b64encode(
            hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode()
        return secure_equals(expected, supplied)

    # --- inbound -----------------------------------------------------------

    def parse_inbound(self, payload):
        """Normalize a LINE webhook payload into ``InboundMessage`` objects.

        Only ``message`` events whose ``message.type`` is ``text`` become
        messages; follow/unfollow/postback/video events and media messages are
        ignored (never silently turned into text). Malformed entries are
        skipped rather than rejected: a webhook delivery must not 400 the
        provider for one bad event in an otherwise valid payload.
        """
        messages = []
        if not isinstance(payload, dict):
            return messages
        events = payload.get("events")
        if not isinstance(events, list):
            return messages
        destination = payload.get("destination")
        recipient = destination if isinstance(destination, str) and destination else None
        for event in events:
            if not isinstance(event, dict) or event.get("type") != "message":
                continue
            message = event.get("message")
            if not isinstance(message, dict) or message.get("type") != "text":
                continue
            text = message.get("text")
            external_id = message.get("id")
            source = event.get("source")
            sender = source.get("userId") if isinstance(source, dict) else None
            # Stable identity (message id + sender) is required for the
            # dedupe/attribution pipeline; an event without either is skipped.
            if (not isinstance(text, str) or not text
                    or not isinstance(external_id, str) or not external_id.strip()
                    or not isinstance(sender, str) or not sender.strip()):
                continue
            webhook_event_id = event.get("webhookEventId")
            messages.append(InboundMessage(
                channel=self.name,
                external_id=external_id,
                sender=sender,
                recipient=recipient,
                text=text,
                kind="text",
                received_at=self._timestamp(event.get("timestamp")),
                raw=message,
                metadata={
                    "source_type": source.get("type") if isinstance(source, dict) else None,
                    "webhook_event_id": webhook_event_id
                    if isinstance(webhook_event_id, str) else None,
                },
            ))
        return messages

    @staticmethod
    def _timestamp(value):
        # LINE timestamps are milliseconds since the epoch.
        try:
            return datetime.fromtimestamp(int(value) / 1000, timezone.utc).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            return utcnow()

    # --- outbound ----------------------------------------------------------

    async def send(self, reply, client=None):
        self.require_configured()  # Raises ChannelConfigError when unconfigured.
        if reply.channel != self.name or not reply.recipient.strip():
            raise ValueError("A LINE reply needs a recipient and matching channel")
        if not reply.text:
            raise ValueError("LINE text must not be empty")
        result = {"channel": self.name, "status": "FAILED", "http_status": None,
                  "external_id": None, "error": None}
        if len(reply.text) > MAX_MESSAGE_CHARS:
            # Honest refusal: a truncated or split push would not be what the
            # operator asked to send.
            return {**result, "error": "TEXT_TOO_LONG"}
        headers = {
            "Authorization": f"Bearer {self.setting('LINE_CHANNEL_ACCESS_TOKEN')}",
            "Content-Type": "application/json",
        }
        payload = {"to": reply.recipient,
                   "messages": [{"type": "text", "text": reply.text}]}
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(PUSH_URL, json=payload, headers=headers)
            result["http_status"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            if not isinstance(body, dict):
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            sent = body.get("sentMessages")
            message = sent[0] if isinstance(sent, list) and sent else None
            # A push is only ever reported DELIVERED when the API echoes back a
            # concrete message id for a 2xx response -- never fabricated from a
            # bare status code.
            if (200 <= response.status_code < 300 and isinstance(message, dict)
                    and isinstance(message.get("id"), str) and message["id"]):
                return {**result, "status": "DELIVERED", "external_id": message["id"]}
            reason = {401: "AUTH_FAILED", 429: "RATE_LIMITED"}.get(
                response.status_code, "UPSTREAM_REJECTED")
            return {**result, "error": reason}
        except httpx.TimeoutException:
            return {**result, "error": "TIMEOUT"}
        except httpx.RequestError:
            return {**result, "error": "TRANSPORT_ERROR"}
        finally:
            if own_client:
                await client.aclose()
