"""Client-supplied model credentials: encryption at rest and endpoint guard.

Clients bring their own models and API keys (the operator's models are never
shared with them). Their keys are:

* encrypted at rest with Fernet (AES-128-CBC + HMAC, ``cryptography``); the
  master key lives in the process environment (``FACAI_MASTER_KEY``) or in a
  root-only file under the data directory — never in the database;
* decrypted only in memory at the moment a request or a run needs them;
* never returned by any API (only a masked tail), never logged, never written
  to a command line.

Endpoints are validated at registration: by default only public HTTPS origins
are accepted, because the server itself will open the connection (a private or
loopback target would make model registration an SSRF primitive against the
Facai deployment). Self-hosted operators who want to register LAN/localhost
models (their own ollama, for example) can set
``FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1`` — the guard then accepts private
addresses but still refuses the Facai service's own loopback API port.
"""

import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken

MASTER_KEY_FILE = "master.key"


class SecretBoxError(ValueError):
    """Raised when a credential cannot be stored or recovered safely."""


def master_key(root=None, env=None):
    """The Fernet master key: env override, else a 0600 file in the data dir.

    The file is created on first use so a fresh instance works without extra
    setup; it is the root of trust for every client-registered key and must be
    treated (and backed up) like the provider env files.
    """
    env = env if env is not None else os.environ
    value = (env.get("FACAI_MASTER_KEY") or "").strip()
    if value:
        try:
            Fernet(value.encode("ascii"))
            return value.encode("ascii")
        except (ValueError, TypeError) as exc:
            # 逆向克隆防护：错误文案会经 422 detail 原样到达客户，绝不写环境
            # 变量名/内部文件名——SecretBoxError 从 register-model / bind-channel
            # 等客户可达端点直接 str(exc) 返回。
            raise SecretBoxError("凭据主密钥无效（服务端配置问题），请联系管理员") from exc
    if root is None:
        raise SecretBoxError("凭据主密钥不可用（服务端尚未初始化），请联系管理员")
    path = Path(root) / MASTER_KEY_FILE
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        path.write_bytes(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    key = path.read_bytes().strip()
    try:
        Fernet(key)
    except (ValueError, TypeError) as exc:
        # 同上：不把主密钥文件的路径带进客户可见的 422 detail。
        raise SecretBoxError("凭据主密钥文件损坏（服务端配置问题），请联系管理员") from exc
    return key


def encrypt(root, plaintext):
    if not isinstance(plaintext, str) or not plaintext:
        raise SecretBoxError("cannot encrypt an empty credential")
    token = Fernet(master_key(root)).encrypt(plaintext.encode("utf-8"))
    return token.decode("ascii")


def decrypt(root, token):
    try:
        return Fernet(master_key(root)).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise SecretBoxError("stored credential cannot be decrypted with this master key") from exc


def mask(plaintext):
    """The only form of a key that may ever leave the server: its tail."""
    if not plaintext:
        return ""
    return f"****{plaintext[-4:]}" if len(plaintext) > 4 else "****"


def _reject(reason):
    raise SecretBoxError(reason)


def validate_public_endpoint(endpoint, env=None):
    """Registration-time endpoint guard: public HTTPS only, by default.

    DNS is resolved and every answered address must be public, so a hostname
    that pretends to be public but resolves into the datacenter cannot be used
    to reach the deployment's own internals. With
    ``FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1`` private addresses are allowed for
    self-hosted LAN models, but the local Facai API is still refused.
    """
    env = env if env is not None else os.environ
    text = str(endpoint or "").strip()
    parts = urlsplit(text)
    if parts.scheme != "https" and not (env.get("FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS") == "1"
                                        and parts.scheme == "http"):
        _reject("模型端点必须是 https://（内网 http 端点需运营者启用自托管选项）")
    if not parts.hostname:
        _reject("模型端点缺少主机名")
    if parts.username or parts.password:
        _reject("模型端点不应包含凭据")
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except OSError as exc:
        _reject(f"模型端点主机无法解析：{type(exc).__name__}")
    allow_private = env.get("FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS") == "1"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved \
                or ip.is_multicast or ip.is_unspecified:
            if not (allow_private and not ip.is_loopback):
                _reject("模型端点指向内网/回环地址，已拒绝（防 SSRF）")
    if allow_private:
        host = (parts.hostname or "").lower()
        if host in ("127.0.0.1", "::1", "localhost") or text.rstrip("/").endswith(":8765"):
            _reject("模型端点不得指向 Facai 服务自身的回环 API")
    return text
