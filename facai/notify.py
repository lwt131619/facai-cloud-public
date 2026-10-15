"""Deliver mission progress to the chat channel that requested the work.

A mission created from a Telegram or WhatsApp message currently runs silently:
the requester sees nothing until they open the web console. This relay closes
that loop. It follows the same pattern as the n8n relay — a durable event
cursor, at-most-once delivery per event, honest failures — but sends human
progress lines back into the chat:

* a step completes        -> ``✅ 阶段 … 完成 · 总进度 N%``
* a step fails            -> ``❌ 阶段 … 失败：<error>``
* the whole mission done  -> ``🏁 任务全部完成``

Events whose mission did not come from a chat channel are skipped, and the
cursor advances past every event either way, so a send failure never blocks
the pipeline or duplicates a flood of retries.
"""

import json
import logging
import os
import threading

from . import secretbox
from .channels import OutboundReply
from .channels.discord import DiscordProvider
from .channels.line import LineProvider
from .channels.slack import SlackProvider
from .channels.telegram import TelegramProvider
from .secretbox import SecretBoxError

logger = logging.getLogger(__name__)

# WeChat joined after iLink authorization landed; every chat channel that
# originates a mission receives its progress the same way.
# relay joined for AutoClaw: its provider queues into the durable outbox
# that the bridge client polls, so progress lines reach WhatsApp the same way.
# line joined before the station's official-account token exists: the NOTIFY
# loop pushes through an owner's own bound LINE channel when there is one,
# falls back to the station credentials when those exist, and silently skips
# otherwise -- the same honest unconfigured semantics as every other channel.
# discord/slack joined as adapter-family members: they push only when the
# station (or, once customer binding lands for them, an owner) holds live
# credentials, and stay an honest UNCONFIGURED entry everywhere else.
NOTIFY_CHANNELS = ("telegram", "whatsapp", "wechat", "relay", "line",
                   "discord", "slack")

# Channels where a customer may bind their OWN bot ("绑一次 = 云端 7×24"): when
# the mission's owner holds such a binding, that owner's progress rides their
# bot and their bound chat. Station-level providers keep their exact behavior
# for every owner without a binding. Future customer-bindable channels plug in
# here; store and API are already generic over the channel name. For line the
# binding's chat_id column carries the bound LINE userId (its per-channel
# meaning -- the push API's ``to``), exactly as it carries telegram's chat id;
# discord/slack carry a channel snowflake / channel id the same way.
OWNER_BINDING_CHANNELS = frozenset({"telegram", "line", "discord", "slack"})

# A temporary owner-bot provider exists for outbound sends only -- it never
# verifies webhooks -- so the placeholder merely satisfies the provider's
# configured() check when the station itself has no matching secret.
_OWNER_OUTBOUND_WEBHOOK_SECRET = "facai-owner-outbound-only"
_OWNER_OUTBOUND_CHANNEL_SECRET = "facai-owner-outbound-only"


def _owner_telegram_provider(token):
    """Outbound-only Telegram provider built from the owner's bound bot token."""
    env = dict(os.environ)
    env["TELEGRAM_BOT_TOKEN"] = token
    env.setdefault("TELEGRAM_WEBHOOK_SECRET", _OWNER_OUTBOUND_WEBHOOK_SECRET)
    return TelegramProvider(env=env)


def _owner_line_provider(credential):
    """Outbound-only LINE provider built from the owner's stored binding.

    The binding keeps ONE secretbox ciphertext carrying both credentials as
    JSON: the channel access token that push needs, plus the channel secret
    reserved for future webhook verification. Only the access token leaves the
    box into memory here. Anything unusable returns None so the station
    channel keeps serving exactly as before.
    """
    try:
        payload = json.loads(credential)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    token = (payload.get("access_token") or "").strip()
    if not token:
        return None
    env = dict(os.environ)
    env["LINE_CHANNEL_ACCESS_TOKEN"] = token
    env.setdefault("LINE_CHANNEL_SECRET", _OWNER_OUTBOUND_CHANNEL_SECRET)
    return LineProvider(env=env)


def _owner_discord_provider(token):
    """Outbound-only Discord provider built from the owner's bound bot token."""
    if not token:
        return None
    env = dict(os.environ)
    env["DISCORD_BOT_TOKEN"] = token
    env.setdefault("DISCORD_WEBHOOK_SECRET", _OWNER_OUTBOUND_WEBHOOK_SECRET)
    return DiscordProvider(env=env)


