"""[9] INGESTION AUTOMATION：一条命令完成 clone→初扫→manifest→Verifier→报告。

全部离线：微型「仓库」与 git clone 都在 tmp_path 本地完成，不触网。
验证口径（任务冻结）：
- manifests 两份 JSON 落盘且 schema 与既有九源一致；
- surfaces 分类正确、逐条带 file:line 证据；
- verdict 如实 T2_PARTIAL（新源无任何接线声明）；
- 重复 ingest 幂等：不重复克隆、manifest/面不翻倍；
- 绝不自动登记 CapabilityRegistry。
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from facai.capabilities import (
    PERMISSION_FAMILIES,
    SCHEMA_PERMISSIONS,
    manifest_grant_templates,
)
from facai.ops import ingest
from facai.ops.ingest import (
    SURFACE_LIMIT,
    IngestError,
    detect_license,
    derive_name,
    derive_permissions,
    ensure_clone,
    run_ingest,
)
from facai.ops.verifier import T2_PARTIAL

MANIFEST_KEYS = {"target", "origin", "managementClass", "summary",
                 "surfaces", "reRooting"}
GRAPH_KEYS = {"schema", "source", "license", "method", "summary",
              "permissions", "capabilities"}
KNOWN_CLASSES = {"LOCAL_BROKERABLE", "LOCAL_DIRECT", "CONFIGURABLE",
                 "REMOTE_PROVIDER", "UNKNOWN"}
CHECK_ORDER = {
    "owner_root", "self_grant", "action_ir", "direct_exec_inventory",
    "brokerable_paths", "secret_access", "grant_revocation",
    "remote_dependencies", "provider_result", "evidence_linkage",
}

GIT = shutil.which("git")


def _make_micro_repo(tmp_path):
    """3 文件微型仓库：一个 https 常量、一个 os.getenv 密钥、一个 subprocess 调用。"""
    src = tmp_path / "mini"
    (src / "app").mkdir(parents=True)
    (src / "app" / "model.py").write_text(
        'API_URL = "https://api.miniprovider.example/v1/chat"\n'
        "\n"
        "def endpoint():\n"
        "    return API_URL\n",
        encoding="utf-8")
    (src / "app" / "creds.py").write_text(
        "import os\n"
        "\n"
        "API_KEY = os.getenv('MINI_API_KEY')\n",
        encoding="utf-8")
    (src / "app" / "runner.py").write_text(
        "import subprocess\n"
        "\n"
        "\n"
        "def run_tool():\n"
        "    return subprocess.run(['echo', 'hi'], check=False)\n",
        encoding="utf-8")
    return src


# --- a) 两份 JSON 落盘且 schema 合法 ------------------------------------------

def test_run_ingest_writes_both_manifests_with_valid_schema(tmp_path):
    src = _make_micro_repo(tmp_path)
    vendor = tmp_path / "vendor"

    result = run_ingest(str(src), name="mini", vendor_root=vendor)

    manifest_path = vendor / "authority-manifests" / "mini.json"
    graph_path = vendor / "capability-graph" / "mini.json"
    assert manifest_path.is_file() and graph_path.is_file()
    assert result["manifest"] == str(manifest_path)
    assert result["capability_graph"] == str(graph_path)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(manifest) == MANIFEST_KEYS            # 与既有九源六键完全一致
    assert manifest["managementClass"].startswith("T2")   # 初判 T2
    assert manifest["reRooting"] == []               # 空待人工
    assert len(manifest["surfaces"]) == 3
    for surface in manifest["surfaces"]:
        assert surface["classification"] in KNOWN_CLASSES
        assert surface["where"] and ":" in surface["where"]
        assert surface["evidence"]

    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    assert set(graph) == GRAPH_KEYS
    assert graph["schema"] == "facai.capability-graph/1"
    assert graph["source"] == "mini"
    assert graph["capabilities"], "骨架至少要落初扫推导的候选"


# --- b) surfaces 分类正确、逐条带证据 -----------------------------------------

def test_scan_classifies_surfaces_with_evidence(tmp_path):
    src = _make_micro_repo(tmp_path)

    surfaces, meta = ingest.scan_source(src)

    assert meta["surface_count"] == 3
    assert meta["truncated"] is False
    by_kind = {s["kind"]: s for s in surfaces}
    net = by_kind["NETWORK"]
    cred = by_kind["CREDENTIAL"]
    shell = by_kind["SHELL_EXEC"]

    # 模型端点常量 → REMOTE_PROVIDER，remoteAuthorityDependency 初判 true
    assert net["classification"] == "REMOTE_PROVIDER"
    assert net["remoteAuthorityDependency"] is True
    assert net["endpoint"] == "https://api.miniprovider.example/v1/chat"
    assert net["where"] == "app/model.py:1"
    assert "API_URL" in net["evidence"]

    # env 密钥 → CREDENTIAL / LOCAL_BROKERABLE
    assert cred["classification"] == "LOCAL_BROKERABLE"
    assert cred["env_key"] == "MINI_API_KEY"
    assert cred["where"] == "app/creds.py:3"
    assert "os.getenv" in cred["evidence"]

    # subprocess 调用点 → SHELL_EXEC / LOCAL_BROKERABLE
    assert shell["classification"] == "LOCAL_BROKERABLE"
    assert shell["where"] == "app/runner.py:5"
    assert "subprocess.run" in shell["evidence"]


def test_unknown_url_is_not_overclaimed_as_remote(tmp_path):
    src = tmp_path / "plain"
    src.mkdir()
    (src / "misc.py").write_text(
        'DOCS = "https://example.com/getting-started"\n', encoding="utf-8")

    surfaces, _ = ingest.scan_source(src)

    assert len(surfaces) == 1
    assert surfaces[0]["classification"] == "UNKNOWN"
    assert surfaces[0]["remoteAuthorityDependency"] is False


def test_scan_caps_surfaces_at_limit(tmp_path):
    src = tmp_path / "wide"
    src.mkdir()
    lines = "\n".join(f'U{i} = "https://h{i}.example/v1/chat"' for i in range(250))
    (src / "many.py").write_text(lines + "\n", encoding="utf-8")

    surfaces, meta = ingest.scan_source(src)

    assert meta["surface_count"] == SURFACE_LIMIT
    assert meta["truncated"] is True
    assert len(surfaces) == SURFACE_LIMIT


# --- c) verdict 如实 T2_PARTIAL ------------------------------------------------

def test_verdict_is_honest_t2_partial(tmp_path):
    src = _make_micro_repo(tmp_path)

    result = run_ingest(str(src), name="mini", vendor_root=tmp_path / "vendor")

    assert result["verdict"] == T2_PARTIAL
    checks = result["verification"]["checks"]
    assert set(checks) == CHECK_ORDER                # 复用 INDEX.json 的 checks 结构
    assert checks["direct_exec_inventory"]["status"] == "PASS"
    # 新源无任何接线声明 → brokerable/secret/remote/normalization/evidence 全 PENDING
    for pending_check in ("brokerable_paths", "secret_access",
                          "remote_dependencies", "provider_result",
                          "evidence_linkage"):
        assert checks[pending_check]["status"] == "PENDING"
    assert result["verification"]["unmanaged"], "无接线声明的新源必有未纳管面"
    assert "license UNKNOWN" in "; ".join(result["verification"]["reasons"])


# --- d) 重复 ingest 幂等：不重复克隆、不翻倍 -----------------------------------

def test_repeat_ingest_is_idempotent(tmp_path):
    src = _make_micro_repo(tmp_path)
    vendor = tmp_path / "vendor"

    first = run_ingest(str(src), name="mini", vendor_root=vendor)
    second = run_ingest(str(src), name="mini", vendor_root=vendor)

    assert first["clone"]["status"] == second["clone"]["status"] == "local"
    assert second["scan"]["surface_count"] == first["scan"]["surface_count"] == 3
    manifest = json.loads(
        (vendor / "authority-manifests" / "mini.json").read_text(encoding="utf-8"))
    assert len(manifest["surfaces"]) == 3            # 不翻倍
    assert sorted(p.name for p in (vendor / "authority-manifests").glob("*.json")) == ["mini.json"]
    assert sorted(p.name for p in (vendor / "capability-graph").glob("*.json")) == ["mini.json"]


def test_ensure_clone_reuses_existing_dest_without_running_git(tmp_path, monkeypatch):
    dest = tmp_path / "vendor" / "mini"
    dest.mkdir(parents=True)
    (dest / "marker.txt").write_text("already-here", encoding="utf-8")

    def _must_not_run(*args, **kwargs):
        raise AssertionError("dest 已存在时不得再次调用 git clone")

    monkeypatch.setattr(ingest.subprocess, "run", _must_not_run)

    status, reused = ensure_clone("https://example.com/org/mini.git", dest)

    assert status == "reused"
    assert (reused / "marker.txt").is_file()


@pytest.mark.skipif(GIT is None, reason="本机无 git 可执行文件")
def test_ensure_clone_clones_local_repo_offline(tmp_path):
    src = _make_micro_repo(tmp_path)

    def git(*args):
        subprocess.run(["git", "-C", str(src), *args], check=True,
                       capture_output=True, text=True)

    git("init")
    git("add", ".")
    git("-c", "user.name=ingest-test", "-c", "user.email=ingest@test.local",
        "commit", "-m", "init")

    status, cloned = ensure_clone(str(src), tmp_path / "vendor" / "mini")

    assert status == "cloned"
    assert (cloned / "app" / "model.py").is_file()


# --- e) fail-closed 输入守卫 ---------------------------------------------------

def test_offline_mode_rejects_git_url(tmp_path):
    with pytest.raises(IngestError):
        run_ingest("https://example.com/org/repo.git",
                   vendor_root=tmp_path / "vendor", offline=True)


def test_missing_local_path_fails(tmp_path):
    with pytest.raises(IngestError):
        run_ingest(str(tmp_path / "nope"), vendor_root=tmp_path / "vendor")


def test_refuses_to_overwrite_non_draft_manifest(tmp_path):
    vendor = tmp_path / "vendor"
    mdir = vendor / "authority-manifests"
    mdir.mkdir(parents=True)
    (mdir / "ratified.json").write_text(
        json.dumps({"summary": "人工逐面复核过的清单"}), encoding="utf-8")
    src = _make_micro_repo(tmp_path)

    with pytest.raises(IngestError):
        run_ingest(str(src), name="ratified", vendor_root=vendor)


# --- f) 报告计数与骨架内容 ------------------------------------------------------

def test_report_counts_capability_candidates_and_external_deps(tmp_path):
    src = _make_micro_repo(tmp_path)

    result = run_ingest(str(src), name="mini", vendor_root=tmp_path / "vendor")

    diff = result["capability_diff"]
    # 三个面种各推导一条候选；初扫绝不登记 → registered=False
    assert diff["new"] == ["shell.exec", "secret.request", "model.invoke"]
    assert diff["external_dependency"] == ["vendor.api_miniprovider_example"]
    assert diff["already_owned"] == []
    assert result["registered"] is False

    graph = json.loads(
        Path(result["capability_graph"]).read_text(encoding="utf-8"))
    assert [c["id"] for c in graph["capabilities"]] == [
        "shell.exec", "secret.request", "model.invoke",
        "vendor.api_miniprovider_example"]
    assert all(c["level"] == "L0_REFERENCE" for c in graph["capabilities"])


def test_detect_license_and_name_derivation(tmp_path):
    src = tmp_path / "lic"
    src.mkdir()
    (src / "LICENSE").write_text(
        "MIT License\n\nPermission is hereby granted, free of charge...\n",
        encoding="utf-8")

    assert detect_license(src) == "MIT"
    assert detect_license(tmp_path) == "UNKNOWN"     # 无 license 文件 → 如实 UNKNOWN
    assert derive_name("https://github.com/block/goose.git") == "goose"
    assert derive_name("C:/some dir/My Tool/") == "my-tool"


# --- g) CLI 一条命令 -------------------------------------------------------------

def test_cli_main_prints_report_and_does_not_register(tmp_path, capsys):
    src = _make_micro_repo(tmp_path)
    vendor = tmp_path / "vendor"

    rc = ingest.main([str(src), "--name", "mini",
                      "--vendor-dir", str(vendor), "--offline"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "T2_PARTIAL" in out
    assert "未纳管面" in out and "新增能力候选" in out and "外部依赖" in out
    assert "未登记" in out and "register_with_gate" in out
    assert (vendor / "authority-manifests" / "mini.json").is_file()


def test_cli_main_returns_1_on_ingest_error(tmp_path, capsys):
    rc = ingest.main([str(tmp_path / "missing"),
                      "--vendor-dir", str(tmp_path / "vendor")])

    assert rc == 1
    assert "ingest 失败" in capsys.readouterr().out


# --- h) 扫描器三修（INGEST_FIRST_RUN §7 实测反馈 → 代码） -----------------------
#
# §7-1 截断偏置：examples/ 抓取 JSON 吃光 200 配额、swarm/core.py 零覆盖
# §7-2 REMOTE_PROVIDER 误报：文档链接与代码端点一视同仁（46/46 全误报）
# §7-3 传递性凭据盲区：OpenAI() 经 SDK 内部读 OPENAI_API_KEY 扫描器不可见


def test_doc_noise_cannot_starve_source_files(tmp_path):
    """修1：源码优先——examples 文档 JSON 再多，core.py 的面也必须出现。"""
    src = tmp_path / "docheavy"
    data = src / "examples" / "data"
    data.mkdir(parents=True)
    for i in range(3):
        lines = "\n".join(
            f'"link": "https://host{i}a.example/v1/chat"\n'
            f'"more": "https://host{i}b.example/v1/chat"' for _ in range(60))
        (data / f"article{i}.json").write_text(lines + "\n", encoding="utf-8")
    (src / "core.py").write_text(
        'client = OpenAI()\n'
        'resp = client.chat.completions.create(model="gpt")\n',
        encoding="utf-8")

    surfaces, meta = ingest.scan_source(src)

    source_wheres = [s["where"] for s in surfaces if s["where"].startswith("core.py")]
    assert source_wheres, "源码面必须在（修复前会被 examples JSON 挤光）"
    assert meta["source_surface_count"] == len(source_wheres) >= 1
    assert meta["source_files_scanned"] == 1
    assert meta["doc_surface_count"] > 0            # 文档命中仍在，但单独计数
    assert all(s["where"].startswith("examples/")
               for s in surfaces if str(s["kind"]).startswith("DOC_"))


def test_doc_hits_demoted_to_doc_kind_and_counted_separately(tmp_path):
    """修1：文档/示例文件命中 classification 规则不变、kind 加 DOC_ 前缀、单独计数。"""
    src = tmp_path / "docsdir"
    (src / "docs").mkdir(parents=True)
    (src / "docs" / "setup.md").write_text(
        "export it via os.getenv('EXAMPLE_TOKEN')\n"
        "see https://example.com/guide\n",
        encoding="utf-8")

    surfaces, meta = ingest.scan_source(src)

    kinds = {s["kind"] for s in surfaces}
    assert kinds == {"DOC_CREDENTIAL", "DOC_NETWORK"}
    # classification 规则不变：凭据样键仍 LOCAL_BROKERABLE
    cred = next(s for s in surfaces if s["kind"] == "DOC_CREDENTIAL")
    assert cred["classification"] == "LOCAL_BROKERABLE"
    assert cred["env_key"] == "EXAMPLE_TOKEN"
    assert meta["doc_surface_count"] == 2
    assert meta["source_surface_count"] == 0


def test_doc_urls_classified_doc_reference_not_remote_provider(tmp_path):
    """修2：文档链接 → DOC_REFERENCE 新分类，绝不进 REMOTE_PROVIDER 误报池。"""
    src = tmp_path / "readme"
    src.mkdir()
    (src / "README.md").write_text(
        "Get a key at https://platform.openai.example.com/account\n"
        "API base https://api.openai.example.com/v1/chat\n",
        encoding="utf-8")
    (src / "guide.txt").write_text("docs at https://docs.example.com/quickstart\n",
                                   encoding="utf-8")

    surfaces, meta = ingest.scan_source(src)

    assert len(surfaces) == 3
    for s in surfaces:
        assert s["classification"] == "DOC_REFERENCE"
        assert s["kind"] == "DOC_NETWORK"
        assert s["remoteAuthorityDependency"] is False
    assert meta["by_classification"] == {"DOC_REFERENCE": 3}
    # 即便 URL 命中模型类关键词（/v1/chat）也不得初判 REMOTE_PROVIDER
    assert meta["by_kind"] == {"DOC_NETWORK": 3}


def test_source_quota_independent_rest_files_skipped_when_full(tmp_path):
    """修1：源码配额独立 200——源码档打满后其余文件不扫、绝不挤占源码面。"""
    src = tmp_path / "full"
    (src / "examples").mkdir(parents=True)
    lines = "\n".join(f'"https://s{i}.example/v1/chat"' for i in range(250))
    (src / "app.py").write_text(lines + "\n", encoding="utf-8")
    (src / "examples" / "data.json").write_text(
        "\n".join(f'"https://d{i}.example/x"' for i in range(50)) + "\n",
        encoding="utf-8")

    surfaces, meta = ingest.scan_source(src)

    assert meta["surface_count"] == SURFACE_LIMIT
    assert meta["source_surface_count"] == SURFACE_LIMIT
    assert meta["truncated"] is True
    assert meta["doc_surface_count"] == 0           # 其余文件根本没扫
    assert meta["other_files_scanned"] == 0
    assert all(not s["where"].startswith("examples/") for s in surfaces)


def test_rest_files_scanned_when_source_under_quota(tmp_path):
    """源码配额未满时其余文件才扫，且有自己的档位、不回挤源码计数。"""
    src = tmp_path / "mixed"
    (src / "examples").mkdir(parents=True)
    (src / "app.py").write_text('ENDPOINT = "https://api.miniprovider.example/v1/chat"\n',
                                encoding="utf-8")
    (src / "examples" / "links.json").write_text(
        "\n".join(f'"https://d{i}.example/v1/chat"' for i in range(9)) + "\n",
        encoding="utf-8")
    (src / "setup.cfg").write_text("url = https://cfg.example.com/home\n",
                                   encoding="utf-8")

    surfaces, meta = ingest.scan_source(src)

    assert meta["source_surface_count"] == 1
    # examples/*.json → DOC_NETWORK/DOC_REFERENCE；setup.cfg 非文档 → 正常档
    doc = [s for s in surfaces if s["kind"] == "DOC_NETWORK"]
    other = [s for s in surfaces if s["kind"] == "NETWORK" and s["where"].endswith("setup.cfg:1")]
    assert len(doc) == 9 and len(other) == 1
    assert doc[0]["classification"] == "DOC_REFERENCE"
    assert other[0]["classification"] == "UNKNOWN"
    assert meta["doc_surface_count"] == 9
    assert meta["other_surface_count"] == 1
    assert meta["surface_count"] == 11


def test_inferred_credential_from_sdk_client_constructors(tmp_path):
    """修3：源码中 SDK 客户端构造 → INFERRED_CREDENTIAL（传递性凭据面）。"""
    src = tmp_path / "sdk"
    src.mkdir()
    (src / "client.py").write_text(
        "import openai, anthropic, httpx, requests\n"
        "client = OpenAI()\n"
        "anth = Anthropic()\n"
        "pool = httpx.Client(auth=('u', 'p'))\n"
        "sess = requests.Session(headers={'Authorization': 'Bearer x'})\n",
        encoding="utf-8")

    surfaces, meta = ingest.scan_source(src)

    inferred = [s for s in surfaces if s["kind"] == "INFERRED_CREDENTIAL"]
    assert {s["sdk"] for s in inferred} == {"openai", "anthropic", "httpx", "requests"}
    for s in inferred:
        assert s["classification"] == "LOCAL_BROKERABLE"
        assert s["remoteAuthorityDependency"] is False
        assert s["inferred"] is True
        assert f"inferred via {s['sdk']}" in s["what"]
    oai = next(s for s in inferred if s["sdk"] == "openai")
    assert oai["where"] == "client.py:2"
    assert "OpenAI()" in oai["evidence"]
    assert meta["by_kind"]["INFERRED_CREDENTIAL"] == 4


def test_inferred_credential_only_from_source_files_not_docs(tmp_path):
    """修3：SDK 构造推断只在源码扩展名里触发；README 提及不算凭据面。"""
    src = tmp_path / "docsOnly"
    src.mkdir()
    (src / "README.md").write_text(
        "Just construct OpenAI() as the quickstart says.\n", encoding="utf-8")

    surfaces, meta = ingest.scan_source(src)

    assert not [s for s in surfaces if s["kind"] == "INFERRED_CREDENTIAL"]
    assert meta["source_surface_count"] == 0


def test_inferred_credential_feeds_secret_request_candidate_and_verifier(tmp_path):
    """修3 联动：INFERRED_CREDENTIAL 进 verifier 枚举检查与 secret.request 候选推导。"""
    src = tmp_path / "wire"
    src.mkdir()
    (src / "core.py").write_text("client = OpenAI()\n", encoding="utf-8")

    result = run_ingest(str(src), name="wire", vendor_root=tmp_path / "vendor")

    # secret.request 候选由推断凭据面触发（证据指向构造行）
    assert "secret.request" in result["capability_diff"]["new"]
    graph = json.loads(
        Path(result["capability_graph"]).read_text(encoding="utf-8"))
    secret_cap = next(c for c in graph["capabilities"] if c["id"] == "secret.request")
    assert secret_cap["evidence"] == "core.py:1"
    # verifier：新分类在枚举内 → direct_exec_inventory PASS；凭据面如实 PENDING
    checks = result["verification"]["checks"]
    assert checks["direct_exec_inventory"]["status"] == "PASS"
    assert checks["secret_access"]["status"] == "PENDING"
    assert "CREDENTIAL" in checks["secret_access"]["evidence"]


# --- i) Dify 插件 manifest 对标吸收：permissions 节 + 受限 grant 模板映射 ------
# （TASKQUEUE 对标吸收项：收录 CLI 产出带权限声明的 capability manifest，
# 注册时映射为受限 grant 模板。验收=本节断言。）


def _graph_of(result) -> dict:
    return json.loads(Path(result["capability_graph"]).read_text(encoding="utf-8"))


def _perms_by_action(perms: list) -> dict:
    return {p["action"]: p for p in perms}


def test_capability_graph_permissions_section_deny_by_default(tmp_path):
    src = _make_micro_repo(tmp_path)

    result = run_ingest(str(src), name="mini", vendor_root=tmp_path / "vendor")

    perms = result["permissions"]
    assert perms["schema"] == SCHEMA_PERMISSIONS
    assert [p["action"] for p in perms["entries"]] == list(PERMISSION_FAMILIES)
    assert perms["declared"] == ["shell.exec", "network.*", "secret.request",
                                 "provider.invoke"]
    assert perms["denied"] == ["filesystem.write", "docker.run"]
    by = _perms_by_action(perms["entries"])

    # 运行时面带证据的族才放开，scope 钉在证据上
    assert by["shell.exec"]["allowed"] is True
    assert by["shell.exec"]["evidence"] == "app/runner.py:5"
    assert by["secret.request"]["allowed"] is True
    assert by["secret.request"]["resources"] == ["MINI_API_KEY"]
    assert by["network.*"]["allowed"] is True
    assert by["network.*"]["resources"] == ["api.miniprovider.example"]
    assert by["provider.invoke"]["allowed"] is True
    # 初扫无启发式规则的族恒拒
    assert by["filesystem.write"]["allowed"] is False
    assert by["docker.run"]["allowed"] is False

    graph = _graph_of(result)
    assert graph["permissions"]["schema"] == SCHEMA_PERMISSIONS
    assert "权限声明 allowed=4/6" in graph["summary"]


def test_capability_graph_entry_permissions_scoped_per_capability(tmp_path):
    src = _make_micro_repo(tmp_path)

    result = run_ingest(str(src), name="mini", vendor_root=tmp_path / "vendor")
    graph = _graph_of(result)

    assert [c["id"] for c in graph["capabilities"]] == [
        "shell.exec", "secret.request", "model.invoke",
        "vendor.api_miniprovider_example"]
    for entry in graph["capabilities"]:
        # 每条能力条目自带 license 留痕 + 六族全枚举的 permissions 节
        assert entry["license"] == graph["license"]
        assert [p["action"] for p in entry["permissions"]] == list(PERMISSION_FAMILIES)

    by_id = {c["id"]: c for c in graph["capabilities"]}
    opened = {cid: sorted(p["action"] for p in e["permissions"] if p["allowed"])
              for cid, e in by_id.items()}
    # 条目级声明只放开自己需要的族，不搭源级便车
    assert opened["shell.exec"] == ["shell.exec"]
    assert opened["secret.request"] == ["secret.request"]
    assert opened["model.invoke"] == ["network.*", "provider.invoke"]
    assert opened["vendor.api_miniprovider_example"] == ["network.*"]
    # 外部依赖条目的 network.* 钉在自己主机上
    dep_net = _perms_by_action(by_id["vendor.api_miniprovider_example"]["permissions"])["network.*"]
    assert dep_net["resources"] == ["api.miniprovider.example"]
    # 未放开族在条目级带通用 deny scope
    denied = [p for p in by_id["shell.exec"]["permissions"] if not p["allowed"]]
    assert all(p["scope"] == "本条目未声明该族（deny-by-default）" for p in denied)


def test_doc_reference_surfaces_never_open_permissions(tmp_path):
    """DOC_REFERENCE/DOC_* 是已清点、非运行时裁决点：文档命中的源权限全拒。"""
    src = tmp_path / "docsOnly"
    src.mkdir()
    (src / "README.md").write_text(
        "run subprocess.run(['ls']) per docs, export MY_TOKEN via os.getenv\n"
        "see https://api.example.com/v1/chat\n", encoding="utf-8")

    result = run_ingest(str(src), name="docs", vendor_root=tmp_path / "vendor")

    assert result["scan"]["surface_count"] > 0     # 命中被清点了
    assert result["permissions"]["declared"] == []
    assert result["permissions"]["denied"] == ["shell.exec", "filesystem.write",
                                               "network.*", "docker.run",
                                               "secret.request", "provider.invoke"]
    graph = _graph_of(result)
    assert all(not p["allowed"]
               for e in graph["capabilities"] for p in e["permissions"])


def test_derive_permissions_runtime_only_not_doc_hits(tmp_path):
    src = tmp_path / "mixed"
    src.mkdir()
    (src / "app.py").write_text(
        "import subprocess\nsubprocess.run(['echo'], check=False)\n", encoding="utf-8")
    (src / "docs").mkdir()
    (src / "docs" / "how.md").write_text(
        "also calls subprocess.run in examples\n", encoding="utf-8")

    surfaces, _ = ingest.scan_source(src)
    perms = _perms_by_action(derive_permissions(surfaces))

    assert perms["shell.exec"]["allowed"] is True
    assert perms["shell.exec"]["evidence"] == "app.py:2"   # 只认运行时面


def test_ingest_manifest_maps_to_restricted_grant_templates(tmp_path):
    """注册路径：落盘 manifest 的 permissions 节映射为受限 grant 模板。"""
    src = _make_micro_repo(tmp_path)

    result = run_ingest(str(src), name="mini", vendor_root=tmp_path / "vendor")
    graph = _graph_of(result)

    templates = manifest_grant_templates(graph, subject="absorbed:mini")
    keys = {t.action_key for t in templates["grants"]}
    assert keys == {
        "facai-native:shell.exec",
        "facai-native:secret.request:MINI_API_KEY",
        "facai-native:network.*:api.miniprovider.example",
        "facai-native:provider.invoke:api.miniprovider.example",
    }
    assert all(t.subject == "absorbed:mini" for t in templates["grants"])
    # deny-by-default：未声明族无模板；license UNKNOWN（微仓库无 LICENSE）留痕
    assert not any(":docker.run" in k or ":filesystem.write" in k for k in keys)
    assert all("license UNKNOWN 留痕" in t.note for t in templates["grants"])
    # copyleft 留痕同样跟随（license 追踪语义保持）
    graph["license"] = "AGPL-3.0"
    for entry in graph["capabilities"]:
        entry["license"] = "AGPL-3.0"
    agpl = manifest_grant_templates(graph, subject="absorbed:mini")
    assert all("license AGPL-3.0 留痕" in t.note for t in agpl["grants"])


def test_cli_report_prints_permission_declaration(tmp_path, capsys):
    src = _make_micro_repo(tmp_path)

    rc = ingest.main([str(src), "--name", "mini",
                      "--vendor-dir", str(tmp_path / "vendor"), "--offline"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "[perms]" in out and "deny-by-default" in out
    assert "manifest_grant_templates" in out
