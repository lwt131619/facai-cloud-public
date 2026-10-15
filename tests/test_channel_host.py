"""Channel HOSTING: schema, state machine, self-service endpoints, per-owner runtime.

docs/CHANNEL_HOST_DESIGN.md 切片一 + 切片二（per-owner 监控）+ 切片四（WhatsApp
配对码宿主）。Proven here:

* the channel_host_bindings state machine only allows BINDING→ACTIVE→UNBOUND
  (+ UNBOUND→BINDING reopen as a SAME-ROW update); everything else raises;
* bindings are owner-isolated: alice's row is invisible to bob at both the
  store and the API layer, and a stop never touches another owner's row;
* wechat host start returns the provider QR payload VERBATIM from the OWNER'S
  OWN provider instance, records a BINDING row in the owner's own namespace,
  and a failed QR start leaves no row behind;
* whatsapp host start（配对码，免扫码）: the customer's phone reaches the
  OWNER'S OWN bridge session (stubbed, offline); the 8-digit pairing code is
  returned VERBATIM, a BINDING row is recorded, malformed phones are 422, a
  bridge failure leaves no row, and a bridge session linked to a DIFFERENT
  number is a 409 -- never silently bound;
* whatsapp ACTIVE is only reached through the login poll when the bridge
  itself reports the owner's session LINKED; pairing answers never activate;
* whatsapp stop is a real revoke on the bridge (logout), audited in the
  HOST_BINDING_STOPPED event, and survives bridge failure with the row still
  honestly UNBOUND;
* host stop is idempotent; stopping what was never started is a 404;
* the tenant-403 semantics of the STATION endpoints are unchanged while
  tenants can drive their own /my/channels/*/host endpoints.

切片二（per-owner 监控，既有）：

* QR 确认（host/login 轮询到 AUTHORIZED）把凭据写进
  ``providers/hosting/<owner>/wechat.env``（绝不写站长的 providers/wechat.env），
  行迁移 BINDING→ACTIVE，审计 HOST_BINDING_ACTIVATED，并启动该 owner 的
  monitor 任务；监控收到的消息建出的任务/消息行 owner 归属正确；
* host/stop 之后轮询真的停止（fetch 次数不再增长、迟到的消息不再入库）；
* 上游连续 3 次拒绝（凭据失效）后 monitor 停止并审计 HOST_MONITOR_STOPPED；
* 站长实例与客户实例并存互不串扰：同一 external_id 在两个账号下各成一行、
  各归其主，出码互不顶掉，凭据文件各落各家，ack 各走各的账号；
* 进程重启后启动扫描把 ACTIVE 绑定的 monitor 与凭据（从盘）拉回来。
"""

import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

import facai.channels as channels_module
import facai.channels.wechat as wechat_module
from facai.api import HOST_WECHAT_CREDENTIAL_FAILURE_LIMIT, create_app
from facai.channels import InboundMessage
from facai.channels.whatsapp import WhatsAppBridgeError, WhatsAppHostBridge
from facai.store import Store

PASSWORD = "host-pass"
ALICE_AUTH = {"Authorization": "Basic " + base64.b64encode(b"alice:alice-pass-123").decode()}
BOB_AUTH = {"Authorization": "Basic " + base64.b64encode(b"bob:bob-pass-1234").decode()}
ADMIN_AUTH = {"Authorization": "Basic " + base64.b64encode(f"admin:{PASSWORD}".encode()).decode()}

QR_PAYLOAD = {"state": "WAITING_FOR_SCAN", "qr_image": "data:image/png;base64,QR0"}
PHONE = "8613800000000"
PAIR_CODE = "ABCD1234"  # 8-digit pairing code, as Baileys mints them


class FakeWeChat:
    """Station-side stand-in: the ADMIN QR pipeline's provider (registry slot).

    configured() stays False so nothing else in the app believes a wechat
    credential exists -- the fake fabricates a QR answer, never a connection.
    The PER-OWNER host pipeline does NOT read this slot any more (wave 2); it
    builds its own instances behind ``facai.api.WeChatProvider``.
    """

    name = "wechat"
    implemented = True
    required_env = ()

    def __init__(self, result=None):
        self.result = result if result is not None else dict(QR_PAYLOAD)
        self.calls = 0

    def configured(self):
        return False

    def missing(self):
        return []

    def status(self):
        from facai.channels.base import ChannelStatus
        return ChannelStatus(self.name, self.implemented, False, "UNCONFIGURED",
                             [], [], False, False, "fake")

    async def start_login(self, client=None):
        self.calls += 1
        return dict(self.result)


def _inbound(external_id="201", sender="friend-1", text="帮我把部署步骤整理一下",
             account="bot-alice"):
    return InboundMessage(channel="wechat", external_id=external_id, sender=sender,
                          text=text, received_at="2026-10-06T00:00:00+00:00",
                          recipient=account, raw={}, metadata={})


class StubHostWeChat:
    """Per-owner stand-in for ``facai.api.WeChatProvider`` -- zero network.

    One instance per owner (as the real factory builds); ``script`` drives
    start_login / poll_login / fetch_updates answers. A poll answer of
    AUTHORIZED loads the fake token into the instance env, so ``configured()``
    flips exactly like the real per-owner instance after a QR confirmation.
    """

    name = "wechat"
    implemented = True
    required_env = ()

    def __init__(self, script=None):
        self.env = {}
        self.script = dict(script or {})
        self.start_calls = 0
        self.poll_calls = 0
        self.fetch_calls = 0
        self.sends = []

    @classmethod
    def for_owner_state(cls, state_dir):
        return cls()

    def configured(self):
        return bool(self.env.get("WECHAT_BOT_TOKEN"))

    def setting(self, key):
        return self.env.get(key) or ""

    def submit_verify_code(self, code):
        return {"state": "VERIFY_CODE_SUBMITTED"}

    async def start_login(self, client=None):
        self.start_calls += 1
        return dict(self.script.get("start", QR_PAYLOAD))

    async def poll_login(self, client=None):
        self.poll_calls += 1
        sequence = self.script.get("poll") or [{"state": "AUTHORIZED", "account": "bot-alice"}]
        result = dict(sequence[min(self.poll_calls - 1, len(sequence) - 1)])
        if result.get("state") == "AUTHORIZED":
            self.env["WECHAT_BOT_TOKEN"] = "token-bot-alice"
            self.env["WECHAT_ILINK_USER_ID"] = result.get("account", "bot-alice")
        return result

    async def fetch_updates(self, client=None):
        self.fetch_calls += 1
        queue = self.script.get("updates")
        if queue:
            # Exhausted queue REPEATS ITS LAST ENTRY: a dead credential keeps
            # rejecting forever, and an idle account keeps answering empty --
            # scripts that must go quiet end on an explicit OK-empty entry.
            entry = queue[min(self.fetch_calls, len(queue)) - 1]
            if entry.get("state") == "OK" and entry.get("messages"):
                return {"state": "OK", "messages": list(entry["messages"]),
                        "get_updates_buf": f"buf-{self.fetch_calls}"}
            return dict(entry)
        return {"state": "OK", "messages": []}

    def acknowledge_updates(self):
        pass

    async def send(self, reply, client=None):
        self.sends.append(reply)
        return {"channel": self.name, "status": "SENT",
                "external_id": f"out-{len(self.sends)}"}