def _owner_slack_provider(token):
    """Outbound-only Slack provider built from the owner's bound bot token."""
    if not token:
        return None
    env = dict(os.environ)
    env["SLACK_BOT_TOKEN"] = token
    env.setdefault("SLACK_SIGNING_SECRET", _OWNER_OUTBOUND_CHANNEL_SECRET)
    return SlackProvider(env=env)


# One builder per owner-bindable channel: turn the decrypted binding
# credential into the channel's outbound-only provider.
_OWNER_PROVIDER_BUILDERS = {
    "telegram": _owner_telegram_provider,
    "line": _owner_line_provider,
    "discord": _owner_discord_provider,
    "slack": _owner_slack_provider,
}

def owner_bot(store, channel, owner_id):
    """(provider, chat_id) via the owner's own bound bot, or None.

    The single trust path for outbound sends over a customer's own binding:
    the bound credential is decrypted here, in memory, at send time only; it
    is never logged, audited or returned. Anything unresolved -- no binding,
    undecryptable credential, unusable payload, lookup failure -- returns None
    so callers fall back honestly (the station channel keeps serving the
    NOTIFY loop; the API test-push reports the send never left).

    Module-level so the ``/api/v1/my/channels/{channel}/test`` endpoint builds
    its provider through the exact same path the NOTIFY loop uses -- the test
    push and the real progress push must never drift apart.
    """
    if channel not in OWNER_BINDING_CHANNELS or not owner_id:
        return None
    builder = _OWNER_PROVIDER_BUILDERS.get(channel)
    if builder is None:
        return None
    try:
        binding = store.channel_binding(owner_id, channel)
        encrypted = store.channel_binding_token(owner_id, channel)
    except Exception:
        logger.warning("channel binding lookup failed for %s/%s",
                       owner_id, channel, exc_info=True)
        return None
    if not binding or not encrypted or not binding.get("chat_id"):
        return None
    try:
        credential = secretbox.decrypt(store.root, encrypted)
    except SecretBoxError:
        logger.warning("owner %s channel binding cannot be decrypted; "
                       "falling back to the station channel", owner_id)
        return None
    try:
        provider = builder(credential)
    except Exception:
        logger.warning("owner %s channel %s binding payload unusable; "
                       "falling back to the station channel",
                       owner_id, channel, exc_info=True)
        return None
    if provider is None:
        return None
    return provider, binding["chat_id"]


STEP_LABELS = {
    "STEP_COMPLETED": "✅ 阶段「{label}」完成 · 总进度 {progress}%",
    "STEP_FAILED": "❌ 阶段「{label}」失败：{error}",
    "STEP_RUNNING": "▶️ 阶段「{label}」开始执行",
}

MISSION_DONE = "🏁 任务「{title}」全部完成，共 {steps} 个阶段。"
MISSION_BLOCKED = "⚠️ 任务「{title}」受阻：{reason}"


