"""Personal WeChat iLink adapter.

Implements the QR login and JSON Bot API documented by Tencent's
openclaw-weixin protocol. A QR login is held in memory until confirmed; the
resulting token is kept only in the provider process environment and is never
returned to the browser or written to logs/database.
"""

import os
import secrets
import threading
import base64
import io
import zlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .base import ChannelProvider, InboundMessage, OutboundReply

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
# The two hosts are both official Tencent iLink ends and serve the identical
# protocol. On 2026-10-03 the .wechat.com host answered 524 on every getupdates
# long poll and timed out on get_qrcode_status while .weixin.qq.com stayed
# healthy, and stale credentials pinned the dead host. Failover across the pair
# keeps the channel alive without ever fabricating a success.
ALTERNATE_BASE_URL = "https://ilinkai.wechat.com"
# Both official host families. A redirect_host or a stored base_url is trusted
# only when it lands on one of these; the failover pair is a subset of them.
OFFICIAL_HOST_SUFFIXES = ("weixin.qq.com", "wechat.com")
# Consecutive gateway-5xx / transport failures on the active host before the
# other official host is tried. A single blip (a normal long-poll 524) must not
# flap the base URL, so more than one failure is required.
FAILOVER_THRESHOLD = 3
# Gateway statuses that mean "the origin/gateway gave up", not "the request was
# rejected": 502/503/504 and Cloudflare's 524.
GATEWAY_STATUSES = (502, 503, 504, 524)
# Keep both version fields in lockstep with the current Tencent/openclaw-weixin
# release: the WeChat client refuses QR binds whose declared interface version is
# older than the channel build it knows ("请在 OpenClaw 中升级 WeChat 接口版本").
BASE_INFO = {"channel_version": "2.4.9", "bot_agent": "OpenClaw/2.4.9"}


def _is_official_host(host):
    host = (host or "").lower()
    return any(host == suffix or host.endswith("." + suffix)
               for suffix in OFFICIAL_HOST_SUFFIXES)


def _ilink_url(value):
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.port not in (None, 443) or parsed.query or parsed.fragment or not parsed.hostname or not _is_official_host(parsed.hostname):
        raise ValueError("INVALID_ILINK_HOST")
    return value.rstrip("/")


def _alternate_official_base(base_url):
    """Return the other official iLink host for a known iLink base, else None."""
    host = (urlsplit(base_url).hostname or "").lower()
    if host == "wechat.com" or host.endswith(".wechat.com"):
        return DEFAULT_BASE_URL
    if host == "weixin.qq.com" or host.endswith(".weixin.qq.com"):
        return ALTERNATE_BASE_URL
    return None


def _app_headers():
    # iLink-App-ClientVersion encodes the plugin version as 0x00MMNNPP (2.4.9).
    return {"iLink-App-Id": "bot", "iLink-App-ClientVersion": str(0x00020409)}


def _png_chunk(tag, data):
    return (len(data).to_bytes(4, "big") + tag + data
            + zlib.crc32(tag + data).to_bytes(4, "big"))


def _qr_png_bytes(payload, border=4, scale=8):
    """Encode a QR payload as PNG without Pillow or pypng.

    qrcode.make(...).save PNG imports qrcode.image.pil, which raises
    ImportError when the runtime installed qrcode without the Pillow extra.
    Only the matrix encoder is used; PNG bytes come from the stdlib.
    """
    import qrcode

    matrix = qrcode.QRCode(border=border, error_correction=qrcode.constants.ERROR_CORRECT_M)
    matrix.add_data(payload)
    matrix.make(fit=True)
    return _png_from_modules(matrix.get_matrix(), border, scale)