class StubWhatsAppBridge:
    """Offline stand-in for ``facai.api.WhatsAppHostBridge`` (the VPS bridge's
    signed admin API). Zero network, zero Baileys.

    ``script`` drives the answers: ``pair`` (verbatim dict result),
    ``pair_error`` (WhatsAppBridgeError reason), ``status`` (list; the last
    entry repeats, like the bridge's live session state), ``status_error``,
    ``logout_error``. Every command is recorded for assertions.
    """

    name = "whatsapp-host-bridge"
    required_env = ()

    # The endpoint validates through the REAL classmethod even when the api
    # module's reference is patched, so phone gating is always the shipped one.
    normalize_phone = staticmethod(WhatsAppHostBridge.normalize_phone)

    def __init__(self, script=None):
        self.script = dict(script or {})
        self.pairs = []        # (owner, phone)
        self.status_calls = 0
        self.logouts = []      # owner

    def configured(self):
        return True

    async def start_pairing(self, owner, phone, client=None):
        self.pairs.append((owner, phone))
        if self.script.get("pair_error"):
            raise WhatsAppBridgeError(self.script["pair_error"])
        return dict(self.script.get("pair")
                    or {"state": "PAIRING_CODE", "code": PAIR_CODE, "phone": phone})

    async def session_status(self, owner, client=None):
        self.status_calls += 1
        if self.script.get("status_error"):
            raise WhatsAppBridgeError(self.script["status_error"])
        queue = self.script.get("status") or [
            {"state": "PAIRING", "code": PAIR_CODE, "phone": PHONE}]
        return dict(queue[min(self.status_calls - 1, len(queue) - 1)])

    async def logout(self, owner, client=None):
        self.logouts.append(owner)
        if self.script.get("logout_error"):
            raise WhatsAppBridgeError(self.script["logout_error"])
        return {"state": "LOGGED_OUT"}


def _app(monkeypatch, tmp_path, host_script=None, host_stub=True,
         bridge_script=None, whatsapp_bridge=True):
    """Boot the app with alice+bob registered.

    ``host_stub=True`` (default) replaces the PER-OWNER provider factory with
    ``StubHostWeChat`` so host endpoints never touch the network; the station's
    registry provider is untouched. ``host_stub=False`` keeps the real
    per-owner ``WeChatProvider`` for the end-to-end credential-file tests,
    which must also redirect the module's httpx client themselves.

    ``whatsapp_bridge=True`` (default) points FACAI_WHATSAPP_BRIDGE_URL at a
    fake address and replaces ``facai.api.WhatsAppHostBridge`` with
    ``StubWhatsAppBridge(bridge_script)`` (reachable as
    ``app.state.host_whatsapp_bridge``); ``False`` leaves the real client and
    no URL, so the endpoints answer their honest 503 unconfigured path.
    """
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    monkeypatch.setenv("FACAI_WECHAT_POLL_INTERVAL", "0.02")
    monkeypatch.setenv("FACAI_WECHAT_ERROR_BACKOFF", "0.02")
    monkeypatch.setenv("FACAI_HOST_WECHAT_POLL_INTERVAL", "0.02")
    if host_stub:
        monkeypatch.setattr("facai.api.WeChatProvider",
                            _stub_provider_class(host_script))
    bridge_stub = None
    if whatsapp_bridge:
        monkeypatch.setenv("FACAI_WHATSAPP_BRIDGE_URL", "http://bridge.test")
        bridge_stub = StubWhatsAppBridge(bridge_script)
        monkeypatch.setattr("facai.api.WhatsAppHostBridge",
                            lambda env=None, base_url=None: bridge_stub)
    else:
        monkeypatch.delenv("FACAI_WHATSAPP_BRIDGE_URL", raising=False)
    app = create_app(tmp_path)
    # Pre-seed the lazy client cache so tests can always read the stub back
    # from app.state, even on paths that never reach the factory (422/409).
    app.state.host_whatsapp_bridge = bridge_stub
    # Register through a throwaway client: its response sets an authenticated
    # session cookie, and a session-bearing POST would hit the CSRF gate. The
    # tests below authenticate with explicit Basic headers on a clean client.
    with TestClient(app) as bootstrap:
        for username, password in (("alice", "alice-pass-123"), ("bob", "bob-pass-1234")):
            r = bootstrap.post("/api/v1/auth/register",
                               json={"username": username, "password": password})
            assert r.status_code in (201, 409), r.text
    return app, TestClient(app)


def _stub_provider_class(script):
    class _ScriptedStub(StubHostWeChat):
        def __init__(self, env=None, state_dir=None):
            super().__init__(script=script)

        @classmethod
        def for_owner_state(cls, state_dir):
            return cls(state_dir=state_dir)

    return _ScriptedStub


