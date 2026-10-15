"""Telegram Bot API text messaging; configuration is not proof of connectivity."""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx

MAX_MESSAGE_CHARS = 4096  # Bot API sendMessage hard limit per text message.
MAX_CHUNKS = 10  # Cap on sequential chunks; beyond this we refuse honestly.
RATE_LIMIT_WAIT_CAP = 5  # Only sleep (once) when retry_after is at most this.

from .base import ChannelProvider, InboundMessage, secure_equals, utcnow


class _RedactBotURL(logging.Filter):
    def filter(self, record):
        # Telegram places the token in its URL. httpx logs request URLs at INFO.
        record.msg = re.sub(r"(api\.telegram\.org/bot)[^/\s\"']+", r"\1[REDACTED]",
                            record.getMessage())
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_RedactBotURL())


class TelegramProvider(ChannelProvider):
    name = "telegram"
    implemented = True
    required_env = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET")

    def missing(self):
        patterns = {"TELEGRAM_BOT_TOKEN": r"[0-9]+:[A-Za-z0-9_-]+",
                    "TELEGRAM_WEBHOOK_SECRET": r"[A-Za-z0-9_-]{1,256}"}
        return [name for name, pattern in patterns.items()
                if not re.fullmatch(pattern, self.setting(name))]

    def verify_signature(self, body, headers):
        expected = self.setting("TELEGRAM_WEBHOOK_SECRET")
        supplied = next((v for k, v in headers.items()
                         if k.lower() == "x-telegram-bot-api-secret-token"), "")
        return bool(expected and self.configured() and secure_equals(supplied, expected))

    def parse_inbound(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("Telegram update must be an object")
        update_id = payload.get("update_id")
        if type(update_id) is not int or update_id < 0:
            raise ValueError("Telegram update_id must be a non-negative integer")
        message = payload.get("message")
        if message is None:
            return []  # Callback queries, edits and service updates are not commands.
        if not isinstance(message, dict):
            raise ValueError("Telegram message must be an object")
        text = message.get("text")
        if text is None:
            return []  # Media is not silently turned into a text command.
        chat = message.get("chat")
        sender = message.get("from")
        message_id = message.get("message_id")
        if (not isinstance(text, str) or not isinstance(chat, dict)
                or type(chat.get("id")) is not int or not isinstance(sender, dict)
                or type(sender.get("id")) is not int or type(message_id) is not int):
            raise ValueError("Invalid Telegram text message")
        if sender.get("is_bot"):
            return []
        try:
            received_at = datetime.fromtimestamp(int(message.get("date")), timezone.utc).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            received_at = utcnow()
        return [InboundMessage(
            channel=self.name, external_id=f"update:{update_id}",
            sender=str(sender["id"]), recipient=str(chat["id"]), text=text,
            received_at=received_at, raw=message,
            metadata={"update_id": update_id, "message_id": message_id,
                      "chat_id": str(chat["id"]), "chat_type": chat.get("type")})]

    @staticmethod
    def _split_text(text, limit=MAX_MESSAGE_CHARS):
        """Split text into chunks of at most ``limit`` characters.

        Cut points prefer paragraph breaks ("\\n\\n"), then line breaks ("\\n"),
        then sentence ends (". "), then any whitespace; only when none of those
        exists within the window does the split fall back to a hard cut, so no
        chunk ever exceeds the Bot API limit.
        """
        chunks = []
        remaining = text
        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break
            window = remaining[:limit]
            cut = next_start = -1
            for separator in ("\n\n", "\n", ". ", " "):
                index = window.rfind(separator)
                if index > 0:
                    if separator == ". ":
                        cut, next_start = index + 1, index + 2  # Keep the period.
                    else:
                        cut, next_start = index, index + len(separator)
                    break
            if cut <= 0:
                cut = next_start = limit
            chunks.append(remaining[:cut])
            remaining = remaining[next_start:]
            if len(chunks) > MAX_CHUNKS:
                break  # Enough to know the cap is exceeded; stop working.
        return chunks

    async def _post_message(self, client, url, payload):
        """Send one chunk, retrying once on a 429 whose retry_after is at most 5s."""
        result = {"channel": self.name, "status": "FAILED", "http_status": None,
                  "external_id": None, "error": None}
        for attempt in range(2):
            try:
                response = await client.post(url, json=payload, follow_redirects=False)
            except httpx.TimeoutException:
                return {**result, "error": "TIMEOUT"}
            except httpx.RequestError:
                return {**result, "error": "TRANSPORT_ERROR"}
            result = {**result, "http_status": response.status_code}
            try:
                body = response.json()
            except ValueError:
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            if not isinstance(body, dict):
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            message = body.get("result")
            if (200 <= response.status_code < 300 and body.get("ok") is True
                    and isinstance(message, dict) and type(message.get("message_id")) is int):
                return {**result, "status": "SENT", "external_id": str(message["message_id"])}
            code = body.get("error_code", response.status_code)
            if type(code) is not int:
                code = response.status_code
            reason = {401: "AUTH_FAILED", 403: "RECIPIENT_FORBIDDEN", 429: "RATE_LIMITED"}.get(code, "UPSTREAM_REJECTED")
            result = {**result, "error": reason}
            params = body.get("parameters")
            retry = params.get("retry_after") if isinstance(params, dict) else None
            if type(retry) is int and retry > 0:
                result = {**result, "retry_after": retry}
            if (attempt == 0 and reason == "RATE_LIMITED"
                    and type(retry) is int and 0 < retry <= RATE_LIMIT_WAIT_CAP):
                await asyncio.sleep(retry)  # Short flood wait: retry the same chunk once.
                continue
            return result
        return result

    async def edit(self, chat_id, message_id, text, client=None):
        """Live-update an already-sent status message (progress without spam).

        Returns True only when Telegram confirms the edit. Failures are
        swallowed: a failed refresh must never break the notification flow.
        """
        url = f"https://api.telegram.org/bot{self.setting('TELEGRAM_BOT_TOKEN')}/editMessageText"
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=15, follow_redirects=False)
        try:
            response = await client.post(url, json={
                "chat_id": chat_id, "message_id": int(message_id), "text": text[:4096],
                "link_preview_options": {"is_disabled": True}})
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            return bool(isinstance(body, dict) and body.get("ok") is True)
        except (httpx.TimeoutException, httpx.RequestError, ValueError):
            return False
        finally:
            if own_client:
                await client.aclose()

    async def send(self, reply, client=None):
        self.require_configured()
        if reply.channel != self.name or not reply.recipient.strip():
            raise ValueError("A Telegram reply needs a recipient and matching channel")
        if not reply.text:
            raise ValueError("Telegram text must not be empty")
        if reply.reply_to is not None:
            if not re.fullmatch(r"[0-9]+", reply.reply_to) or int(reply.reply_to) <= 0:
                raise ValueError("Telegram reply_to must be a positive message ID")
        chunks = self._split_text(reply.text)
        if len(chunks) > MAX_CHUNKS:
            # Honest refusal: better to report the text as undeliverable than
            # to flood the chat with dozens of messages.
            return {"channel": self.name, "status": "FAILED", "http_status": None,
                    "external_id": None, "error": "TEXT_TOO_LONG"}
        url = f"https://api.telegram.org/bot{self.setting('TELEGRAM_BOT_TOKEN')}/sendMessage"
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=30, follow_redirects=False)
        try:
            sent = 0
            result = None
            for index, chunk in enumerate(chunks):
                payload = {"chat_id": reply.recipient, "text": chunk,
                           "link_preview_options": {"is_disabled": True}}
                # The reply threading belongs to the conversation, so only the
                # first chunk carries reply_parameters.
                if index == 0 and reply.reply_to is not None:
                    payload["reply_parameters"] = {"message_id": int(reply.reply_to)}
                result = await self._post_message(client, url, payload)
                if result["status"] != "SENT":
                    if len(chunks) > 1:
                        result["partial_sent"] = sent
                    return result  # Later chunks are not attempted.
                sent += 1
            return result  # Last chunk's SENT result; external_id is its message_id.
        finally:
            if own_client:
                await client.aclose()
