"""WhatsApp channel adapter using the official Meta Cloud API shape.

This is an interface skeleton, not a claimed live integration. It implements the
three parts the Cloud API requires:

* ``GET`` webhook verification (``hub.mode`` / ``hub.verify_token`` / ``hub.challenge``);
* ``POST`` webhook payload parsing into the unified ``InboundMessage`` model;
* an outbound text sender against ``graph.facebook.com``.

Credentials are read from the process environment only, referenced by name in
the classified provider env file (``deploy/providers/whatsapp.env.example``).
Without complete credentials the adapter reports ``UNCONFIGURED`` and refuses to
send; it never prints or returns a secret value.

``send`` never fabricates success: a result is only ``SENT`` when the Graph API
answers a 2xx with a concrete ``messages[0].id``. Every other outcome (upstream
rejection, malformed body, timeout, transport failure) is reported with a
stable, non-secret reason code -- mirroring the Telegram adapter's honesty
convention -- so Mission Control can tell a token problem from rate limiting
from a transient network error instead of seeing one opaque ``FAILED``.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from .base import (
    ChannelProvider,
    InboundMessage,
    hmac_sha256_hex,
    secure_equals,
)

DEFAULT_GRAPH_VERSION = "v21.0"

# The pairing-code host bridge (bridge/whatsapp-relay) is reached over its
# signed admin API; the URL is plain deployment config, not a credential.
WHATSAPP_BRIDGE_URL_ENV = "FACAI_WHATSAPP_BRIDGE_URL"
BRIDGE_TIMEOUT_SECONDS = 15.0  # /host/pair itself waits up to ~10s bridge-side


class WhatsAppBridgeError(Exception):
    """A bridge control call failed, with a STABLE, non-secret reason code.

    Mirrors the adapter honesty convention: callers get a short category
    (BRIDGE_UNREACHABLE / BRIDGE_TIMEOUT / BRIDGE_REJECTED / BRIDGE_BAD_BODY)
    instead of a raw traceback or URL, so API errors never leak the bridge's
    location or internals.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def normalize_pairing_phone(raw) -> str:
    """E.164 digits without the leading '+', else ValueError (business wording).

    WhatsApp personal numbers are 7-15 digits nation+subscriber; the bridge
    re-validates, but the API rejects early so a malformed phone never
    reaches the bridge or creates a binding row. A module-level function on
    purpose: the endpoint validates through this name even where tests patch
    the client CLASS with a stand-in.
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if not 7 <= len(digits) <= 15:
        raise ValueError("手机号必须是 7-15 位数字（国家码+号码，无需 + 号）")
    return digits


class WhatsAppHostBridge:
    """Signed HTTP client for the per-owner pairing sessions on the bridge.

    The VPS bridge (bridge/whatsapp-relay) holds one Baileys session per owner
    under its own ``wa-auth/hosting/<owner>/`` namespace; this client only
    commands it: mint a pairing code, read the session's link status, log out.
    Every request carries the SAME HMAC signature the relay channel uses
    (``X-Facai-Signature`` over ``<timestamp>.<body>`` with the shared
    ``RELAY_SHARED_SECRET``) — the deployment keeps exactly one shared secret.

    This is deliberately NOT a ChannelProvider: it parses no webhooks and
    sends no replies — it is the control plane for the hosted sessions, kept
    next to the WhatsApp adapter because it shares the channel's secret and
    honesty conventions.

    Responses are passed through VERBATIM; the pairing code is never cached or
    logged here — Baileys codes are single-use and short-lived, so any later
    poll re-reads the live state from the bridge instead of replaying.
    """

    name = "whatsapp-host-bridge"
    required_env = ("RELAY_SHARED_SECRET",)

    def __init__(self, env=None, base_url=None):
        self.env = env if env is not None else os.environ
        self.base_url = (base_url or "").rstrip("/")

    @classmethod
    def from_env(cls, env=None):
        env = env if env is not None else os.environ
        return cls(env, base_url=(env.get(WHATSAPP_BRIDGE_URL_ENV) or "").strip())

    def setting(self, key):
        return (self.env.get(key) or "").strip()

    def missing(self):
        return [key for key in self.required_env if not self.setting(key)]

    def configured(self):
        """Both the shared secret AND the bridge URL must be present."""
        return bool(self.base_url) and not self.missing()

    normalize_phone = staticmethod(normalize_pairing_phone)

    # --- signed transport (the relay scheme, Facai -> bridge direction) -----

    def _signed_headers(self, body: bytes) -> dict:
        timestamp = str(int(time.time()))
        message = timestamp.encode("ascii") + b"." + body
        return {
            "Content-Type": "application/json",
            "X-Facai-Signature": "sha256=" + hmac_sha256_hex(
                self.setting("RELAY_SHARED_SECRET"), message),
            "X-Facai-Timestamp": timestamp,
        }

    async def _call(self, method, path, payload, client=None) -> dict:
        if not self.configured():
            raise WhatsAppBridgeError("BRIDGE_NOT_CONFIGURED")
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=BRIDGE_TIMEOUT_SECONDS)
        try:
            response = await client.request(method, self.base_url + path,
                                            headers=self._signed_headers(body),
                                            content=body if method != "GET" else None)
        except httpx.TimeoutException:
            raise WhatsAppBridgeError("BRIDGE_TIMEOUT") from None
        except httpx.RequestError:
            raise WhatsAppBridgeError("BRIDGE_UNREACHABLE") from None
        finally:
            if own_client:
                await client.aclose()
        if not (200 <= response.status_code < 300):
            raise WhatsAppBridgeError("BRIDGE_REJECTED")
        try:
            parsed = response.json()
        except ValueError:
            raise WhatsAppBridgeError("BRIDGE_BAD_BODY") from None
        if not isinstance(parsed, dict):
            raise WhatsAppBridgeError("BRIDGE_BAD_BODY")
        return parsed

    # --- bridge commands ------------------------------------------------------

    async def start_pairing(self, owner: str, phone: str, client=None) -> dict:
        """Mint an 8-digit pairing code in the owner's own bridge session.

        Bridge answers (passed through verbatim): PAIRING_CODE (code ready),
        PAIRING_WAIT (socket still connecting; the code arrives via status),
        LINKED (session already live), RELINKING (stored credentials are
        reconnecting to their original number — a code would be a lie).
        """
        return await self._call("POST", "/host/pair",
                                {"owner": owner, "phone": phone}, client)

    async def session_status(self, owner: str, client=None) -> dict:
        """The owner's session state: NONE / PAIRING (code if still fresh) / LINKED."""
        return await self._call("GET", f"/host/session?owner={quote(owner, safe='')}",
                                None, client)

    async def logout(self, owner: str, client=None) -> dict:
        """Revoke the owner's bridge session (server-side unpair + credential wipe)."""
        return await self._call("POST", "/host/logout", {"owner": owner}, client)