def _png_from_modules(modules, border, scale=8):
    modules = list(modules)
    quiet = border * scale
    modules_px = len(modules) * scale
    size = modules_px + quiet * 2
    white = b"\xff" * size
    raw = bytearray()
    for _ in range(quiet):
        raw.append(0)
        raw.extend(white)
    for row in modules:
        line = bytearray(b"\xff" * quiet)
        for cell in row:
            line.extend(b"\x00" * scale if cell else b"\xff" * scale)
        line.extend(b"\xff" * quiet)
        for _ in range(scale):
            raw.append(0)
            raw.extend(line)
    for _ in range(quiet):
        raw.append(0)
        raw.extend(white)
    return b"".join((
        b"\x89PNG\r\n\x1a\n",
        _png_chunk(b"IHDR", size.to_bytes(4, "big") + size.to_bytes(4, "big") + b"\x08\x00\x00\x00\x00"),
        _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)),
        _png_chunk(b"IEND", b""),
    ))


class WeChatProvider(ChannelProvider):
    name = "wechat"
    implemented = True
    required_env = ("WECHAT_BOT_TOKEN", "WECHAT_ILINK_USER_ID")

    def __init__(self, env=None, state_dir=None):
        super().__init__(env)
        self._login = None
        # iLink rejects concurrent get_qrcode_status long-polls for the same QR.
        # Multiple viewers (console modal, background poller) are normal, so the
        # second caller gets an optimistic no-new-info "wait" instead of a
        # second upstream call that would break the first one.
        self._poll_lock = threading.Lock()
        self._updates_buf = ""
        self._pending_updates_buf = None
        # Consecutive gateway-5xx / transport failures on the active host. Any
        # non-gateway response resets it, so failover needs sustained trouble.
        self._consecutive_failures = 0
        self._base_url = (self.setting("WECHAT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._credential_file = Path(state_dir) / "wechat.env" if state_dir else None
        self._load_credentials()

    @classmethod
    def for_owner_state(cls, state_dir):
        """One owner's isolated provider instance (CHANNEL_HOST_DESIGN.md §1).

        The env mapping is a private dict, NEVER the process environment: a
        hosted customer's iLink credentials must not merge into the operator's
        global env, and no other owner's instance can see them either. The
        credential file lives only under the given state directory —
        ``<data>/providers/hosting/<owner_id>/wechat.env`` — so a customer QR
        confirmation can never overwrite the station's ``providers/wechat.env``,
        and each instance holds its own QR login state (a customer starting a
        scan no longer supersedes the operator's in-flight QR).
        """
        return cls({}, state_dir=Path(state_dir))

    def _load_credentials(self):
        if self._credential_file is None or not self._credential_file.is_file():
            return
        try:
            for line in self._credential_file.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition("=")
                if sep and key in {"WECHAT_BOT_TOKEN", "WECHAT_ILINK_USER_ID", "WECHAT_BASE_URL"}:
                    self.env[key] = value
            self._base_url = (self.setting("WECHAT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        except OSError:
            return

    def _save_credentials(self, values):
        if self._credential_file is None:
            raise OSError("secure provider state directory unavailable")
        self._credential_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._credential_file.parent, 0o700)
        except OSError:
            pass
        temp = self._credential_file.with_suffix(".tmp")
        content = "".join(f"{key}={values[key]}\n" for key in
                           ("WECHAT_BOT_TOKEN", "WECHAT_ILINK_USER_ID", "WECHAT_BASE_URL"))
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, self._credential_file)
        try:
            os.chmod(self._credential_file, 0o600)
        except OSError:
            pass

    def _clear_credentials(self):
        """Revoke the stored iLink authorization: env keys, login state, file.

        Symmetric with ``_save_credentials``: the same three keys and the same
        protected credential file. Clearing needs no state directory (nothing
        on disk belongs to this provider then), so unlike saving it does not
        raise for a missing one; a file that exists but cannot be removed is
        an OSError the caller must surface instead of a silent fake unbind.
        Returns whether the credential file existed and was deleted.
        """
        for key in ("WECHAT_BOT_TOKEN", "WECHAT_ILINK_USER_ID", "WECHAT_BASE_URL"):
            self.env.pop(key, None)
        self._login = None
        self._base_url = DEFAULT_BASE_URL
        if self._credential_file is None or not self._credential_file.is_file():
            return False
        self._credential_file.unlink()
        return True

    def unbind(self):
        """Admin-API entry: drop stored credentials so a fresh QR can be scanned.

        Only touches the data-side provider state (``<state_dir>/wechat.env``)
        and this process's provider env. Credentials injected by systemd from
        the operator-managed ``/etc/facai-cloud/providers/`` copy live outside
        this reach and are re-applied on restart; the API response says so
        instead of claiming a restart-proof unbind.
        """
        return self._clear_credentials()

    # --- official-domain failover -----------------------------------------

    async def _request_with_failover(self, client, method, path, base_url, **kwargs):
        """Issue one iLink request, failing over to the other official host.

        Returns ``(response, base_url_used)``. The response is returned exactly
        as received — a gateway 5xx stays a 5xx and a transport error is
        re-raised once failover is exhausted — so callers keep reporting honest
        retryable failures instead of a fabricated success.

        After ``FAILOVER_THRESHOLD`` consecutive gateway-5xx/transport failures
        on ``base_url`` the request is retried on the other official iLink host;
        when that host answers with anything other than a gateway error the
        caller may adopt it for later requests and persistence.
        """
        base = base_url.rstrip("/")
        transport_error = None
        response = None
        try:
            response = await client.request(method, f"{base}{path}", **kwargs)
        except httpx.RequestError as exc:
            transport_error = exc
            self._consecutive_failures += 1
        else:
            if response.status_code in GATEWAY_STATUSES:
                self._consecutive_failures += 1
            else:
                self._consecutive_failures = 0
                return response, base
        alternate = _alternate_official_base(base)
        if self._consecutive_failures >= FAILOVER_THRESHOLD and alternate and alternate != base:
            try:
                alt_response = await client.request(method, f"{alternate}{path}", **kwargs)
            except httpx.RequestError:
                # Neither official host answered: stay put and retry later.
                self._consecutive_failures = 0
                if transport_error is not None:
                    raise transport_error
                return response, base
            self._consecutive_failures = 0
            return alt_response, alternate
        if transport_error is not None:
            raise transport_error
        return response, base

    def _adopt_base_url(self, base_url):
        """Adopt a failover host for later requests and durable persistence."""
        self._base_url = base_url.rstrip("/")
        self.env["WECHAT_BASE_URL"] = self._base_url
        self._persist_base_url()

    def _persist_base_url(self):
        """Rewrite the stored base URL, preserving the existing secrets.

        Best effort only: the in-memory switch already applies, and secrets are
        never logged. Nothing is written when no credential file exists (e.g.
        before authorization) or when the token/user are not yet known.
        """
        if self._credential_file is None or not self._credential_file.is_file():
            return
        token = self.setting("WECHAT_BOT_TOKEN")
        user_id = self.setting("WECHAT_ILINK_USER_ID")
        if not token or not user_id:
            return
        try:
            self._save_credentials({"WECHAT_BOT_TOKEN": token,
                                    "WECHAT_ILINK_USER_ID": user_id,
                                    "WECHAT_BASE_URL": self._base_url})
        except OSError:
            return

    def status(self):
        status = super().status()
        if status.configured:
            status.detail = "iLink 授权凭据已加载；可收发消息"
        else:
            status.detail = "个人微信 iLink 已支持二维码授权，等待扫码"
        return status

    async def start_login(self, client=None):
        own = client is None
        client = client or httpx.AsyncClient(timeout=15, follow_redirects=False)
        try:
            response = await client.post(
                f"{DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode?bot_type=3",
                json={"local_token_list": []},
                # The official client sends the iLink app headers on EVERY
                # request, including QR creation; without them Tencent embeds a
                # stale interface version in the QR and WeChat refuses the scan.
                headers={"Content-Type": "application/json",
                    "AuthorizationType": "ilink_bot_token",
                    "X-WECHAT-UIN": base64.b64encode(str(secrets.randbelow(2**32)).encode()).decode(),
                    **_app_headers()})
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("INVALID_QR_RESPONSE")
            qr = data.get("qrcode")
            image = data.get("qrcode_img_content")
            if not isinstance(qr, str) or not qr or not isinstance(image, str) or not image:
                raise ValueError("INVALID_QR_RESPONSE")
            self._login = {"qrcode": qr, "image": image, "base_url": DEFAULT_BASE_URL,
                           "verify_code": None, "started_at": datetime.now(timezone.utc).isoformat()}
            # PNG is rendered consistently by browsers and avoids SVG data URI
            # parsing differences (the previous SVG appeared as a broken image
            # in the deployed Mission Control page). Encoding must not import
            # qrcode.image.pil; that backend is absent unless Pillow is installed.
            png = _qr_png_bytes(image, border=4)
            qr_data = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            return {"state": "WAITING_FOR_SCAN", "qr_image": qr_data}
        except httpx.TimeoutException:
            return {"state": "FAILED", "error": "TIMEOUT"}
        except httpx.RequestError:
            return {"state": "FAILED", "error": "TRANSPORT_ERROR"}
        except (ValueError, KeyError):
            return {"state": "FAILED", "error": "INVALID_QR_RESPONSE"}
        finally:
            if own:
                await client.aclose()

    async def poll_login(self, client=None):
        if not self._login:
            return {"state": "NOT_STARTED"}
        if not self._poll_lock.acquire(blocking=False):
            return {"state": "wait"}
        try:
            return await self._poll_login_locked(client)
        finally:
            self._poll_lock.release()

    async def _poll_login_locked(self, client=None):
        own = client is None
        client = client or httpx.AsyncClient(timeout=35, follow_redirects=False)
        try:
            params = {"qrcode": self._login["qrcode"]}
            if self._login.get("verify_code"):
                params["verify_code"] = self._login["verify_code"]
            response, base_url = await self._request_with_failover(
                client, "GET", "/ilink/bot/get_qrcode_status", self._login["base_url"],
                params=params, headers=_app_headers())
            if base_url != self._login["base_url"] and response.status_code not in GATEWAY_STATUSES:
                # Another official host answered: keep polling it for this QR.
                self._login["base_url"] = base_url
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                # Upstream occasionally answers with a non-JSON body (a gateway
                # 5xx page) while the QR itself is still valid. Treat it exactly
                # like a transport blip: keep the login flow alive and retry,
                # never destroy the operator's in-progress scan.
                return {"state": "RETRY", "error": "INVALID_LOGIN_RESPONSE"}
            state = data.get("status")
            if state == "scaned_but_redirect" and data.get("redirect_host"):
                host = str(data["redirect_host"]).removeprefix("https://")
                self._login["base_url"] = _ilink_url("https://" + host)
            if state == "need_verifycode":
                return {"state": "need_verifycode"}
            if state == "confirmed":
                token, user_id = data.get("bot_token"), data.get("ilink_user_id")
                try:
                    base_url = _ilink_url(data.get("baseurl") or self._login["base_url"])
                except ValueError:
                    # 逆向克隆防护：error 串会作为 502 detail 到达客户（host/start），
                    # 不回显上游返回的原始 baseurl。
                    return {"state": "FAILED", "error": "INVALID_LOGIN_RESPONSE"}
                if not isinstance(token, str) or not token or not isinstance(user_id, str) or not user_id:
                    missing = [name for name, value in (("bot_token", token),
                                                        ("ilink_user_id", user_id))
                               if not value]
                    return {"state": "FAILED",
                            "error": f"INVALID_LOGIN_RESPONSE:missing={','.join(missing)}"}
                # Keep secrets only in this process. Production persistence is via
                # the protected provider env file configured by the operator.
                values = {"WECHAT_BOT_TOKEN": token, "WECHAT_ILINK_USER_ID": user_id,
                          "WECHAT_BASE_URL": base_url}
                try:
                    self._save_credentials(values)
                except OSError:
                    return {"state": "FAILED", "error": "SECURE_STORAGE_UNAVAILABLE"}
                self.env.update(values)
                self._base_url = base_url.rstrip("/")
                self._login = None
                return {"state": "AUTHORIZED", "account": user_id}
            if state == "binded_redirect" and self.configured():
                # Official semantics: the bot is already bound to this client
                # and existing credentials remain valid — nothing to redo.
                return {"state": "AUTHORIZED", "account": self.setting("WECHAT_ILINK_USER_ID")}
            return {"state": state if state in {"wait", "scaned", "expired", "need_verifycode", "verify_code_blocked", "binded_redirect"} else f"WAITING_FOR_SCAN:upstream={state}"}
        except httpx.TimeoutException:
            return {"state": "WAITING_FOR_SCAN"}
        except httpx.RequestError:
            # Transient transport trouble during status polling is retryable:
            # the QR itself is still valid, so never terminate the login flow.
            return {"state": "RETRY", "error": "TRANSPORT_ERROR"}
        except ValueError:
            # A malformed/non-JSON status body is transient upstream noise, not a
            # reason to abort: the QR stays valid, so retry instead of failing.
            return {"state": "RETRY", "error": "INVALID_LOGIN_RESPONSE"}
        finally:
            if own:
                await client.aclose()

    def submit_verify_code(self, code):
        if not self._login:
            raise ValueError("LOGIN_NOT_STARTED")
        if not isinstance(code, str) or not code.isdigit() or not 4 <= len(code) <= 8:
            raise ValueError("INVALID_VERIFY_CODE")
        self._login["verify_code"] = code
        return {"state": "VERIFY_CODE_SUBMITTED"}

    def _headers(self):
        token = self.setting("WECHAT_BOT_TOKEN")
        if not token:
            raise ValueError("WECHAT_NOT_AUTHORIZED")
        return {"Content-Type": "application/json", "AuthorizationType": "ilink_bot_token",
                "Authorization": f"Bearer {token}", **_app_headers(),
                "X-WECHAT-UIN": base64.b64encode(str(secrets.randbelow(2**32)).encode()).decode()}

    async def fetch_updates(self, client=None):
        self.require_configured()
        own = client is None
        client = client or httpx.AsyncClient(timeout=40, follow_redirects=False)
        try:
            response, base_url = await self._request_with_failover(
                client, "POST", "/ilink/bot/getupdates", self._base_url,
                headers=self._headers(), json={"get_updates_buf": self._updates_buf, "base_info": BASE_INFO})
            if base_url != self._base_url and response.status_code not in GATEWAY_STATUSES:
                self._adopt_base_url(base_url)
            if response.status_code in GATEWAY_STATUSES:
                # getupdates is a long poll: when no message is pending the origin
                # holds the connection until the gateway gives up (Cloudflare 524,
                # or 502/503/504). That is normal long-poll behaviour — the same
                # thing the official client treats as an empty "wait" — so retry
                # instead of reporting a channel failure.
                return {"state": "RETRY", "error": "GATEWAY_TIMEOUT", "messages": []}
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                return {"state": "FAILED", "error": "INVALID_UPSTREAM_RESPONSE", "messages": []}
            if not isinstance(data, dict):
                return {"state": "FAILED", "error": "INVALID_UPSTREAM_RESPONSE", "messages": []}
            if data.get("ret", 0) != 0 or data.get("errcode", 0) != 0:
                return {"state": "FAILED", "error": "UPSTREAM_REJECTED", "messages": []}
            try:
                messages = self.parse_inbound(data)
            except ValueError:
                return {"state": "FAILED", "error": "INVALID_UPSTREAM_RESPONSE", "messages": []}
            next_buf = data.get("get_updates_buf")
            self._pending_updates_buf = next_buf if isinstance(next_buf, str) and next_buf else self._updates_buf
            return {"state": "OK", "messages": messages,
                    "get_updates_buf": self._pending_updates_buf}
        except httpx.TimeoutException:
            return {"state": "RETRY", "error": "TIMEOUT", "messages": []}
        except httpx.RequestError:
            return {"state": "RETRY", "error": "TRANSPORT_ERROR", "messages": []}
        finally:
            if own:
                await client.aclose()

    def acknowledge_updates(self):
        """Commit the returned long-poll cursor after the caller durably stores messages."""
        if self._pending_updates_buf is not None:
            self._updates_buf = self._pending_updates_buf
            self._pending_updates_buf = None

    def parse_inbound(self, payload):
        if not isinstance(payload, dict) or not isinstance(payload.get("msgs", []), list):
            raise ValueError("Invalid WeChat iLink update payload")
        result = []
        for msg in payload.get("msgs", []):
            if not isinstance(msg, dict):
                continue
            sender = msg.get("from_user_id")
            items = msg.get("item_list", [])
            if not isinstance(sender, str) or not sender or not isinstance(items, list):
                continue
            texts = [item.get("text_item", {}).get("text") for item in items
                     if isinstance(item, dict) and item.get("type") == 1
                     and isinstance(item.get("text_item"), dict)]
            # Preserve the user's exact text for the audit record and prompt.
            # Whitespace is used only to decide whether the message is empty.
            text = "\n".join(value for value in texts if isinstance(value, str))
            if not text.strip():
                continue
            message_id = str(msg.get("message_id") or msg.get("client_id") or "")
            if not message_id:
                continue
            timestamp = msg.get("create_time_ms")
            try:
                received_at = datetime.fromtimestamp(int(timestamp) / 1000, timezone.utc).isoformat()
            except (TypeError, ValueError, OSError, OverflowError):
                received_at = datetime.now(timezone.utc).isoformat()
            result.append(InboundMessage(channel=self.name, external_id=message_id,
                sender=sender, recipient=self.setting("WECHAT_ILINK_USER_ID"), text=text,
                received_at=received_at, raw=msg,
                metadata={"context_token": msg.get("context_token")}))
        return result

    async def send(self, reply: OutboundReply, client=None):
        self.require_configured()
        if reply.channel != self.name or not reply.recipient.strip() or not reply.text:
            raise ValueError("A WeChat reply needs a recipient and text")
        own = client is None
        client = client or httpx.AsyncClient(timeout=20, follow_redirects=False)
        msg = {"from_user_id": "", "to_user_id": reply.recipient,
               "client_id": secrets.token_urlsafe(18), "message_type": 2,
               "message_state": 2, "item_list": [{"type": 1, "text_item": {"text": reply.text}}]}
        if reply.metadata.get("context_token"):
            msg["context_token"] = reply.metadata["context_token"]
        try:
            response, base_url = await self._request_with_failover(
                client, "POST", "/ilink/bot/sendmessage", self._base_url,
                headers=self._headers(), json={"msg": msg, "base_info": BASE_INFO})
            if base_url != self._base_url and response.status_code not in GATEWAY_STATUSES:
                self._adopt_base_url(base_url)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                return {"channel": self.name, "status": "FAILED", "http_status": response.status_code,
                        "error": "INVALID_UPSTREAM_RESPONSE"}
            if not isinstance(data, dict):
                return {"channel": self.name, "status": "FAILED", "http_status": response.status_code,
                        "error": "INVALID_UPSTREAM_RESPONSE"}
            if data.get("ret", 0) != 0:
                return {"channel": self.name, "status": "FAILED", "http_status": response.status_code,
                        "error": "UPSTREAM_REJECTED"}
            return {"channel": self.name, "status": "SENT", "http_status": response.status_code,
                    "external_id": str(data.get("message_id") or msg["client_id"]), "error": None}
        except httpx.TimeoutException:
            return {"channel": self.name, "status": "FAILED", "http_status": None, "error": "TIMEOUT"}
        except httpx.RequestError:
            return {"channel": self.name, "status": "FAILED", "http_status": None, "error": "TRANSPORT_ERROR"}
        finally:
            if own:
                await client.aclose()