def _wait_until(predicate, timeout=10.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


# --- store: state machine ---------------------------------------------------------


def test_state_machine_happy_path_bind_activate_stop_reopen(tmp_path):
    store = Store(tmp_path)
    row = store.transition_host_binding("alice", "wechat", "BINDING",
                                        session_dir="providers/hosting/alice/wechat",
                                        display_name="个人微信")
    assert row["state"] == "BINDING"
    assert row["session_dir"] == "providers/hosting/alice/wechat"
    created_id, created_at = row["id"], row["created_at"]

    row = store.transition_host_binding("alice", "wechat", "ACTIVE")
    assert row["state"] == "ACTIVE"
    # A transition that carries no namespace keeps the row's own (COALESCE).
    assert row["session_dir"] == "providers/hosting/alice/wechat"
    assert row["id"] == created_id and row["created_at"] == created_at

    row = store.transition_host_binding("alice", "wechat", "UNBOUND")
    assert row["state"] == "UNBOUND"
    assert row["session_dir"] == "providers/hosting/alice/wechat"

    # Reopen is a SAME-ROW write back to BINDING: fresh session_dir, original
    # id and created_at survive, so the revocation history stays queryable.
    reopened = store.transition_host_binding("alice", "wechat", "BINDING",
                                             session_dir="providers/hosting/alice/wechat/v2")
    assert reopened["state"] == "BINDING"
    assert reopened["id"] == created_id
    assert reopened["created_at"] == created_at
    assert reopened["session_dir"] == "providers/hosting/alice/wechat/v2"


def test_state_machine_illegal_transitions_raise(tmp_path):
    store = Store(tmp_path)
    # A binding that was never started cannot jump to a later state.
    with pytest.raises(ValueError):
        store.transition_host_binding("alice", "wechat", "ACTIVE")
    with pytest.raises(ValueError):
        store.transition_host_binding("alice", "wechat", "UNBOUND")
    store.transition_host_binding("alice", "wechat", "BINDING")
    with pytest.raises(ValueError):
        store.transition_host_binding("alice", "wechat", "WHATEVER")  # unknown state
    store.transition_host_binding("alice", "wechat", "ACTIVE")
    with pytest.raises(ValueError):
        store.transition_host_binding("alice", "wechat", "BINDING")  # ACTIVE->BINDING
    store.transition_host_binding("alice", "wechat", "UNBOUND")
    with pytest.raises(ValueError):
        store.transition_host_binding("alice", "wechat", "ACTIVE")  # UNBOUND->ACTIVE


def test_state_machine_same_state_transitions_are_noops(tmp_path):
    store = Store(tmp_path)
    first = store.transition_host_binding("alice", "wechat", "BINDING",
                                          session_dir="providers/hosting/alice/wechat")
    # Same-state requests return the row unchanged -- "start while already
    # BINDING" is idempotent, never a fake second QR lifecycle.
    again = store.transition_host_binding("alice", "wechat", "BINDING",
                                          session_dir="providers/hosting/alice/wechat")
    assert again == first
    store.transition_host_binding("alice", "wechat", "ACTIVE")
    active = store.transition_host_binding("alice", "wechat", "ACTIVE")
    assert active["state"] == "ACTIVE"


def test_host_bindings_list_by_owner_is_scoped(tmp_path):
    store = Store(tmp_path)
    store.transition_host_binding("alice", "wechat", "BINDING",
                                  session_dir="providers/hosting/alice/wechat")
    store.transition_host_binding("alice", "whatsapp", "BINDING",
                                  session_dir="providers/hosting/alice/whatsapp")
    store.transition_host_binding("bob", "wechat", "BINDING",
                                  session_dir="providers/hosting/bob/wechat")
    assert [row["channel"] for row in store.host_bindings_for("alice")] == \
        ["wechat", "whatsapp"]  # ORDER BY channel
    assert [row["channel"] for row in store.host_bindings_for("bob")] == ["wechat"]
    assert store.host_bindings_for("carol") == []
    assert store.host_binding("alice", "wechat")["owner_id"] == "alice"
    assert store.host_binding("alice", "telegram") is None


# --- API: wechat host start returns the provider QR verbatim ----------------------


def test_wechat_host_start_returns_qr_and_records_binding(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)

    r = client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    stub = app.state.host_wechat_providers["alice"]
    assert stub.start_calls == 1
    # QR data is returned EXACTLY as the provider built it -- no reshaping.
    assert body["qr"] == QR_PAYLOAD
    assert body["state"] == "BINDING"
    assert body["channel"] == "wechat"
    assert body["session_dir"] == "providers/hosting/alice/wechat"
    row = app.state.store.host_binding("alice", "wechat")
    assert row["state"] == "BINDING"
    assert row["display_name"] == "个人微信"
    # A second start while BINDING is idempotent, not a second QR lifecycle row.
    r2 = client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
    assert r2.status_code == 200
    assert app.state.store.host_binding("alice", "wechat")["id"] == row["id"]
    assert stub.start_calls == 2  # a fresh QR is served, the row is reused


def test_wechat_host_start_failure_leaves_no_row(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path,
                       host_script={"start": {"state": "FAILED", "error": "TIMEOUT"}})
    r = client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
    assert r.status_code == 502
    assert app.state.store.host_binding("alice", "wechat") is None


def test_wechat_host_start_conflicts_while_active(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    store = app.state.store
    store.transition_host_binding("alice", "wechat", "BINDING",
                                  session_dir="providers/hosting/alice/wechat")
    store.transition_host_binding("alice", "wechat", "ACTIVE")
    r = client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
    assert r.status_code == 409
    assert store.host_binding("alice", "wechat")["state"] == "ACTIVE"


# --- API: whatsapp pairing-code host (免扫码绑定) ----------------------------------


def _pair(client, phone=PHONE, headers=ALICE_AUTH):
    return client.post("/api/v1/my/channels/whatsapp/host/start",
                       json={"phone": phone}, headers=headers)


def test_whatsapp_host_start_returns_pairing_code_and_records_binding(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    stub = app.state.host_whatsapp_bridge

    r = _pair(client)
    assert r.status_code == 200, r.text
    body = r.json()
    # The pairing code reaches the customer VERBATIM, never reshaped or cached.
    assert body["pairing"] == {"state": "PAIRING_CODE", "code": PAIR_CODE, "phone": PHONE}
    assert body["state"] == "BINDING"
    assert body["channel"] == "whatsapp"
    assert body["session_dir"] == "providers/hosting/alice/whatsapp"
    assert stub.pairs == [("alice", PHONE)]
    row = app.state.store.host_binding("alice", "whatsapp")
    assert row["state"] == "BINDING"
    assert row["display_name"] == "个人 WhatsApp"

    # A second start while BINDING is idempotent on the ROW (same lifecycle)
    # but mints a fresh code: codes are single-use and short-lived.
    r2 = _pair(client)
    assert r2.status_code == 200
    assert r2.json()["pairing"]["code"] == PAIR_CODE
    assert app.state.store.host_binding("alice", "whatsapp")["id"] == row["id"]
    assert stub.pairs == [("alice", PHONE), ("alice", PHONE)]
    events = [e["type"] for e in app.state.store.channel_events(message_id="host:alice")]
    assert events.count("HOST_BINDING_STARTED") == 1


@pytest.mark.parametrize("bad_phone", ["", "   ", "abc", "86138", "1" * 16, "86-13-38"])
def test_whatsapp_host_start_rejects_malformed_phone(monkeypatch, tmp_path, bad_phone):
    app, client = _app(monkeypatch, tmp_path)
    stub = app.state.host_whatsapp_bridge
    r = _pair(client, phone=bad_phone)
    assert r.status_code == 422
    assert "7-15" in r.json()["detail"]
    assert app.state.store.host_binding("alice", "whatsapp") is None
    assert stub.pairs == []  # the bridge never sees a malformed phone
    # A missing phone field is the same honest 422, not a bridge call.
    assert client.post("/api/v1/my/channels/whatsapp/host/start",
                       headers=ALICE_AUTH).status_code == 422
    assert stub.pairs == []


def test_whatsapp_host_start_bridge_failure_leaves_no_row(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path,
                       bridge_script={"pair_error": "BRIDGE_UNREACHABLE"})
    r = _pair(client)
    assert r.status_code == 502
    assert "BRIDGE_UNREACHABLE" in r.json()["detail"]
    # Fail-closed: no bridge session, no BINDING row pretending one exists.
    assert app.state.store.host_binding("alice", "whatsapp") is None
    assert [e["type"] for e in
            app.state.store.channel_events(message_id="host:alice")] == []


def test_whatsapp_host_start_conflicts_while_active(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    stub = app.state.host_whatsapp_bridge
    store = app.state.store
    store.transition_host_binding("alice", "whatsapp", "BINDING",
                                  session_dir="providers/hosting/alice/whatsapp")
    store.transition_host_binding("alice", "whatsapp", "ACTIVE")
    r = _pair(client)
    assert r.status_code == 409
    assert store.host_binding("alice", "whatsapp")["state"] == "ACTIVE"
    assert stub.pairs == []  # no fresh pairing code once hosting is live


def test_whatsapp_host_start_refuses_session_linked_to_other_number(monkeypatch, tmp_path):
    """桥上已有该 owner 的会话但链接的是别的号码：绝不静默绑定，也绝不发码。"""
    app, client = _app(monkeypatch, tmp_path,
                       bridge_script={"pair": {"state": "LINKED",
                                               "phone": "8613999999999"}})
    r = _pair(client)
    assert r.status_code == 409
    assert app.state.store.host_binding("alice", "whatsapp") is None


def test_whatsapp_host_start_with_same_number_already_linked_stays_binding(monkeypatch, tmp_path):
    """同号已在链：诚实进 BINDING（无码可输），由 login 轮询从桥侧确认 ACTIVE。"""
    app, client = _app(monkeypatch, tmp_path,
                       bridge_script={"pair": {"state": "LINKED", "phone": PHONE}})
    r = _pair(client)
    assert r.status_code == 200
    assert r.json()["state"] == "BINDING"
    assert r.json()["pairing"] == {"state": "LINKED", "code": None, "phone": PHONE}
    assert app.state.store.host_binding("alice", "whatsapp")["state"] == "BINDING"


def test_whatsapp_host_start_requires_configured_bridge(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path, whatsapp_bridge=False)
    r = _pair(client)
    assert r.status_code == 503
    detail = json.dumps(r.json(), ensure_ascii=False)
    # Honest refusal without naming env vars or bridge internals (source-leak
    # discipline): the customer cannot fix server config either way.
    assert "FACAI_" not in detail and "http" not in detail
    assert app.state.store.host_binding("alice", "whatsapp") is None


def test_whatsapp_host_login_activates_only_on_bridge_link_confirmation(monkeypatch, tmp_path):
    """ACTIVE 只能由桥侧 LINKED 确认：审计 ACTIVATED，重复 login 409。"""
    app, client = _app(monkeypatch, tmp_path,
                       bridge_script={"status": [{"state": "LINKED", "phone": PHONE}]})
    started = _pair(client)
    assert started.json()["state"] == "BINDING"

    confirmed = client.get("/api/v1/my/channels/whatsapp/host/login", headers=ALICE_AUTH)
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert body["state"] == "ACTIVE"
    assert body["login"] == {"state": "LINKED", "phone": PHONE}
    assert app.state.store.host_binding("alice", "whatsapp")["state"] == "ACTIVE"
    events = [e for e in app.state.store.channel_events(message_id="host:alice")
              if e["type"] == "HOST_BINDING_ACTIVATED"]
    assert len(events) == 1
    assert events[0]["payload"]["method"] == "PAIRING_CODE"
    assert events[0]["payload"]["account"] == PHONE

    # Nothing is in flight any more: polling again is a 409, never a re-bind.
    assert client.get("/api/v1/my/channels/whatsapp/host/login",
                      headers=ALICE_AUTH).status_code == 409


def test_whatsapp_host_login_stays_binding_while_pairing(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)  # default status: PAIRING + fresh code
    _pair(client)
    r = client.get("/api/v1/my/channels/whatsapp/host/login", headers=ALICE_AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "BINDING"  # a pairing answer NEVER activates
    assert body["login"]["code"] == PAIR_CODE
    assert app.state.store.host_binding("alice", "whatsapp")["state"] == "BINDING"
    assert [e["type"] for e in app.state.store.channel_events(message_id="host:alice")] == \
        ["HOST_BINDING_STARTED"]


def test_whatsapp_host_login_requires_binding_row(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert client.get("/api/v1/my/channels/whatsapp/host/login",
                      headers=ALICE_AUTH).status_code == 409
    assert app.state.host_whatsapp_bridge.status_calls == 0  # fail before the bridge


def test_whatsapp_host_login_failure_keeps_binding(monkeypatch, tmp_path):
    """轮询桥失败 ≠ 配对失败：502，行留在 BINDING，下次轮询可继续。"""
    app, client = _app(monkeypatch, tmp_path,
                       bridge_script={"status_error": "BRIDGE_TIMEOUT"})
    _pair(client)
    r = client.get("/api/v1/my/channels/whatsapp/host/login", headers=ALICE_AUTH)
    assert r.status_code == 502
    assert app.state.store.host_binding("alice", "whatsapp")["state"] == "BINDING"


def test_whatsapp_host_stop_logs_out_bridge_session_and_audits(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    stub = app.state.host_whatsapp_bridge
    _pair(client)
    stopped = client.post("/api/v1/my/channels/whatsapp/host/stop", headers=ALICE_AUTH)
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "UNBOUND"
    assert stub.logouts == ["alice"]  # a REAL revoke: the bridge unlinks the number
    events = [e for e in app.state.store.channel_events(message_id="host:alice")
              if e["type"] == "HOST_BINDING_STOPPED"]
    assert len(events) == 1
    assert events[0]["payload"]["bridge_logout"] == "OK"
    # Idempotent repeat: no second logout, row untouched.
    again = client.post("/api/v1/my/channels/whatsapp/host/stop", headers=ALICE_AUTH)
    assert again.status_code == 200
    assert stub.logouts == ["alice"]
    after = client.get("/api/v1/my/channels/whatsapp/host/status", headers=ALICE_AUTH)
    assert after.json()["state"] == "UNBOUND"
    assert after.json()["display_name"] == "个人 WhatsApp"


def test_whatsapp_host_stop_survives_bridge_logout_failure(monkeypatch, tmp_path):
    """桥不可达时解绑依然成立（Facai 状态诚实），审计写明 hygiene 结果。"""
    app, client = _app(monkeypatch, tmp_path,
                       bridge_script={"logout_error": "BRIDGE_UNREACHABLE"})
    _pair(client)
    stopped = client.post("/api/v1/my/channels/whatsapp/host/stop", headers=ALICE_AUTH)
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "UNBOUND"
    events = [e for e in app.state.store.channel_events(message_id="host:alice")
              if e["type"] == "HOST_BINDING_STOPPED"]
    assert events[0]["payload"]["bridge_logout"] == "FAILED:BRIDGE_UNREACHABLE"


def test_whatsapp_host_bindings_are_owner_isolated(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    stub = app.state.host_whatsapp_bridge
    _pair(client)  # alice

    # Bob sees nothing of alice's pairing session or row.
    assert client.get("/api/v1/my/channels/whatsapp/host/status",
                      headers=BOB_AUTH).json()["state"] == "NONE"
    assert client.get("/api/v1/my/channels/whatsapp/host/login",
                      headers=BOB_AUTH).status_code == 409
    assert client.post("/api/v1/my/channels/whatsapp/host/stop",
                       headers=BOB_AUTH).status_code == 404
    assert stub.logouts == []  # bob's failed stop never revokes alice's session
    assert app.state.store.host_binding("alice", "whatsapp")["state"] == "BINDING"

    _pair(client, headers=BOB_AUTH)
    alice_row = app.state.store.host_binding("alice", "whatsapp")
    bob_row = app.state.store.host_binding("bob", "whatsapp")
    assert alice_row["id"] != bob_row["id"]
    assert alice_row["session_dir"] == "providers/hosting/alice/whatsapp"
    assert bob_row["session_dir"] == "providers/hosting/bob/whatsapp"

    client.post("/api/v1/my/channels/whatsapp/host/stop", headers=BOB_AUTH)
    assert stub.logouts == ["bob"]  # only bob's own bridge session is revoked
    assert app.state.store.host_binding("alice", "whatsapp")["state"] == "BINDING"
    assert app.state.store.host_binding("bob", "whatsapp")["state"] == "UNBOUND"


def test_whatsapp_host_start_and_login_require_authentication(monkeypatch, tmp_path):
    _app_, client = _app(monkeypatch, tmp_path)
    assert client.post("/api/v1/my/channels/whatsapp/host/start",
                       json={"phone": PHONE}).status_code == 401
    assert client.get("/api/v1/my/channels/whatsapp/host/login").status_code == 401
    assert client.post("/api/v1/my/channels/whatsapp/host/stop").status_code == 401


def test_host_endpoints_reject_non_hostable_channel(monkeypatch, tmp_path):
    _app_, client = _app(monkeypatch, tmp_path)
    assert client.post("/api/v1/my/channels/telegram/host/start",
                       headers=ALICE_AUTH).status_code == 404
    assert client.get("/api/v1/my/channels/telegram/host/status",
                      headers=ALICE_AUTH).status_code == 404
    assert client.post("/api/v1/my/channels/telegram/host/stop",
                       headers=ALICE_AUTH).status_code == 404


# --- API: status + idempotent stop -------------------------------------------------


def test_host_status_reports_none_then_binding_then_unbound(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)

    empty = client.get("/api/v1/my/channels/wechat/host/status", headers=ALICE_AUTH)
    assert empty.status_code == 200
    assert empty.json()["state"] == "NONE"

    client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
    bound = client.get("/api/v1/my/channels/wechat/host/status", headers=ALICE_AUTH)
    assert bound.json()["state"] == "BINDING"
    assert bound.json()["session_dir"] == "providers/hosting/alice/wechat"

    stopped = client.post("/api/v1/my/channels/wechat/host/stop", headers=ALICE_AUTH)
    assert stopped.status_code == 200
    assert stopped.json()["state"] == "UNBOUND"
    after = client.get("/api/v1/my/channels/wechat/host/status", headers=ALICE_AUTH)
    assert after.json()["state"] == "UNBOUND"


def test_host_stop_is_idempotent_and_404_when_never_started(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    assert client.post("/api/v1/my/channels/wechat/host/stop",
                       headers=ALICE_AUTH).status_code == 404

    client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
    first_stop = client.post("/api/v1/my/channels/wechat/host/stop", headers=ALICE_AUTH)
    assert first_stop.status_code == 200
    assert first_stop.json()["state"] == "UNBOUND"
    row_after_first = app.state.store.host_binding("alice", "wechat")
    for _ in range(3):  # repeated stops stay 200 and stop mutating the row
        r = client.post("/api/v1/my/channels/wechat/host/stop", headers=ALICE_AUTH)
        assert r.status_code == 200
        assert r.json()["state"] == "UNBOUND"
    row_final = app.state.store.host_binding("alice", "wechat")
    assert row_final["state"] == "UNBOUND"
    assert row_final["updated_at"] == row_after_first["updated_at"]


def test_host_lifecycle_writes_audit_events(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
    client.post("/api/v1/my/channels/wechat/host/stop", headers=ALICE_AUTH)
    events = [e["type"] for e in app.state.store.channel_events(message_id="host:alice")]
    assert events == ["HOST_BINDING_STARTED", "HOST_BINDING_STOPPED"]


# --- owner isolation ---------------------------------------------------------------


def test_host_bindings_are_owner_isolated(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)

    bob_status = client.get("/api/v1/my/channels/wechat/host/status", headers=BOB_AUTH)
    assert bob_status.json()["state"] == "NONE"  # bob sees nothing of alice's row
    # Bob stopping his (nonexistent) binding must not touch alice's row.
    assert client.post("/api/v1/my/channels/wechat/host/stop",
                       headers=BOB_AUTH).status_code == 404
    assert app.state.store.host_binding("alice", "wechat")["state"] == "BINDING"

    client.post("/api/v1/my/channels/wechat/host/start", headers=BOB_AUTH)
    alice_row = app.state.store.host_binding("alice", "wechat")
    bob_row = app.state.store.host_binding("bob", "wechat")
    assert alice_row["id"] != bob_row["id"]
    assert alice_row["session_dir"] == "providers/hosting/alice/wechat"
    assert bob_row["session_dir"] == "providers/hosting/bob/wechat"

    client.post("/api/v1/my/channels/wechat/host/stop", headers=BOB_AUTH)
    assert app.state.store.host_binding("alice", "wechat")["state"] == "BINDING"
    assert app.state.store.host_binding("bob", "wechat")["state"] == "UNBOUND"


def test_host_start_and_stop_require_authentication(monkeypatch, tmp_path):
    _app_, client = _app(monkeypatch, tmp_path)
    assert client.post("/api/v1/my/channels/wechat/host/start").status_code == 401
    assert client.get("/api/v1/my/channels/wechat/host/status").status_code == 401
    assert client.post("/api/v1/my/channels/wechat/host/stop").status_code == 401


# --- station semantics unchanged ----------------------------------------------------


def test_tenant_403_semantics_on_station_endpoints_unchanged(monkeypatch, tmp_path):
    app, client = _app(monkeypatch, tmp_path)
    app.state.channels.providers["wechat"] = FakeWeChat()
    # Station QR pipeline and station channel config stay admin-only...
    assert client.post("/api/v1/channels/wechat/login",
                       headers=ALICE_AUTH).status_code == 403
    assert client.get("/api/v1/channels/wechat/login",
                      headers=ALICE_AUTH).status_code == 403
    assert client.get("/api/v1/channels", headers=ALICE_AUTH).status_code == 403
    # ...while tenants drive their OWN hosted-channel endpoints.
    assert client.get("/api/v1/my/channels/wechat/host/status",
                      headers=ALICE_AUTH).status_code == 200
    assert client.post("/api/v1/my/channels/wechat/host/start",
                       headers=ALICE_AUTH).status_code == 200
    # The existing token-binding surface keeps working next to the host rows.
    assert client.get("/api/v1/my/channels", headers=ALICE_AUTH).status_code == 200


# --- wave 2: per-owner credentials, ACTIVE confirm, per-owner monitor --------------

ALICE_UPDATE = {"message_id": 201, "from_user_id": "friend-1",
                "create_time_ms": 1720000000000, "context_token": "ctx-a",
                "item_list": [{"type": 1, "text_item": {"text": "帮我把部署步骤整理一下"}}]}


class SingleAccountILink:
    """One iLink bot account: QR login, confirmations, an update stream, send."""

    def __init__(self, token, account, updates=()):
        self.token = token
        self.account = account
        self.updates = list(updates)
        self.updates_served = 0
        self.login_polls = 0
        self.sent = []

    async def handler(self, request):
        path = request.url.path
        if path.endswith("get_bot_qrcode"):
            return httpx.Response(200, json={"qrcode": "qr-1",
                                             "qrcode_img_content": "https://weixin.qq.com/qr"})
        if path.endswith("get_qrcode_status"):
            self.login_polls += 1
            return httpx.Response(200, json={"status": "confirmed", "bot_token": self.token,
                                             "ilink_user_id": self.account,
                                             "baseurl": "https://ilinkai.weixin.qq.com"})
        if path.endswith("getupdates"):
            self.updates_served += 1
            if self.updates_served <= len(self.updates):
                return httpx.Response(200, json={
                    "ret": 0, "get_updates_buf": f"buf-{self.updates_served}",
                    "msgs": [self.updates[self.updates_served - 1]]})
            return httpx.Response(200, json={"ret": 0, "msgs": []})
        if path.endswith("sendmessage"):
            self.sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ret": 0, "message_id": "out-1"})
        return httpx.Response(404)


class TwoAccountILink:
    """Two iLink bot accounts behind one transport, isolated by bearer token.

    QR requests are answered in arrival order (``qr-1``, ``qr-2``, …) and the
    test maps each id to the account that may confirm it; ``getupdates`` and
    ``sendmessage`` are routed by the Authorization bearer. The two accounts
    deliberately share one external_id sequence (both deliver message_id 101)
    because upstream ids are only unique PER ACCOUNT.
    """

    def __init__(self, accounts):
        # accounts: {token: {"account": ilink_user_id, "updates": [msg, ...]}}
        self.accounts = accounts
        self.qr_order = []
        self.confirmations = {}
        self.updates_served = {}
        self.sends = []          # (token, request json)
        self.login_polls = 0

    async def handler(self, request):
        path = request.url.path
        token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        token = token or None
        if path.endswith("get_bot_qrcode"):
            qr = f"qr-{len(self.qr_order) + 1}"
            self.qr_order.append(qr)
            return httpx.Response(200, json={"qrcode": qr,
                                             "qrcode_img_content": f"https://weixin.qq.com/{qr}"})
        if path.endswith("get_qrcode_status"):
            self.login_polls += 1
            creds = self.confirmations.get(request.url.params.get("qrcode"))
            if creds is None:
                return httpx.Response(200, json={"status": "wait"})
            return httpx.Response(200, json={"status": "confirmed", "bot_token": creds[0],
                                             "ilink_user_id": creds[1],
                                             "baseurl": "https://ilinkai.weixin.qq.com"})
        if path.endswith("getupdates"):
            self.updates_served[token] = self.updates_served.get(token, 0) + 1
            stream = self.accounts[token]
            n = self.updates_served[token]
            if n <= len(stream["updates"]):
                return httpx.Response(200, json={
                    "ret": 0, "get_updates_buf": f"buf-{n}", "msgs": [stream["updates"][n - 1]]})
            return httpx.Response(200, json={"ret": 0, "msgs": []})
        if path.endswith("sendmessage"):
            self.sends.append((token, json.loads(request.content)))
            return httpx.Response(200, json={"ret": 0, "message_id": f"out-{len(self.sends)}"})
        return httpx.Response(404)


def _mock_wechat_httpx(monkeypatch, mock_or_holder):
    """Redirect every WeChatProvider httpx client to the mock iLink transport.

    ``mock_or_holder`` is the mock, or a zero-arg callable returning the
    CURRENT mock (for tests that swap accounts between process restarts).
    Only httpx construction inside the wechat module is redirected; the API,
    store, monitor and dispatch code under test stay real.
    """
    real_async_client = wechat_module.httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("transport", None)
        mock = mock_or_holder() if callable(mock_or_holder) else mock_or_holder
        return real_async_client(transport=httpx.MockTransport(mock.handler), **kwargs)

    monkeypatch.setattr(wechat_module.httpx, "AsyncClient", factory)
    real_build = channels_module.build_providers
    monkeypatch.setattr(
        channels_module, "build_providers",
        lambda env=None, state_dir=None: real_build({}, state_dir=state_dir))


def _inbound_rows(app):
    return [row for row in app.state.store.channel_messages("wechat")
            if row["direction"] == "INBOUND"]


def test_host_qr_confirm_writes_owner_credentials_and_monitor_creates_owner_mission(
        monkeypatch, tmp_path):
    """QR 确认 → 凭据落 owner 子树 → ACTIVE → 监控建任务归属该 owner。"""
    mock = SingleAccountILink(token="tok-alice", account="bot-alice", updates=[ALICE_UPDATE])
    _mock_wechat_httpx(monkeypatch, mock)
    app, client = _app(monkeypatch, tmp_path, host_stub=False)
    with client:
        assert client.get("/api/v1/my/channels/wechat/host/status",
                          headers=ALICE_AUTH).json()["state"] == "NONE"
        started = client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
        assert started.status_code == 200
        assert started.json()["state"] == "BINDING"

        confirmed = client.get("/api/v1/my/channels/wechat/host/login", headers=ALICE_AUTH)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["state"] == "ACTIVE"
        assert confirmed.json()["login"]["account"] == "bot-alice"

        # 凭据文件落在 hosting/<owner>/ 下，站长的全局文件绝无凭据。
        credential_file = tmp_path / "providers" / "hosting" / "alice" / "wechat.env"
        assert credential_file.is_file()
        content = credential_file.read_text(encoding="utf-8")
        assert "WECHAT_BOT_TOKEN=tok-alice" in content
        assert "WECHAT_ILINK_USER_ID=bot-alice" in content
        assert not (tmp_path / "providers" / "wechat.env").exists()

        provider = app.state.host_wechat_providers["alice"]
        assert provider.configured() is True
        task = app.state.host_monitor_tasks.get("alice")
        assert task is not None and not task.done()

        assert _wait_until(lambda: bool(_inbound_rows(app))
                           and _inbound_rows(app)[0]["mission_id"] is not None)
        rows = _inbound_rows(app)
        assert len(rows) == 1
        assert rows[0]["owner_id"] == "alice"
        assert rows[0]["sender"] == "friend-1"
        mission = app.state.store.get(rows[0]["mission_id"])
        assert mission is not None and mission["owner_id"] == "alice"
        assert "部署" in mission["original_input"]

        events = [e["type"] for e in
                  app.state.store.channel_events(message_id="host:alice")]
        assert "HOST_BINDING_STARTED" in events
        assert "HOST_BINDING_ACTIVATED" in events
        assert app.state.store.host_binding("alice", "wechat")["state"] == "ACTIVE"


def test_host_stop_ends_monitor_polling(monkeypatch, tmp_path):
    """stop → UNBOUND 后轮询真的停止：fetch 次数冻结，迟到消息不入库。"""
    script = {"updates": [{"state": "OK", "messages": [_inbound()]},
                          {"state": "OK", "messages": []}]}
    app, client = _app(monkeypatch, tmp_path, host_script=script)
    with client:
        client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
        confirmed = client.get("/api/v1/my/channels/wechat/host/login", headers=ALICE_AUTH)
        assert confirmed.json()["state"] == "ACTIVE"
        stub = app.state.host_wechat_providers["alice"]
        task = app.state.host_monitor_tasks["alice"]
        # Idle polling proves the loop is alive before the stop.
        assert _wait_until(lambda: stub.fetch_calls >= 3)
        assert _wait_until(lambda: bool(_inbound_rows(app))
                           and _inbound_rows(app)[0]["mission_id"] is not None)

        stopped = client.post("/api/v1/my/channels/wechat/host/stop", headers=ALICE_AUTH)
        assert stopped.status_code == 200
        assert stopped.json()["state"] == "UNBOUND"
        assert "alice" not in app.state.host_monitor_tasks
        assert _wait_until(lambda: task.done())

        calls_at_stop = stub.fetch_calls
        # A message that "arrives" after the stop must never be picked up: the
        # queue would serve it on the next fetch, and there is no next fetch.
        script["updates"] = [{"state": "OK",
                              "messages": [_inbound(external_id="999", text="stop 之后才到")]}]
        time.sleep(0.2)
        assert stub.fetch_calls == calls_at_stop
        assert [row["external_id"] for row in _inbound_rows(app)] == ["201"]


def test_host_monitor_stops_after_three_credential_rejections(monkeypatch, tmp_path):
    """凭据失效 3 连 → monitor 停止并审计 HOST_MONITOR_STOPPED，不空转。"""
    script = {"updates": [{"state": "FAILED", "error": "UPSTREAM_REJECTED"}]}
    app, client = _app(monkeypatch, tmp_path, host_script=script)
    with client:
        client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
        confirmed = client.get("/api/v1/my/channels/wechat/host/login", headers=ALICE_AUTH)
        assert confirmed.json()["state"] == "ACTIVE"
        stub = app.state.host_wechat_providers["alice"]

        assert _wait_until(lambda: "alice" not in app.state.host_monitor_tasks, timeout=10)
        assert stub.fetch_calls == HOST_WECHAT_CREDENTIAL_FAILURE_LIMIT
        events = [e for e in app.state.store.channel_events(message_id="host:alice")
                  if e["type"] == "HOST_MONITOR_STOPPED"]
        assert len(events) == 1
        assert events[0]["payload"]["reason"] == "CREDENTIAL_INVALID"
        assert events[0]["payload"]["consecutive_failures"] == \
            HOST_WECHAT_CREDENTIAL_FAILURE_LIMIT
        # The row stays ACTIVE by design: a restart re-runs the bounded probe
        # via the startup scan; the honest audit names why polling stopped.
        assert app.state.store.host_binding("alice", "wechat")["state"] == "ACTIVE"


def test_station_and_host_wechat_instances_do_not_cross_talk(monkeypatch, tmp_path):
    """站长实例与客户实例并存互不串扰：出码、凭据、同号消息、ack 各归各家。"""
    admin_msg = {"message_id": 101, "from_user_id": "friend-a",
                 "create_time_ms": 1720000000000, "context_token": "ctx-admin",
                 "item_list": [{"type": 1, "text_item": {"text": "帮我把部署步骤整理一下"}}]}
    alice_msg = {"message_id": 101, "from_user_id": "friend-b",
                 "create_time_ms": 1720000000001, "context_token": "ctx-alice",
                 "item_list": [{"type": 1, "text_item": {"text": "帮我把账单汇总成表格"}}]}
    mock = TwoAccountILink({
        "tok-admin": {"account": "bot-admin", "updates": [admin_msg]},
        "tok-alice": {"account": "bot-alice", "updates": [alice_msg]},
    })
    _mock_wechat_httpx(monkeypatch, mock)
    app, client = _app(monkeypatch, tmp_path, host_stub=False)
    with client:
        station_started = client.post("/api/v1/channels/wechat/login", headers=ADMIN_AUTH)
        assert station_started.status_code == 200
        assert mock.qr_order == ["qr-1"]
        station_provider = app.state.channels.get("wechat")
        assert station_provider._login["qrcode"] == "qr-1"
        mock.confirmations["qr-1"] = ("tok-admin", "bot-admin")

        started = client.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
        assert started.status_code == 200
        assert mock.qr_order == ["qr-1", "qr-2"]
        alice_provider = app.state.host_wechat_providers["alice"]
        assert alice_provider._login["qrcode"] == "qr-2"
        # 出码互不顶掉（wave 1 的已知边界已消除）：站长的进行中二维码原样保留。
        assert station_provider._login["qrcode"] == "qr-1"
        mock.confirmations["qr-2"] = ("tok-alice", "bot-alice")

        station_ok = client.get("/api/v1/channels/wechat/login", headers=ADMIN_AUTH)
        assert station_ok.json() == {"state": "AUTHORIZED", "account": "bot-admin"}
        alice_ok = client.get("/api/v1/my/channels/wechat/host/login", headers=ALICE_AUTH)
        assert alice_ok.json()["state"] == "ACTIVE"
        assert alice_ok.json()["login"]["account"] == "bot-alice"

        # 凭据各落各家，互不可见。
        station_file = tmp_path / "providers" / "wechat.env"
        alice_file = tmp_path / "providers" / "hosting" / "alice" / "wechat.env"
        assert station_file.is_file() and alice_file.is_file()
        station_content = station_file.read_text(encoding="utf-8")
        alice_content = alice_file.read_text(encoding="utf-8")
        assert "WECHAT_BOT_TOKEN=tok-admin" in station_content
        assert "tok-alice" not in station_content
        assert "WECHAT_BOT_TOKEN=tok-alice" in alice_content
        assert "tok-admin" not in alice_content
        assert station_provider.setting("WECHAT_BOT_TOKEN") == "tok-admin"
        assert alice_provider.setting("WECHAT_BOT_TOKEN") == "tok-alice"

        # 上游 message_id 只在账号内唯一：同一 external_id "101" 必须各成一行、
        # 各归其主，而不是第二个被当成"重复"丢掉。
        def _both_delivered():
            rows = _inbound_rows(app)
            return len(rows) == 2 and all(row["mission_id"] for row in rows)
        assert _wait_until(_both_delivered)
        rows = sorted(_inbound_rows(app), key=lambda row: row["owner_id"])
        assert [row["owner_id"] for row in rows] == ["admin", "alice"]
        assert [row["external_id"] for row in rows] == ["101", "101"]
        assert [row["sender"] for row in rows] == ["friend-a", "friend-b"]
        mission_admin = app.state.store.get(rows[0]["mission_id"])
        mission_alice = app.state.store.get(rows[1]["mission_id"])
        assert mission_admin["owner_id"] == "admin"
        assert mission_alice["owner_id"] == "alice"
        assert "部署" in mission_admin["original_input"]
        assert "账单" in mission_alice["original_input"]

        # ack 各走各的账号：站长的账号只回 friend-a，客户的账号只回 friend-b。
        def _both_acked():
            acks = [(token, body) for token, body in mock.sends
                    if "已收到需求" in json.dumps(body, ensure_ascii=False)]
            return len(acks) == 2
        assert _wait_until(_both_acked)
        for token, body in mock.sends:
            text = json.dumps(body, ensure_ascii=False)
            if "已收到需求" not in text:
                continue
            if token == "tok-admin":
                assert body["msg"]["to_user_id"] == "friend-a"
            else:
                assert token == "tok-alice"
                assert body["msg"]["to_user_id"] == "friend-b"


def test_active_host_binding_recovers_monitor_and_credentials_after_restart(
        monkeypatch, tmp_path):
    """停机恢复：重启后启动扫描把 ACTIVE 绑定的 monitor 与盘上凭据拉回来。"""
    holder = {"mock": SingleAccountILink(token="tok-alice", account="bot-alice")}
    _mock_wechat_httpx(monkeypatch, lambda: holder["mock"])
    app1, client1 = _app(monkeypatch, tmp_path, host_stub=False)
    with client1:
        client1.post("/api/v1/my/channels/wechat/host/start", headers=ALICE_AUTH)
        confirmed = client1.get("/api/v1/my/channels/wechat/host/login", headers=ALICE_AUTH)
        assert confirmed.json()["state"] == "ACTIVE"
        assert (tmp_path / "providers" / "hosting" / "alice" / "wechat.env").is_file()
    # client1 的 with 退出 = 进程"停机"：monitor 被取消，凭据留在盘上。

    later_msg = {"message_id": 202, "from_user_id": "friend-1",
                 "create_time_ms": 1720000001000, "context_token": "ctx-b",
                 "item_list": [{"type": 1, "text_item": {"text": "帮我把周报归档"}}]}
    holder["mock"] = SingleAccountILink(token="tok-alice", account="bot-alice",
                                        updates=[later_msg])
    app2 = create_app(tmp_path)
    with TestClient(app2) as client2:
        # 启动扫描在 lifespan 里同步起任务、构造实例（实例构造即读盘上凭据）。
        assert "alice" in app2.state.host_monitor_tasks
        provider = app2.state.host_wechat_providers["alice"]
        assert provider.configured() is True
        assert provider.setting("WECHAT_BOT_TOKEN") == "tok-alice"

        def _delivered():
            rows = [row for row in _inbound_rows(app2) if row["external_id"] == "202"]
            return bool(rows) and rows[0]["mission_id"] is not None
        assert _wait_until(_delivered)
        row = [r for r in _inbound_rows(app2) if r["external_id"] == "202"][0]
        assert row["owner_id"] == "alice"
        assert app2.state.store.get(row["mission_id"])["owner_id"] == "alice"