class WhatsAppCloudProvider(ChannelProvider):
    name = "whatsapp"
    implemented = True
    required_env = (
        "WHATSAPP_VERIFY_TOKEN",
        "WHATSAPP_APP_SECRET",
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_PHONE_NUMBER_ID",
    )

    # --- configuration -----------------------------------------------------

    @property
    def graph_version(self):
        return self.setting("WHATSAPP_GRAPH_VERSION") or DEFAULT_GRAPH_VERSION

    # --- webhook verification ---------------------------------------------

    def verify_challenge(self, params):
        if params.get("hub.mode") != "subscribe":
            return None
        token = self.setting("WHATSAPP_VERIFY_TOKEN")
        if not token or not secure_equals(params.get("hub.verify_token"), token):
            return None
        challenge = params.get("hub.challenge")
        return challenge if challenge else None

    def verify_signature(self, body, headers):
        secret = self.setting("WHATSAPP_APP_SECRET")
        supplied = headers.get("x-hub-signature-256", "")
        if not secret or not supplied:
            return False
        prefix, _, digest = supplied.partition("=")
        if prefix != "sha256" or not digest:
            return False
        return secure_equals(hmac_sha256_hex(secret, body), digest)

    # --- inbound -----------------------------------------------------------

    def parse_inbound(self, payload):
        messages = []
        if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
            return messages
        entry_list = payload.get("entry")
        if not isinstance(entry_list, list):
            return messages
        for entry in entry_list:
            if not isinstance(entry, dict):
                continue
            changes = entry.get("changes")
            if not isinstance(changes, list):
                continue
            for change in changes:
                if not isinstance(change, dict):
                    continue
                if change.get("field") != "messages":
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                if value.get("messaging_product") != "whatsapp":
                    continue
                metadata = value.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                recipient = metadata.get("display_phone_number")
                contacts_list = value.get("contacts")
                if not isinstance(contacts_list, list):
                    contacts_list = []
                contacts = {}
                for c in contacts_list:
                    if not isinstance(c, dict):
                        continue
                    wa_id = c.get("wa_id")
                    if not isinstance(wa_id, str) or not wa_id:
                        continue
                    profile = c.get("profile")
                    name = profile.get("name") if isinstance(profile, dict) else None
                    contacts[wa_id] = name
                messages_list = value.get("messages")
                if not isinstance(messages_list, list):
                    continue
                for message in messages_list:
                    if not isinstance(message, dict):
                        continue
                    sender = message.get("from")
                    kind = message.get("type") or "unknown"
                    external_id = message.get("id")
                    if (not isinstance(external_id, str) or not external_id.strip()
                            or not isinstance(sender, str) or not sender.strip()
                            or not isinstance(kind, str) or not kind.strip()):
                        continue
                    messages.append(InboundMessage(
                        channel=self.name,
                        external_id=external_id,
                        sender=sender,
                        recipient=recipient,
                        text=self._text_of(kind, message),
                        kind=kind,
                        received_at=self._timestamp(message.get("timestamp")),
                        raw=message,
                        metadata={
                            "phone_number_id": metadata.get("phone_number_id"),
                            "display_phone_number": recipient,
                            "profile_name": contacts.get(sender),
                        },
                    ))
        return messages

    @staticmethod
    def _text_of(kind, message):
        if kind == "text":
            text = message.get("text")
            body = text.get("body", "") if isinstance(text, dict) else ""
            return body if isinstance(body, str) else ""
        if kind == "button":
            button = message.get("button")
            text = button.get("text", "") if isinstance(button, dict) else ""
            return text if isinstance(text, str) else ""
        if kind == "interactive":
            interactive = message.get("interactive")
            if not isinstance(interactive, dict):
                return ""
            reply = interactive.get("button_reply") or interactive.get("list_reply") or {}
            title = reply.get("title", "") if isinstance(reply, dict) else ""
            return title if isinstance(title, str) else ""
        return ""

    @staticmethod
    def _timestamp(value):
        try:
            return datetime.fromtimestamp(int(value), timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return datetime.now(timezone.utc).isoformat()

    # --- outbound ----------------------------------------------------------

    async def send(self, reply, client=None):
        self.require_configured()
        if not reply.recipient:
            raise ValueError("A WhatsApp reply needs a recipient")
        if reply.reply_to is not None and (not isinstance(reply.reply_to, str)
                                           or not reply.reply_to.strip()):
            raise ValueError("A WhatsApp reply_to must be a message id")
        url = (f"https://graph.facebook.com/{self.graph_version}/"
               f"{self.setting('WHATSAPP_PHONE_NUMBER_ID')}/messages")
        headers = {
            "Authorization": f"Bearer {self.setting('WHATSAPP_ACCESS_TOKEN')}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": reply.recipient,
            "type": "text",
            "text": {"preview_url": False, "body": reply.text},
        }
        if reply.reply_to is not None:
            # Cloud API threading: replying to an inbound wamid quotes it in the
            # customer's app. Mirrors the Telegram adapter's reply_to handling so
            # the unified OutboundReply behaves the same on every channel.
            payload["context"] = {"message_id": reply.reply_to.strip()}
        own_client = client is None
        if own_client:
            client = httpx.AsyncClient(timeout=30)
        result = {"channel": self.name, "status": "FAILED", "http_status": None,
                  "external_id": None, "error": None}
        try:
            response = await client.post(url, json=payload, headers=headers)
            result["http_status"] = response.status_code
            try:
                body = response.json()
            except ValueError:
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            if not isinstance(body, dict):
                return {**result, "error": "INVALID_UPSTREAM_RESPONSE"}
            messages = body.get("messages")
            message = messages[0] if isinstance(messages, list) and messages else None
            # A message is only ever reported SENT when the Graph API echoes back a
            # concrete message id for a 2xx response. Anything else -- including a
            # 2xx with a malformed/empty body -- must not be fabricated as success.
            if (200 <= response.status_code < 300 and isinstance(message, dict)
                    and isinstance(message.get("id"), str) and message["id"]):
                return {**result, "status": "SENT", "external_id": message["id"]}
            reason = self._failure_reason(response.status_code, body)
            return {**result, "error": reason, "detail": self._error_text(body) or None}
        except httpx.TimeoutException:
            return {**result, "error": "TIMEOUT"}
        except httpx.RequestError:
            return {**result, "error": "TRANSPORT_ERROR"}
        finally:
            if own_client:
                await client.aclose()

    @staticmethod
    def _failure_reason(status_code, body):
        """Map an upstream HTTP status to a stable, non-secret failure category.

        Mirrors the Telegram adapter's honesty convention: callers get a short,
        stable reason code (not a raw, possibly-localized Meta error string) so
        Mission Control can distinguish credential problems from rate limiting
        from permanent rejections without guessing at prose.
        """
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        if status_code == 401 or code == 190:
            return "AUTH_FAILED"
        if status_code == 403:
            return "PERMISSION_DENIED"
        if status_code == 429:
            return "RATE_LIMITED"
        return "UPSTREAM_REJECTED"

    @staticmethod
    def _error_text(body):
        if not isinstance(body, dict):
            return ""
        error = body.get("error")
        if isinstance(error, dict):
            return " ".join(str(error.get(key, "")) for key in ("type", "code", "message")).strip()
        return str(error) if error else ""
