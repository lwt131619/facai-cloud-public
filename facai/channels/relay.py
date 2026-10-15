"""Generic HTTP relay channel for external bridge clients (AutoClaw).

AutoClaw runs on the operator's own device and drives a personal WhatsApp
account. That WhatsApp usage is an UNOFFICIAL integration the operator opts
into at their own risk; Facai Cloud itself never touches WhatsApp. Facai only
receives what AutoClaw forwards over HTTP and queues replies for AutoClaw to
carry back:

* **Inbound.** AutoClaw POSTs a JSON object ``{external_id, sender, text}``
  to ``/api/v1/channels/relay/webhook``. The request must carry the same
  HMAC-SHA256 signature the n8n integration uses: ``X-Facai-Signature:
  sha256=<hex>`` over ``<timestamp>.<body>`` with ``X-Facai-Timestamp``
  inside a bounded skew window. Messages then flow through the exact same
  pipeline as every other dispatching channel (idempotent dedupe by
  ``external_id``, verbatim original text, conversion lease, mission
  creation, full-DAG dispatch).
* **Outbound.** Every reply (mission acknowledgement and progress lines) is
  recorded in the durable ``channel_messages`` outbox (direction OUTBOUND,
  status QUEUED) -- nothing is ever pushed to WhatsApp directly. AutoClaw
  polls ``POST /api/v1/channels/relay/send`` (same signature scheme) to
  fetch pending messages and acknowledges each id once delivered; acked
  ids are marked SENT and never returned again (at-least-once semantics).

The shared secret lives in the process environment only (on the server,
``/etc/facai-cloud/providers/relay.env``). A missing secret reports
``UNCONFIGURED``: the webhook refuses with 503 and the outbox stays empty.
No secret value is ever printed, returned or written to the database.
"""

import time
from collections.abc import Mapping

from .base import (
    ChannelProvider,
    InboundMessage,
    utcnow,
)

SIGNATURE_HEADER = "X-Facai-Signature"
TIMESTAMP_HEADER = "X-Facai-Timestamp"
DEFAULT_MAX_SKEW_SECONDS = 300


class RelayProvider(ChannelProvider):
    """Bridge-client relay: signed HTTP forwarding in, durable outbox out."""

    name = "relay"
    implemented = True
    required_env = ("RELAY_SHARED_SECRET",)
    # send() itself durably records the outbound row (the outbox IS the audit
    # trail); notifiers must not write a second record for the same delivery,
    # or every queued reply is duplicated in the poll response.
    records_outbound_itself = True

    def __init__(self, env=None):
        super().__init__(env)
        self._outbox = None

    @property
    def max_skew_seconds(self):
        try:
            return max(5, min(3600, int(self.setting("RELAY_MAX_SKEW") or DEFAULT_MAX_SKEW_SECONDS)))
        except (TypeError, ValueError):
            return DEFAULT_MAX_SKEW_SECONDS

    def bind_outbox(self, outbox):
        """Wire the durable outbox sink; called once by the app factory.

        ``outbox(reply: OutboundReply) -> message_id`` must persist the reply
        and return its id. Without a bound outbox the provider honestly
        refuses to queue anything instead of silently dropping replies.
        """
        self._outbox = outbox

    # --- signatures (the n8n scheme: sha256 hex over "<timestamp>.<body>") --

    def sign(self, body: bytes, timestamp) -> str:
        from .base import hmac_sha256_hex

        message = str(timestamp).encode("ascii") + b"." + body
        return hmac_sha256_hex(self.setting("RELAY_SHARED_SECRET"), message)

    def verify_signature(self, body: bytes, headers: Mapping, now=None) -> bool:
        secret = self.setting("RELAY_SHARED_SECRET")
        if not secret:
            return False
        supplied = next((value for key, value in headers.items()
                         if key.lower() == SIGNATURE_HEADER.lower()), "")
        timestamp = next((value for key, value in headers.items()
                          if key.lower() == TIMESTAMP_HEADER.lower()), "")
        if not supplied or not timestamp:
            return False
        try:
            timestamp.encode("ascii")
            stamped_at = int(timestamp)
        except (AttributeError, TypeError, ValueError):
            return False
        reference = time.time() if now is None else now
        if abs(reference - stamped_at) > self.max_skew_seconds:
            return False
        try:
            prefix, _, digest = str(supplied).partition("=")
            if prefix != "sha256" or len(digest) != 64:
                return False
            from .base import secure_equals

            return secure_equals(self.sign(body, timestamp), digest)
        except (TypeError, ValueError):
            return False

    # --- parsing and sending ------------------------------------------------

    def parse_inbound(self, payload: Mapping) -> list:
        """Normalize the relay payload; the text is kept verbatim."""
        if not isinstance(payload, Mapping):
            raise ValueError("relay payload must be a JSON object")
        external_id = str(payload.get("external_id") or "").strip()
        sender = str(payload.get("sender") or "").strip()
        text = payload.get("text")
        if not external_id or not sender or not isinstance(text, str) or not text.strip():
            raise ValueError("relay payload requires external_id, sender and text")
        return [InboundMessage(
            channel=self.name,
            external_id=external_id,
            sender=sender,
            recipient=str(payload.get("recipient") or "").strip() or None,
            kind=str(payload.get("kind") or "text").strip() or "text",
            text=text,
            received_at=utcnow(),
            raw=dict(payload) if isinstance(payload, dict) else {},
        )]

    async def send(self, reply, client=None) -> dict:
        """Queue one reply in the durable outbox for AutoClaw to fetch."""
        self.require_configured()
        if self._outbox is None:
            raise RuntimeError("relay outbox is not wired; cannot queue the reply")
        message_id = self._outbox(reply)
        if not message_id:
            raise RuntimeError("relay outbox refused the reply")
        return {"status": "QUEUED", "external_id": message_id,
                "detail": "已进入 relay outbox，等待 AutoClaw 拉取投递"}
