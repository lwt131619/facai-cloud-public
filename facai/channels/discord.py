"""Discord channel adapter: relay-bridge HMAC inbound + bot-token push.

Native Discord "Interactions" endpoints are signed with Ed25519, which would
drag a native crypto dependency into the core for a station-side feature.
FACAI instead accepts deliveries from a customer-run bridge (the proven
whatsapp-relay shape): the bridge forwards Discord message events as JSON and
signs the raw body with a shared secret — hex HMAC-SHA256 in the
``X-Facai-Signature`` header. The trust model is identical to the relay
channel: the secret lives in the classified provider env file only, and an
unsigned, wrongly signed or unconfigured delivery fails closed before parsing.

Outbound pushes with the bot token via ``POST /channels/{id}/messages``.
``send`` reports ``DELIVERED`` only for a 2xx whose body echoes a concrete
message id; every other outcome carries a stable, non-secret reason code using
the same vocabulary as the LINE adapter, so Mission Control can tell an auth
problem from rate limiting from a transient error.
"""

import httpx

from .base import ChannelProvider, InboundMessage, hmac_sha256_hex, secure_equals, utcnow

# Discord hard limit per text message. Beyond it the push is refused honestly
# (TEXT_TOO_LONG) instead of silently truncated or split.
MAX_MESSAGE_CHARS = 2000


class DiscordProvider(ChannelProvider):
    name = "discord"
    implemented = True
    required_env = ("DISCORD_BOT_TOKEN", "DISCORD_WEBHOOK_SECRET")

    # --- webhook verification ---------------------------------------------

    def verify_signature(self, body, headers):
        """Compare the ``X-Facai-Signature`` header (hex HMAC-SHA256 of the raw
        body with the bridge shared secret) in constant time. Missing secret,
        missing header or an unconfigured channel fails closed."""
        secret = self.setting("DISCORD_WEBHOOK_SECRET")
        supplied = next((v for k, v in headers.items()
                         if k.lower() == "x-facai-signature"), "")
        if not secret or not supplied or not self.configured():
            return False
        return secure_equals(hmac_sha256_hex(secret, body), supplied)

    # --- inbound -----------------------------------------------------------

    def parse_inbound(self, payload):
        """Normalize a bridge-forwarded Discord payload into ``InboundMessage``
        objects.

        The bridge posts ``{"messages": [ ... Discord message objects ... ]}``.
        Messages authored by bots are skipped (never self-reply loops), as is
        anything without a stable identity: message id, author id, channel id
        and non-empty text are all required for the dedupe/attribution
        pipeline. Malformed entries are skipped rather than rejected — one bad
        object must not 400 the whole delivery.
        """
        messages = []
        if not isinstance(payload, dict):
            return messages
        entries = payload.get("messages")
        if not isinstance(entries, list):
            return messages
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            author = entry.get("author")
            if not isinstance(author, dict) or author.get("bot") is True:
                continue
            text = entry.get("content")
            external_id = entry.get("id")
            recipient = entry.get("channel_id")
            sender = author.get("id")
            if (not isinstance(text, str) or not text
                    or not isinstance(external_id, str) or not external_id.strip()
                    or not isinstance(recipient, str) or not recipient.strip()
                    or not isinstance(sender, str) or not sender.strip()):
                continue
            guild_id = entry.get("guild_id")
            messages.append(InboundMessage(
                channel=self.name,
                external_id=external_id,
                sender=sender,
                recipient=recipient,
                text=text,
                kind="text",
                received_at=self._timestamp(entry.get("timestamp")),
                raw=entry,
                metadata={"guild_id": guild_id if isinstance(guild_id, str) else None},
            ))
        return messages

    @staticmethod
    def _timestamp(value):
        # The bridge forwards Discord's ISO-8601 timestamp when it has one.
        if isinstance(value, str) and value.strip():
            return value
        return datetime.now(timezone.utc).isoformat()

    # --- outbound ----------------------------------------------------------

    async def send(self, reply, client=None):
        self.require_configured()  # Raises ChannelConfigError when unconfigured.
        if reply.channel != self.name or not reply.recipient.strip():
            raise ValueError("A Discord reply needs a recipient and matching channel")
        if not reply.text:
            raise ValueError("Discord text must not be empty")
        result = {"channel": self.name, "status": "FAILED", "http_status": None,
                  "external_id": None, "error": None}
        if len(reply.text) > MAX_MESSAGE_CHARS:
            # Honest refusal: a truncated or split push would not be what the
            # operator asked to send.
            return {**result, "error": "TEXT_TOO_LONG"}
        headers = {"Authorization": f"Bot {self.setting('DISCORD_BOT_TOKEN')}",
                   "Content-Type": "application/json"}
        url = f"https://discord.com/api/v10/channels/{reply.recipient}/messages"
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=30)
        try:
            response = await client.post(url, json={"content": reply.text},
                                         headers=headers)
            result["http_status"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            if not isinstance(body, dict):
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            # A push is only ever reported DELIVERED when the API echoes back a
            # concrete message id for a 2xx response -- never fabricated from a
            # bare status code.
            if (200 <= response.status_code < 300
                    and isinstance(body.get("id"), str) and body["id"]):
                return {**result, "status": "DELIVERED", "external_id": body["id"]}
            reason = {401: "AUTH_FAILED", 403: "AUTH_FAILED",
                      429: "RATE_LIMITED"}.get(response.status_code,
                                               "UPSTREAM_REJECTED")
            return {**result, "error": reason}
        except httpx.TimeoutException:
            return {**result, "error": "TIMEOUT"}
        except httpx.RequestError:
            return {**result, "error": "TRANSPORT_ERROR"}
        finally:
            if own_client:
                await client.aclose()
