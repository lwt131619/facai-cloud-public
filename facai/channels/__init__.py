"""Message channel registry.

``ChannelRegistry`` builds every known provider once and exposes its honest
status. Providers read credentials from the process environment, so a status
call never contacts a network and never exposes a secret value.
"""

import os
from pathlib import Path

from .base import ChannelConfigError, ChannelStatus, InboundMessage, OutboundReply
from .line import LineProvider
from .telegram import TelegramProvider
from .wechat import WeChatProvider
from .relay import RelayProvider
from .whatsapp import WhatsAppCloudProvider
from .discord import DiscordProvider
from .slack import SlackProvider

PROVIDER_CLASSES = {
    WhatsAppCloudProvider.name: WhatsAppCloudProvider,
    RelayProvider.name: RelayProvider,
    WeChatProvider.name: WeChatProvider,
    TelegramProvider.name: TelegramProvider,
    LineProvider.name: LineProvider,
    DiscordProvider.name: DiscordProvider,
    SlackProvider.name: SlackProvider,
}


def build_providers(env=None, state_dir=None):
    env = env if env is not None else os.environ
    return {name: (WeChatProvider(env, state_dir=state_dir) if name == "wechat" else cls(env))
            for name, cls in PROVIDER_CLASSES.items()}


class ChannelRegistry:
    def __init__(self, env=None, state_dir=None):
        self.providers = build_providers(env, state_dir=state_dir)

    @property
    def channels(self):
        return tuple(self.providers)

    def get(self, channel):
        return self.providers.get(channel)

    def require(self, channel):
        provider = self.get(channel)
        if provider is None:
            raise KeyError(channel)
        return provider

    def statuses(self):
        return {name: provider.status().to_dict()
                for name, provider in self.providers.items()}

    def status(self, channel):
        return self.require(channel).status()


__all__ = [
    "ChannelConfigError",
    "ChannelRegistry",
    "ChannelStatus",
    "InboundMessage",
    "OutboundReply",
    "PROVIDER_CLASSES",
    "build_providers",
]
