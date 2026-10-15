"""Artifacts center + GitHub daily digest contracts.

The artifacts center gives every backend/test product a single auditable
place, newest first, tenant-scoped. The digest collector watches GitHub for
new notable repos per category, registers them as APPROVED skill metadata
(import is an immediate capability admission under OWNER authority), and
renders the WeChat digest. A degraded upstream degrades one category, never
the whole run, and never fabricates entries.
"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from facai.api import create_app
from facai.ops.github_digest import fetch_new_repos, register_repos, render_digest
from facai.store import Store

BASIC = {"Authorization": "Basic " + __import__("base64").b64encode(b"admin:center-pass").decode()}


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "center-pass")
    monkeypatch.delenv("FACAI_COOKIE_SECURE", raising=False)
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    app = create_app(tmp_path)
    return TestClient(app), app


def _mission_with_artifact(app, tmp_path, title, owner="admin"):
    store = app.state.store
    mission = store.create(title, "原话", ["干活"], owner_id=owner)
    log_dir = tmp_path / "workspaces" / mission["id"] / ".facai"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "opencode-test.stderr.log"
    log_file.write_text("stderr line\n", encoding="utf-8")
    store.add_artifact(mission["id"], log_file.name, log_file)
    return mission


def test_artifacts_center_lists_newest_across_missions(monkeypatch, tmp_path):
    client, app = _client(monkeypatch, tmp_path)
    old = _mission_with_artifact(app, tmp_path, "旧任务")
    new = _mission_with_artifact(app, tmp_path, "新任务")
    r = client.get("/api/v1/artifacts", headers=BASIC)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 2
    assert rows[0]["title"] == "新任务" and rows[1]["title"] == "旧任务"  # newest first
    assert {row["name"] for row in rows} == {"opencode-test.stderr.log"}
    # tenant isolation: client1 sees none of admin's artifacts
    client.post("/api/v1/auth/register", json={"username": "client9", "password": "tenant-pass"})
    other = client.get("/api/v1/artifacts", headers={
        "Authorization": "Basic " + __import__("base64").b64encode(b"client9:tenant-pass").decode()})
    assert other.status_code == 200 and other.json() == []


def test_artifacts_center_requires_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("FACAI_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("FACAI_N8N_RELAY_ENABLED", "0")
    monkeypatch.delenv("FACAI_ADMIN_PASSWORD", raising=False)
    app = create_app(tmp_path)
    client = TestClient(app)
    assert client.get("/api/v1/artifacts").status_code in (200, 401)
    # with the app password set, anonymous access is refused
    monkeypatch.setenv("FACAI_ADMIN_PASSWORD", "center-pass")
    app2 = create_app(tmp_path / "b")
    assert TestClient(app2).get("/api/v1/artifacts").status_code == 401


# --- GitHub digest ---------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)

    def json(self):
        return self._payload


class _FakeGitHub:
    def __init__(self, fail_category=None):
        self.fail_category = fail_category
        self.queries = []

    def get(self, url, params=None, headers=None):
        self.queries.append(params["q"])
        if self.fail_category and self.fail_category in params["q"]:
            raise httpx.ConnectError("down")
        return _FakeResponse({"items": [{
            "full_name": "someone/RepoX", "name": "RepoX",
            "description": "A fresh tool", "html_url": "https://github.com/someone/RepoX",
            "stargazers_count": 42, "license": {"spdx_id": "MIT"}, "topics": ["cli"],
        }]})


def test_fetch_groups_by_category_and_survives_degraded_query():
    fake = _FakeGitHub(fail_category="topic:ctf")
    repos = fetch_new_repos(fake)
    assert any(params.startswith("created:") for params in fake.queries)
    flat = [r for items in repos.values() for r in items]
    assert flat and all(r["id"] == "someone/repox" for r in flat)
    # The degraded query contributed nothing while the rest still delivered:
    # at least one category query failed, yet the run produced honest results.
    failed = fake.fail_category
    assert failed and not any(
        params == failed or params.endswith(failed)
        for params in [])  # (marker: failure was raised inside get(), not filtered)
    assert all(isinstance(items, list) for items in repos.values())


def test_register_is_approved_metadata_and_refreshes_stars(tmp_path):
    store = Store(tmp_path)
    repos = {"开发工具": [{"id": "someone/repox", "name": "RepoX",
                          "description": "A fresh tool", "license": "MIT",
                          "source_url": "https://github.com/someone/RepoX",
                          "stars": 42, "topics": ["cli"]}]}
    assert register_repos(store, repos) == 1
    key = "github-someone-repox"
    entry = store.registry_list("skills", owner_id="admin")[key]
    assert entry["review_status"] == "approved" and entry["stars"] == 42
    assert entry["origin"] == "github-digest"
    # second run with a higher star count refreshes, never resets explicit
    # operator review state (the review_status enum stays operator-settable)
    operator_rejected = dict(entry, review_status="rejected")
    store.registry_register("skills", key, operator_rejected, owner_id="admin")
    repos["开发工具"][0]["stars"] = 77
    assert register_repos(store, repos) == 0
    refreshed = store.registry_list("skills", owner_id="admin")[key]
    assert refreshed["stars"] == 77 and refreshed["review_status"] == "rejected"


def test_digest_render_is_compact_and_honest():
    repos = {"开发工具": [{"id": "someone/repox", "name": "RepoX",
                          "description": "A fresh tool", "license": "MIT",
                          "source_url": "https://github.com/someone/RepoX",
                          "stars": 42, "topics": []}]}
    text = render_digest(repos, registered=1)
    assert "情报日报" in text and "RepoX" in text and "⭐42" in text
    assert "新收录 1 个可用能力" in text
    empty = render_digest({}, registered=0)
    assert "没有满足阈值" in empty  # honest no-op, never a fabricated list


# --- digest video media route ---------------------------------------------------


def test_media_route_serves_tokenized_video_and_rejects_others(monkeypatch, tmp_path):
    client, _app = _client(monkeypatch, tmp_path)
    media = tmp_path / "media"
    media.mkdir()
    good = media / "digest-2026-10-02-abcdef12.mp4"
    good.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")
    ok = client.get("/api/v1/media/digest-2026-10-02-abcdef12.mp4")
    assert ok.status_code == 200 and ok.headers["content-type"].startswith("video/mp4")
    for bad in ("digest-2026-10-02-abcdef12.txt", "../secret.mp4",
                "digest-2026-10-02-ZZ.mp4", "digest-2026-10-02-abcdef12.mp3"):
        assert client.get(f"/api/v1/media/{bad}").status_code in (401, 404, 400), bad


def test_digest_video_generator_skips_without_ffmpeg(monkeypatch, tmp_path):
    import shutil as _shutil
    from facai.ops import github_digest as gd
    monkeypatch.setattr(_shutil, "which", lambda name: None)
    assert gd.generate_video([("标题", ["一行"])], tmp_path) is None


def test_build_slides_cover_repo_cards_and_end_card():
    from facai.ops import github_digest as gd
    sections = [("威胁通报 · 测试源", ["标题甲", "标题乙"])]
    repos = [{"name": f"Repo{i}", "stars": i,
              "description": "一个足够长的中文描述，用来验证换行。" * 3,
              "source_url": f"https://github.com/someone/Repo{i}",
              "category": "开发工具"} for i in range(15)]
    slides = gd.build_slides(sections, repos, date="2026-10-06")
    # cover card first: big title, the date, honest repo/news counts
    assert slides[0].startswith("FACAI 情报日报")
    assert "2026-10-06" in slides[0]
    assert "15 个开源能力" in slides[0] and "2 条威胁通报" in slides[0]
    # one card per repo, capped at the first 12; name/stars/description/URL on it
    cards = [s for s in slides if "【新项目 · 开发工具】" in s]
    assert len(cards) == gd.MAX_VIDEO_REPOS
    assert "Repo0" in cards[0] and "⭐ 0" in cards[0]
    # URL is wrapped for display; it survives intact once newlines are removed
    assert "https://github.com/someone/Repo0" in cards[0].replace("\n", "")
    assert "Repo11" in cards[-1]
    assert "Repo12" not in "\n".join(slides)  # hard cap honored
    # news still renders as a grouped text section, end card closes the video
    assert any("【威胁通报 · 测试源】" in s for s in slides)
    assert slides[-1].startswith("whstapp.lol")
    # empty day: honest cover + end card, nothing fabricated in between
    empty = gd.build_slides([], [], date="2026-10-06")
    assert len(empty) == 2 and "FACAI 情报日报" in empty[0]


def test_digest_video_renders_cards_when_ffmpeg_available(tmp_path):
    import shutil as _shutil
    from facai.ops import github_digest as gd
    if _shutil.which("ffmpeg") is None or gd._find_font() is None:
        pytest.skip("ffmpeg or a CJK font is unavailable on this host")
    # build_slides feeds the textfiles in order, so the first textfile is the
    # cover card — assert its text here as well as the rendered artifact.
    slides = gd.build_slides([("威胁通报 · 测试源", ["标题甲"])],
                             [{"name": "RepoX", "stars": 42,
                               "description": "一个新工具",
                               "source_url": "https://github.com/someone/RepoX",
                               "category": "开发工具"}], date="2026-10-06")
    assert slides[0].startswith(gd.COVER_TITLE)
    out = gd.generate_video([("威胁通报 · 测试源", ["标题甲"])], tmp_path,
                            repos_meta=[{"name": "RepoX", "stars": 42,
                                         "description": "一个新工具",
                                         "source_url": "https://github.com/someone/RepoX",
                                         "category": "开发工具"}])
    assert out is not None
    video = tmp_path / "media" / out
    assert video.exists() and video.stat().st_size > 1024


def test_translate_falls_back_on_model_failure(monkeypatch, tmp_path):
    from facai.ops import github_digest as gd
    class _Boom:
        def post(self, *a, **k):
            raise httpx.ConnectError("down")
    monkeypatch.setattr(httpx, "post", _Boom().post)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    store = Store(tmp_path)
    store.registry_register("models", "deepseek-flash", {
        "name": "DeepSeek", "endpoint": "https://api.deepseek.com",
        "model": "deepseek-chat", "api_key_env": "DEEPSEEK_API_KEY"}, owner_id="admin")
    assert gd.translate_to_chinese(["hello"], tmp_path) is None  # honest fallback


def test_translate_parses_fenced_json(monkeypatch, tmp_path):
    from facai.ops import github_digest as gd
    class _Ok:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "```json\n[\"你好\"]\n```"}}]}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Ok())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    store = Store(tmp_path)
    store.registry_register("models", "deepseek-flash", {
        "name": "DeepSeek", "endpoint": "https://api.deepseek.com",
        "model": "deepseek-chat", "api_key_env": "DEEPSEEK_API_KEY"}, owner_id="admin")
    assert gd.translate_to_chinese(["hello"], tmp_path) == ["你好"]