class ChannelNotifier:
    """Background relay from mission events to chat-channel replies."""

    def __init__(self, store, registry, interval=2.0):
        self.store = store
        self.registry = registry
        self.interval = interval
        self._stop = threading.Event()
        self._thread = None
        # mission_id -> {channel: (chat_id, message_id)}: progress updates
        # refresh the same chat message instead of spamming new ones. In-memory
        # only; after a restart one fresh message is simply sent again.
        self._live = {}

    def _owner_bot(self, channel, owner_id):
        """(provider, chat_id) via the owner's own bound bot, or None."""
        return owner_bot(self.store, channel, owner_id)

    def _send(self, channel, recipient, text, mission_id=None, editing=False, owner_id="admin"):
        provider = self.registry.get(channel)
        owner_bot = self._owner_bot(channel, owner_id)
        if owner_bot is not None:
            # 优先客户自己的 bot：their token and their bound chat carry this
            # owner's progress, even when the station bot is unconfigured.
            provider, recipient = owner_bot
        if provider is None or not provider.configured():
            return False
        import asyncio

        try:
            if editing and mission_id and (entry := self._live.get(mission_id, {}).get(channel)):
                chat_id, message_id = entry
                edit = getattr(provider, "edit", None)
                if callable(edit) and asyncio.run(edit(chat_id, message_id, text)):
                    return True  # refreshed in place; fall through only on failure
            result = asyncio.run(provider.send(OutboundReply(
                channel=channel, recipient=recipient, text=text)))
            # Audit every outbound chat line (text only, never a credential) so
            # the console can show what a channel actually received — unless the
            # provider already durably records the delivery itself (relay's
            # outbox is the audit trail; a second row would double-deliver).
            if not getattr(provider, "records_outbound_itself", False):
                try:
                    self.store.record_channel_message(
                        channel, "OUTBOUND",
                        (result or {}).get("status", "SENT") if isinstance(result, dict) else "SENT",
                        external_id=(result or {}).get("external_id") if isinstance(result, dict) else None,
                        recipient=recipient, text=text, mission_id=mission_id,
                        owner_id=owner_id or "admin")
                except Exception:
                    logger.warning("channel notify audit failed for %s", channel, exc_info=True)
            if mission_id and isinstance(result, dict) and result.get("external_id"):
                # Telegram returns a real message id; other channels just keep
                # sending fresh messages (their send result lacks a usable id).
                self._live.setdefault(mission_id, {})[channel] = (recipient, result["external_id"])
            return True
        except Exception:
            logger.warning("channel notify send failed for %s", channel,
                           exc_info=True)
            if owner_bot is not None:
                # 失败如实落审计：a customer-bot send that raised is recorded as
                # a FAILED outbound row (text only, never the token) and never
                # blocks the pipeline -- the cursor advances either way.
                try:
                    self.store.record_channel_message(
                        channel, "OUTBOUND", "FAILED", recipient=recipient, text=text,
                        mission_id=mission_id, owner_id=owner_id or "admin")
                except Exception:
                    logger.warning("channel notify failure audit failed for %s",
                                   channel, exc_info=True)
            return False

    def _deliver(self, event, mission, channel, recipient, owner_id="admin"):
        event_type = event["type"]
        payload = event.get("payload") or {}
        label = next((step["label"] for step in mission["steps"]
                      if step["id"] == event.get("step_id")), None) \
            or payload.get("label") or "阶段"
        editing = event_type == "STEP_COMPLETED" and mission["progress"] < 100
        if event_type == "STEP_COMPLETED" and mission["progress"] >= 100:
            text = MISSION_DONE.format(title=mission["title"], steps=len(mission["steps"]))
        elif event_type == "STEP_FAILED":
            text = STEP_LABELS[event_type].format(
                label=label, error=(payload.get("error") or "未知错误")[:200])
        elif event_type == "STEP_BLOCKED":
            text = MISSION_BLOCKED.format(title=mission["title"],
                                          reason=f"阶段「{label}」依赖失败")
        elif event_type in STEP_LABELS:
            text = STEP_LABELS[event_type].format(
                label=label, progress=mission["progress"])
        else:
            return
        self._send(channel, recipient, text, mission_id=mission.get("id"), editing=editing,
                   owner_id=owner_id)

    def run_pending(self, limit=100):
        """Deliver notifications for new events of channel-linked missions."""
        cursor = self.store.channel_notify_cursor()
        if cursor is None:
            # First run: start from the current head so enabling the relay
            # never replays the whole event history into a chat.
            self.store.set_channel_notify_cursor(self.store.last_event_sequence())
            return 0
        linked = self.store.channel_linked_missions(NOTIFY_CHANNELS)
        sent = 0
        for event in self.store.n8n_events_after(cursor, limit):
            cursor = event["sequence"]
            mission_id = event.get("mission_id")
            if mission_id in linked:
                channel = next((c for c in NOTIFY_CHANNELS
                                if self.store.channel_sender_for_mission(mission_id, c)), None)
                recipient = (self.store.channel_sender_for_mission(mission_id, channel)
                             if channel else None)
                if channel and recipient:
                    mission = self.store.get(mission_id)
                    if mission is not None:
                        self._deliver(event, mission, channel, recipient,
                                      owner_id=mission.get("owner_id", "admin"))
                        sent += 1
            self.store.set_channel_notify_cursor(cursor)
        return sent

    def start(self, interval=None):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve, args=(interval or self.interval,), daemon=True)
        self._thread.start()

    def _serve(self, interval):
        while not self._stop.is_set():
            try:
                self.run_pending()
            except Exception:
                logger.exception("channel notify round failed")
            self._stop.wait(interval)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
