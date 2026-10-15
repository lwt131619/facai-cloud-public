"""FACAI_GATES_OFF=1 一键解除条框：锁定 / 弱密码 / 保留名 / 任务限速。

默认（开关关闭）行为零变化——既有 1171 项测试就是回归护栏。
本文件只测开关打开后的放行语义。
"""
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.auth import gates_off, is_weak_password

MISSION = {"title": "条框解除", "original_input": "gates off", "steps": ["一步"]}


def _build(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "ap-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.setenv("FACAI_CHANNEL_NOTIFY_ENABLED", "0")
    monkeypatch.setenv("FACAI_GATES_OFF", "1")
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.post("/api/v1/auth/register",
                    json={"username": "tester", "password": "1"})
    return app


# --- 开关解析 ----------------------------------------------------------------


def test_gates_off_env_parsing(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("FACAI_GATES_OFF", value)
        assert gates_off() is True
    monkeypatch.setenv("FACAI_GATES_OFF", "")
    assert gates_off() is False
    monkeypatch.delenv("FACAI_GATES_OFF", raising=False)
    assert gates_off() is False


def test_weak_password_passthrough_when_off(monkeypatch):
    monkeypatch.setenv("FACAI_GATES_OFF", "1")
    assert is_weak_password("1") is False          # 一位密码也放行
    assert is_weak_password("password") is False   # 常见弱口令也放行
    assert is_weak_password("") is True            # 只挡"空"
    assert is_weak_password(None) is True
    monkeypatch.setenv("FACAI_GATES_OFF", "")
    assert is_weak_password("password") is True    # 默认行为不变


# --- 注册：保留名 + 弱密码放行 ------------------------------------------------


def test_register_reserved_name_and_weak_password(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    with TestClient(app) as client:
        # "root" 是保留名、"1" 是弱密码——条框解除后都放行。
        r = client.post("/api/v1/auth/register",
                        json={"username": "root", "password": "1"})
        assert r.status_code == 201, r.text
        # 默认行为对照：开关关闭时同样请求应被拒。
        monkeypatch.setenv("FACAI_GATES_OFF", "")
        app2 = create_app(tmp_path / "second")
        with TestClient(app2) as client2:
            r2 = client2.post("/api/v1/auth/register",
                              json={"username": "root", "password": "1"})
            assert r2.status_code in (400, 422), r2.text


# --- 登录：失败锁定永不触发 ----------------------------------------------------


def test_login_never_locks_when_off(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    with TestClient(app) as client:
        for _ in range(10):  # 远超 max_failures=5
            r = client.post("/api/v1/auth/login",
                            json={"username": "tester", "password": "wrong"})
            assert r.status_code == 401, r.text  # 401 而非 429
        ok = client.post("/api/v1/auth/login",
                         json={"username": "tester", "password": "1"})
        assert ok.status_code == 200 and ok.json()["authenticated"] is True


# --- 任务创建：滑窗 429 解除（402 容量门保留） ---------------------------------


def test_mission_rate_window_off(monkeypatch, tmp_path):
    app = _build(monkeypatch, tmp_path)
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login",
                            json={"username": "tester", "password": "1"})
        client.headers.update(
            {"X-CSRF-Token": login.json()["csrf_token"]})
        codes = [client.post("/api/v1/missions", json=MISSION).status_code
                 for _ in range(9)]
        # 全部 201：第 7 个起不再 429（402 容量门上限是 10，未触）。
        assert codes == [201] * 9, codes
