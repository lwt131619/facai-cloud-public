"""Slack channel adapter: native v0 HMAC webhook verification + bot push.

Slack signs every event delivery natively: ``X-Slack-Signature`` is
``v0=<hex hmac-sha256>`` over the base string ``v0:<timestamp>:<raw body>``
keyed with the app's signing secret, and ``X-Slack-Request-Timestamp`` guards
against replay (deliveries older than five minutes are refused — a clock
skew larger than that means the requester is misconfigured, not early).
Verification fails closed on missing credentials, headers or an unconfigured
channel.

Outbound pushes with the bot token via ``chat.postMessage``. ``send`` reports
``DELIVERED`` only for a 2xx whose body carries ``ok: true`` and a concrete
message ``ts``; Slack answers auth failures with ``ok: false`` + an error code
on a 200, so the reason mapping reads the body, not just the status code.
Reason codes use the same non-secret vocabulary as the LINE adapter.
"""

import math
import time

import httpx

from .base import ChannelProvider, InboundMessage, hmac_sha256_hex, secure_equals, utcnow

# chat.postMessage's text field limit. Beyond it the push is refused honestly
# (TEXT_TOO_LONG) instead of silently truncated or split.
MAX_MESSAGE_CHARS = 40000

POST_URL = "https://slack.com/api/chat.postMessage"

# Slack's documented replay window for the timestamp header (seconds).
MAX_CLOCK_SKEW = 300


class SlackProvider(ChannelProvider):
    name = "slack"
    implemented = True
    required_env = ("SLACK_BOT_TOKEN", "SLACK_SIGNING_SECRET")

    # --- webhook verification ---------------------------------------------

    def verify_signature(self, body, headers, now=None):
        """Verify Slack's ``v0`` HMAC signature in constant time, refusing
        deliveries whose timestamp header drifted beyond the replay window."""
        secret = self.setting("SLACK_SIGNING_SECRET")
        signature = next((v for k, v in headers.items()
                          if k.lower() == "x-slack-signature"), "")
        timestamp = next((v for k, v in headers.items()
                          if k.lower() == "x-slack-request-timestamp"), "")
        if not secret or not signature or not self.configured():
            return False
        try:
            age = (now if now is not None else time.time()) - int(timestamp)
        except ValueError:
            return False
        if not (math.isfinite(age) and abs(age) <= MAX_CLOCK_SKEW):
            return False
        base = f"v0:{timestamp}:".encode("utf-8") + body
        expected = "v0=" + hmac_sha256_hex(secret, base)
        return secure_equals(expected, signature)

    # --- inbound -----------------------------------------------------------

    def parse_inbound(self, payload):
        """Normalize a Slack ``event_callback`` into ``InboundMessage`` objects.

        Only plain text messages from humans become messages: bot-authored
        deliveries (including our own pushes, which arrive as echoes) and any
        ``subtype`` event (edits, deletes, joins, uploads) are skipped, and a
        message without a stable identity — ``ts``, ``user``, ``channel`` or
        non-empty text — is dropped rather than half-parsed. Malformed
        payloads yield an empty list, never an exception: a webhook delivery
        must not 400 the provider for one bad event.
        """
        messages = []
        if not isinstance(payload, dict) or payload.get("type") != "event_callback":
            return messages
        event = payload.get("event")
        if not isinstance(event, dict) or event.get("type") != "message":
            return messages
        if event.get("bot_id") or event.get("subtype"):
            return messages
        text = event.get("text")
        external_id = event.get("ts")
        sender = event.get("user")
        recipient = event.get("channel")
        if (not isinstance(text, str) or not text
                or not isinstance(external_id, str) or not external_id.strip()
                or not isinstance(sender, str) or not sender.strip()
                or not isinstance(recipient, str) or not recipient.strip()):
            return messages
        team_id = payload.get("team_id")
        messages.append(InboundMessage(
            channel=self.name,
            external_id=external_id,
            sender=sender,
            recipient=recipient,
            text=text,
            kind="text",
            received_at=utcnow(),
            raw=event,
            metadata={"team_id": team_id if isinstance(team_id, str) else None},
        ))
        return messages

    # --- outbound ----------------------------------------------------------

    async def send(self, reply, client=None):
        self.require_configured()  # Raises ChannelConfigError when unconfigured.
        if reply.channel != self.name or not reply.recipient.strip():
            raise ValueError("A Slack reply needs a recipient and matching channel")
        if not reply.text:
            raise ValueError("Slack text must not be empty")
        result = {"channel": self.name, "status": "FAILED", "http_status": None,
                  "external_id": None, "error": None}
        if len(reply.text) > MAX_MESSAGE_CHARS:
            # Honest refusal: a truncated or split push would not be what the
            # operator asked to send.
            return {**result, "error": "TEXT_TOO_LONG"}
        headers = {"Authorization": f"Bearer {self.setting('SLACK_BOT_TOKEN')}",
                   "Content-Type": "application/json"}
        payload = {"channel": reply.recipient, "text": reply.text}
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(POST_URL, json=payload, headers=headers)
            result["http_status"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            if not isinstance(body, dict):
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            # A push is only ever reported DELIVERED when the API confirms
            # ok:true with a concrete message ts for a 2xx response -- never
            # fabricated from a bare status code.
            if (200 <= response.status_code < 300 and body.get("ok") is True
                    and isinstance(body.get("ts"), str) and body["ts"]):
                return {**result, "status": "DELIVERED", "external_id": body["ts"]}
            upstream_error = body.get("error")
            if response.status_code == 429 or upstream_error == "rate_limited":
                return {**result, "error": "RATE_LIMITED"}
            if (response.status_code == 401
                    or upstream_error in ("invalid_auth", "account_inactive")):
                return {**result, "error": "AUTH_FAILED"}
            return {**result, "error": "UPSTREAM_REJECTED"}
        except httpx.TimeoutException:
            return {**result, "error": "TIMEOUT"}
        except httpx.RequestError:
            return {**result, "error": "TRANSPORT_ERROR"}
        finally:
            if own_client:
                await client.aclose()
