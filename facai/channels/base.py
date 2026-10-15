"""Unified message-channel layer for Facai Cloud.

The channel layer sits between an external messaging service and Mission
Control. It defines one inbound model, one outbound model and one provider
contract, so a new service (WhatsApp now, WeChat/Telegram later) plugs in
without changing the API surface.

A provider is honest about its state. Credentials are read from the process
environment only (on the server these come from the classified provider env
files under ``/etc/facai-cloud/providers/``). When any required credential is
missing the provider reports ``configured=False``, returns the missing variable
*names* (never their values) and refuses to send. A provider never fabricates a
live connection.
"""

import hashlib
import hmac
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping

STATE_CONFIGURED = "CONFIGURED"
STATE_UNCONFIGURED = "UNCONFIGURED"
STATE_UNIMPLEMENTED = "UNIMPLEMENTED"


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class ChannelConfigError(ValueError):
    """Raised when a channel is missing required credentials."""

    def __init__(self, channel, missing):
        self.channel = channel
        self.missing = list(missing)
        super().__init__(
            f"{channel} is not configured; missing {', '.join(self.missing) or 'credentials'}")


@dataclass
class InboundMessage:
    """A normalized message received from an external channel."""

    channel: str
    external_id: str
    sender: str
    text: str
    received_at: str
    recipient: str | None = None
    kind: str = "text"
    raw: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "channel": self.channel,
            "external_id": self.external_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "kind": self.kind,
            "text": self.text,
            "received_at": self.received_at,
            "metadata": self.metadata,
        }


@dataclass
class OutboundReply:
    """A normalized reply to send through an external channel."""

    channel: str
    recipient: str
    text: str
    reply_to: str | None = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return {
            "channel": self.channel,
            "recipient": self.recipient,
            "text": self.text,
            "reply_to": self.reply_to,
            "metadata": self.metadata,
        }


@dataclass
class ChannelStatus:
    """The honest, secret-free state of one channel provider."""

    channel: str
    implemented: bool
    configured: bool
    state: str
    required: list
    missing: list
    verify_ready: bool
    send_ready: bool
    detail: str

    def to_dict(self):
        return {
            "channel": self.channel,
            "implemented": self.implemented,
            "configured": self.configured,
            "state": self.state,
            "required": list(self.required),
            "missing": list(self.missing),
            "verify_ready": self.verify_ready,
            "send_ready": self.send_ready,
            "detail": self.detail,
        }


class ChannelProvider(ABC):
    """Contract every messaging channel adapter implements.

    ``required_env`` names the environment variables that must all be present
    for the channel to be considered configured. Only names are ever exposed.
    """

    name = ""
    implemented = False
    required_env: tuple = ()

    def __init__(self, env=None):
        self.env = env if env is not None else os.environ

    def setting(self, key):
        return (self.env.get(key) or "").strip()

    def settings(self):
        return {key: self.setting(key) for key in self.required_env}

    def missing(self):
        return [key for key, value in self.settings().items() if not value]

    def configured(self):
        return not self.missing()

    def require_configured(self):
        if not self.implemented:
            raise NotImplementedError(f"{self.name} adapter interface is reserved, not wired")
        missing = self.missing()
        if missing:
            raise ChannelConfigError(self.name, missing)

    def status(self) -> ChannelStatus:
        missing = self.missing()
        configured = not missing
        if not self.implemented:
            state, detail = STATE_UNIMPLEMENTED, "接口已预留，尚未接线"
        elif configured:
            state, detail = STATE_CONFIGURED, "凭据完整，可接收 Webhook 与发送"
        else:
            state, detail = STATE_UNCONFIGURED, "缺少凭据，安全返回未配置状态"
        return ChannelStatus(
            channel=self.name,
            implemented=self.implemented,
            configured=configured and self.implemented,
            state=state,
            required=list(self.required_env),
            missing=missing,
            verify_ready=configured and self.implemented,
            send_ready=configured and self.implemented,
            detail=detail,
        )

    # --- webhook -----------------------------------------------------------

    def verify_challenge(self, params: Mapping) -> str | None:
        """Return the challenge to echo for a GET webhook verification, else None."""
        return None

    def verify_signature(self, body: bytes, headers: Mapping) -> bool:
        """Verify the webhook request signature. Default: reject."""
        return False

    # --- parsing and sending ----------------------------------------------

    @abstractmethod
    def parse_inbound(self, payload: Mapping) -> list:
        """Normalize a provider webhook payload into ``InboundMessage`` objects."""

    @abstractmethod
    async def send(self, reply: OutboundReply, client=None) -> dict:
        """Send one reply. Must raise when the channel is unconfigured."""


def secure_equals(left, right):
    return hmac.compare_digest(str(left or "").encode("utf-8"),
                               str(right or "").encode("utf-8"))


def hmac_sha256_hex(secret, body):
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
