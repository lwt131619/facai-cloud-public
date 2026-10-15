"""[9] INGESTION AUTOMATION — 收录流水线一条命令。

把 docs/REROOT_PLAYBOOK 的手工八段（clone→扫描→AuthorityManifest→
CapabilityManifest→Verifier→Registry）代码化为一条命令::

    python -m facai.ops.ingest <本地路径或git URL> [--name xxx] [--offline]

阶段（与 HANDOFF-NEXT.md §十三 [9] 对应）：

1. clone     git URL 浅克隆进 ``<vendor>/<name>``；``--offline`` 跳过 clone、
             只收本地路径（给 URL 直接拒绝，不做任何网络动作）。目标目录已
             存在则复用，绝不重复克隆。本地路径原位扫描、不落 vendor。
2. scan      启发式初扫（明确标注为初扫，非人工清单）：先扫源码扩展名文件
             （.py/.ts/.js/.rs/.go/.tsx/.jsx/.mjs/.cjs/.java/.rb/.sh），扫完且
             源码配额未满才扫其余文件；文档/示例类命中 kind 加 DOC_ 前缀、
             URL 引用单列 DOC_REFERENCE 分类（单独计数，不挤占独立 200 的源码
             配额——INGEST_FIRST_RUN §7-1 实测反馈）。规则：正则扫 ``https?://``
             常量、``os.getenv``/``os.environ``/``process.env`` 键、
             ``subprocess``/``child_process`` 调用点、已知 SDK 客户端构造
             （OpenAI(/Anthropic(/httpx auth/requests.Session auth →
             INFERRED_CREDENTIAL，覆盖「密钥经 SDK 内部读取」的传递盲区），
             产出 surfaces 初稿，全部带 file:line 证据。分类规则：模型/推送
             端点→REMOTE_PROVIDER；env 凭据与 SDK 推断凭据→CREDENTIAL
             LOCAL_BROKERABLE；子进程→SHELL_EXEC LOCAL_BROKERABLE；文档/示例
             URL 引用→DOC_REFERENCE；其余 UNKNOWN。
3. manifest  写 ``<vendor>/authority-manifests/<name>.json``（schema 与既有
             九源一致：target/origin/managementClass 初判 T2/summary/surfaces/
             reRooting=空待人工）+ ``<vendor>/capability-graph/<name>.json``：
             能力条目带 Dify 插件 manifest 式 ``permissions`` 节——六族
             capability action（shell.exec/filesystem.write/network.*/
             docker.run/secret.request/provider.invoke，与
             facai/broker.CAPABILITY_ACTIONS 逐字对齐）逐条 allowed:true/false
             + scope 描述；deny-by-default：只有运行时面（非 DOC_）带
             file:line 证据的族才放开。已存在的人工清单/图一律拒绝覆盖
             （fail-closed）。注册侧经 facai/capabilities.py 的
             grant_templates_for/manifest_grant_templates 把该节映射为受限
             grant 模板（subject + backend:action[:resource]、有界 ttl/uses、
             risk 标签，照 authority.CapabilityGrant 语义）。
4. verdict   调 :func:`facai.ops.verifier.verify_source` 出 T1/T2 判定，复核
             INDEX.json 的 verification 结构。新收录源无任何接线声明 → 如实
             T2_PARTIAL，绝不虚报。
5. report    打印收录报告（新增能力候选/外部依赖/未纳管面计数）。

铁律：本命令**绝不自动登记**进 CapabilityRegistry——登记始终过
:func:`facai.ops.verifier.register_with_gate` 门，由人工或后续批次触发。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from ..capabilities import (
    PERMISSION_FAMILIES,
    SCHEMA_PERMISSIONS,
    capability_diff,
    denied_permissions,
)
from .foundry_seed import seed as seed_foundry
from .verifier import verify_source

# --- 初扫口径（启发式，非人工清单） -----------------------------------------

SURFACE_LIMIT = 200            # 收录初扫面上限
MAX_FILE_BYTES = 1_000_000     # 单文件超 1MB 视为非审阅文本，跳过
EVIDENCE_LINES_MAX = 3         # 同一面在同一文件里最多保留的证据行数

SCHEMA_GRAPH = "facai.capability-graph/1"

REMOTE_PROVIDER = "REMOTE_PROVIDER"
LOCAL_BROKERABLE = "LOCAL_BROKERABLE"
# 文档/示例链接引用（INGEST_FIRST_RUN §7-2 实测反馈）：已清点、非运行时裁决点。
# 独立分类，绝不进 REMOTE_PROVIDER 误报池；remoteAuthorityDependency 恒 false。
DOC_REFERENCE = "DOC_REFERENCE"
UNKNOWN = "UNKNOWN"

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".tox", ".idea", ".vscode", "dist", "build", "target",
}
SKIP_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".svg", ".pdf",
    ".zip", ".gz", ".tgz", ".tar", ".bz2", ".7z", ".exe", ".dll", ".so",
    ".dylib", ".bin", ".o", ".a", ".obj", ".pyc", ".pyo", ".class", ".jar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".avi",
    ".mov", ".wav", ".ogg", ".db", ".sqlite", ".sqlite3", ".gguf", ".onnx",
    ".pb", ".h5", ".parquet", ".arrow", ".wasm", ".lib", ".pdb", ".snap",
    ".lock",
}
SKIP_SUFFIXES = (".min.js", ".min.css")

_URL_RE = re.compile(r"https?://[^\s\"'`\\)\]}>,;]+")
_ENV_RULES = (
    ("os.getenv", re.compile(r"\bos\.getenv\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']")),
    ("os.environ.get", re.compile(r"\bos\.environ\.get\(\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']")),
    ("os.environ[]", re.compile(r"\bos\.environ\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]")),
    ("process.env", re.compile(r"\bprocess\.env\.([A-Za-z_][A-Za-z0-9_]*)")),
    ("process.env[]", re.compile(r"\bprocess\.env\[\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']\s*\]")),
)
_SUBPROC_RE = re.compile(
    r"\bsubprocess\.(?:run|Popen|call|check_output|check_call|getoutput|getstatusoutput)\b"
    r"|\bos\.(?:system|popen|exec\w+|spawn\w+)\b"
    r"|\bchild_process\b"
)
# 模型/推送类端点启发式：命中即初判 remoteAuthorityDependency=true（待人工复核）
_REMOTE_HINTS_RE = re.compile(
    r"api\.|/api\b|/v1\b|\bmodel|\bllm\b|complet|chat|push|notify|webhook|"
    r"telemetry|posthog|analytics|oauth|token",
    re.I,
)
# env 凭据键启发式：命中即 CREDENTIAL/LOCAL_BROKERABLE（应走 Secret Broker）
_CRED_HINTS_RE = re.compile(r"KEY|TOKEN|SECRET|PASSWD|PASSWORD|CREDENTIAL|API_?KEY|AUTH", re.I)

# 源码扩展名（INGEST_FIRST_RUN §7-1 实测反馈）：先扫源码、扫完且源码配额未满
# 才扫其余文件——examples/ 抓取 JSON 不得再挤掉真实源码面。源码配额独立 200。
SOURCE_EXTS = {
    ".py", ".ts", ".js", ".rs", ".go", ".tsx", ".jsx", ".mjs", ".cjs",
    ".java", ".rb", ".sh",
}
_DOC_EXTS = (".md", ".rst", ".txt")
_DOC_SEGMENTS = {"docs", "doc", "examples", "example"}

# 已知 SDK 客户端构造（INGEST_FIRST_RUN §7-3 实测反馈）：源码中命中即产
# INFERRED_CREDENTIAL 面——「密钥经 SDK 内部读取」的传递性凭据流，只扫仓库
# 自身文本的 env 正则看不见它。标注 inferred via <SDK>，应走 Secret Broker。
_INFERRED_CRED_RULES = (
    ("openai", re.compile(r"\b(?:Async)?OpenAI\s*\(")),
    ("anthropic", re.compile(r"\b(?:Async)?Anthropic\s*\(")),
    ("httpx", re.compile(r"\bhttpx\.(?:Async)?Client\s*\([^)]*\bauth\b")),
    ("requests", re.compile(r"\brequests\.Session\s*\([^)]*\b(?:headers|auth)\s*=")),
)


def _is_doc_file(rel: str) -> bool:
    """文档/示例类文件：.md/.rst/.txt 结尾，或路径含 docs/examples/README 段。"""
    lowered = rel.lower()
    if lowered.endswith(_DOC_EXTS):
        return True
    return any(seg in _DOC_SEGMENTS or seg.startswith("readme")
               for seg in lowered.split("/"))

_DRAFT_SUMMARY_PREFIX = "初扫"   # 本流水线产物的 summary 前缀，覆盖守卫据此放行


class IngestError(ValueError):
    """收录输入不合法或动作被守卫拒绝（fail-closed，不留半成品产出）。"""


def _is_git_url(target: str) -> bool:
    t = target.strip()
    return (t.startswith(("http://", "https://", "git@", "ssh://", "git://"))
            or t.lower().endswith(".git"))


def derive_name(target: str) -> str:
    """从 target 尾段推导收录名（缺省名兜底，保证能落盘）。"""
    tail = re.split(r"[\\/]+", str(target).strip().rstrip("/\\"))[-1]
    if tail.lower().endswith(".git"):
        tail = tail[: -len(".git")]
    name = re.sub(r"[^a-z0-9._-]+", "-", tail.lower()).strip("-.")
    return name or "ingested-source"


def ensure_clone(url: str, dest: Path) -> tuple[str, Path]:
    """浅克隆 url 进 dest；dest 已存在且非空则复用（绝不重复克隆）。"""
    if dest.exists() and any(dest.iterdir()):
        return "reused", dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", str(url), str(dest)],
        capture_output=True, text=True)
    if proc.returncode != 0 or not dest.exists():
        detail = (proc.stderr or proc.stdout or "").strip()
        raise IngestError(f"git clone 失败（exit={proc.returncode}）：{detail[:400]}")
    return "cloned", dest


# --- license 初探（best-effort；UNKNOWN 是诚实的默认值） ---------------------

_LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE",
                  "COPYING", "COPYING.md", "COPYING.txt")
_LICENSE_PATTERNS = (
    (re.compile(r"apache license", re.I), "Apache-2.0"),
    (re.compile(r"\bMIT License\b|permission is hereby granted", re.I), "MIT"),
    (re.compile(r"gnu Affero general public license", re.I), "AGPL-3.0"),
    (re.compile(r"gnu general public license", re.I), "GPL-3.0"),
    (re.compile(r"mozilla public license", re.I), "MPL-2.0"),
    (re.compile(r"\bISC License\b|isc\.org", re.I), "ISC"),
)


def detect_license(root: Path) -> str:
    """根目录 license 文件文本初探；认不出就如实返回 UNKNOWN。"""
    for name in _LICENSE_FILES:
        for cand in (root / name,):
            if cand.is_file():
                text = cand.read_text(encoding="utf-8", errors="replace")[:4096]
                for pattern, spdx in _LICENSE_PATTERNS:
                    if pattern.search(text):
                        return spdx
    return "UNKNOWN"


# --- scan 阶段：启发式初扫，全部带 file:line 证据 ----------------------------


def _add_surface(surfaces: list, index: dict, key: tuple, surface: dict,
                 evidence_line: str, cap: int) -> bool:
    """同键去重（同文件内补证据行）；满了返回 False 由调用方标 truncated。"""
    existing = index.get(key)
    if existing is not None:
        lines = existing["_evidence_lines"]
        if evidence_line not in lines and len(lines) < EVIDENCE_LINES_MAX:
            lines.append(evidence_line)
            existing["evidence"] = "\n".join(lines)
        return True
    if len(surfaces) >= cap:
        return False
    surface["evidence"] = evidence_line
    surface["_evidence_lines"] = [evidence_line]
    index[key] = surface
    surfaces.append(surface)
    return True


def _collect_repo_files(root: Path) -> tuple[list[Path], list[Path]]:
    """按既有 os.walk 口径收集可扫描文本文件，分成（源码, 其余）两桶。

    两桶各自保持原来的字母序遍历顺序；跳过规则（SKIP_DIRS/EXTS/SUFFIXES）
    与修复前完全一致。
    """
    source: list[Path] = []
    other: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        base = Path(dirpath)
        for fn in sorted(filenames):
            path = base / fn
            if path.suffix.lower() in SKIP_EXTS or fn.endswith(SKIP_SUFFIXES):
                continue
            (source if path.suffix.lower() in SOURCE_EXTS else other).append(path)
    return source, other


def _scan_file(path: Path, root: Path, surfaces: list, index: dict, cap: int,
               stats: dict, *, infer: bool, allow_doc_demotion: bool) -> bool:
    """扫单个文件入 (surfaces, index)，就地累加 stats["files"]。

    返回是否因档位配额打满而截断。``allow_doc_demotion``（其余文件 pass 开启）：
    文档/示例类文件命中降权——kind 加 DOC_ 前缀，URL 引用改判 DOC_REFERENCE
    （remoteAuthorityDependency 恒 false），单独计数、不挤占源码配额。
    ``infer``（仅源码 pass）：命中已知 SDK 客户端构造 → INFERRED_CREDENTIAL 面。
    """
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    stats["files"] += 1
    rel = path.relative_to(root).as_posix()
    demote = allow_doc_demotion and _is_doc_file(rel)
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue

        for match in _URL_RE.finditer(line):
            url = match.group(0).rstrip(".,;:")
            if demote:
                surface = {
                    "kind": "DOC_NETWORK",
                    "where": f"{rel}:{lineno}",
                    "what": ("初扫：文档/示例文件中的 URL 引用（DOC_REFERENCE——"
                             "已清点、非运行时裁决点，不进 REMOTE_PROVIDER 误报池）"),
                    "classification": DOC_REFERENCE,
                    "remoteAuthorityDependency": False,
                    "endpoint": url,
                }
            else:
                remote = bool(_REMOTE_HINTS_RE.search(url))
                surface = {
                    "kind": "NETWORK",
                    "where": f"{rel}:{lineno}",
                    "what": ("初扫：硬编码 http(s) 端点常量——命中模型/推送类关键词，"
                             "初判远端裁决（remoteAuthorityDependency=true，待人工复核）"
                             if remote else
                             "初扫：硬编码 http(s) 端点常量（未命中模型/推送类关键词，初判 UNKNOWN）"),
                    "classification": REMOTE_PROVIDER if remote else UNKNOWN,
                    "remoteAuthorityDependency": remote,
                    "endpoint": url,
                }
            if not _add_surface(surfaces, index, ("NETWORK", url, rel), surface,
                                f"{rel}:{lineno}  {line[:240]}", cap):
                return True

        for rule_name, pattern in _ENV_RULES:
            for match in pattern.finditer(line):
                env_key = match.group(1)
                cred = bool(_CRED_HINTS_RE.search(env_key))
                surface = {
                    "kind": ("DOC_CREDENTIAL" if cred else "DOC_CONFIG") if demote
                            else ("CREDENTIAL" if cred else "CONFIG"),
                    "where": f"{rel}:{lineno}",
                    "what": ("初扫：env 凭据键——应由 Facai Secret Broker 下发"
                             "（LOCAL_BROKERABLE）"
                             if cred else
                             "初扫：env 配置键（未识别凭据语义，初判 UNKNOWN）"),
                    "classification": LOCAL_BROKERABLE if cred else UNKNOWN,
                    "remoteAuthorityDependency": False,
                    "env_key": env_key,
                    "rule": rule_name,
                }
                if not _add_surface(surfaces, index, ("ENV", env_key, rel), surface,
                                    f"{rel}:{lineno}  {line[:240]}", cap):
                    return True

        for match in _SUBPROC_RE.finditer(line):
            call = match.group(0)
            surface = {
                "kind": "DOC_SHELL_EXEC" if demote else "SHELL_EXEC",
                "where": f"{rel}:{lineno}",
                "what": "初扫：子进程/shell 调用点——可由 Facai Broker 收口（LOCAL_BROKERABLE）",
                "classification": LOCAL_BROKERABLE,
                "remoteAuthorityDependency": False,
                "call": call,
            }
            if not _add_surface(surfaces, index, ("SUBPROC", call, rel), surface,
                                f"{rel}:{lineno}  {line[:240]}", cap):
                return True

        if infer:
            for sdk, pattern in _INFERRED_CRED_RULES:
                if pattern.search(line):
                    surface = {
                        "kind": "INFERRED_CREDENTIAL",
                        "where": f"{rel}:{lineno}",
                        "what": (f"初扫：{sdk} SDK 客户端构造点——凭据经该 SDK 内部读取"
                                 f"（inferred via {sdk}，传递性凭据），应由 Facai "
                                 "Secret Broker 下发（LOCAL_BROKERABLE）"),
                        "classification": LOCAL_BROKERABLE,
                        "remoteAuthorityDependency": False,
                        "sdk": sdk,
                        "inferred": True,
                    }
                    if not _add_surface(surfaces, index, ("INFERRED", sdk, rel), surface,
                                        f"{rel}:{lineno}  {line[:240]}", cap):
                        return True
    return False


def scan_source(root: Path, limit: int = SURFACE_LIMIT) -> tuple[list[dict], dict]:
    """启发式初扫一个源码根，返回 (surfaces, meta)。

    扫描顺序（INGEST_FIRST_RUN §7-1 实测反馈后的修复）：先扫源码扩展名文件
    （SOURCE_EXTS），扫完且源码配额未满才扫其余文件。其余文件里文档/示例类
    命中 kind 加 DOC_ 前缀、URL 引用单列 DOC_REFERENCE 分类，单独计数，绝不
    挤占源码配额——源码配额独立 limit（其余文件档另有独立 limit）。
    surfaces 元素形如既有 manifest 的面（kind/where/what/classification/
    remoteAuthorityDependency/evidence，外加初扫专属的 rule/endpoint/env_key/
    call/sdk 字段）。同一 (规则, 键, 文件) 去重；每档各自封顶 limit。
    """
    surfaces: list[dict] = []
    index: dict[tuple, dict] = {}
    src_paths, rest_paths = _collect_repo_files(root)

    # pass 1：源码文件，独立配额 limit
    stats_src = {"files": 0}
    truncated = False
    for path in src_paths:
        if truncated:
            break
        if _scan_file(path, root, surfaces, index, limit, stats_src,
                      infer=True, allow_doc_demotion=False):
            truncated = True
    source_surface_count = len(surfaces)

    # pass 2：其余文件（仅源码配额未满时才扫）；文档/示例命中降权 DOC_，
    # 该档独立配额 limit，不挤占源码档。
    stats_rest = {"files": 0}
    doc_truncated = False
    rest_added_before = len(surfaces)
    if not truncated and len(surfaces) < limit:
        rest_cap = len(surfaces) + limit
        for path in rest_paths:
            if doc_truncated:
                break
            if _scan_file(path, root, surfaces, index, rest_cap, stats_rest,
                          infer=False, allow_doc_demotion=True):
                doc_truncated = True
    pass2 = surfaces[rest_added_before:]
    doc_surface_count = sum(1 for s in pass2 if str(s["kind"]).startswith("DOC_"))
    other_surface_count = len(pass2) - doc_surface_count

    for surface in surfaces:
        surface.pop("_evidence_lines", None)
    by_class = Counter(s["classification"] for s in surfaces)
    by_kind = Counter(s["kind"] for s in surfaces)
    meta = {
        "files_scanned": stats_src["files"] + stats_rest["files"],
        "source_files_scanned": stats_src["files"],
        "other_files_scanned": stats_rest["files"],
        "surface_count": len(surfaces),
        "source_surface_count": source_surface_count,
        "doc_surface_count": doc_surface_count,
        "other_surface_count": other_surface_count,
        "limit": limit,
        "truncated": truncated,
        "doc_truncated": doc_truncated,
        "by_classification": dict(sorted(by_class.items())),
        "by_kind": dict(sorted(by_kind.items())),
    }
    return surfaces, meta


# --- 能力候选 / 外部依赖推导（骨架级，登记前必须过人工与 Verifier） ----------

_CAP_TEMPLATES = (
    # (命中面 kind, 候选 id, provider 契约, 候选名, native_candidate)
    ("SHELL_EXEC", "shell.exec", "ExecutionProvider",
     "子进程/shell 执行面（初扫推导候选，待 Capability Miner 人工复核）", True),
    ("CREDENTIAL", "secret.request", "CapabilityProvider",
     "凭据获取面（初扫推导候选，吸收后一律走 Secret Broker）", True),
    ("REMOTE_PROVIDER", "model.invoke", "ModelProvider",
     "远端模型/推送调用面（初扫推导候选，须 re-root 至 Facai Model Router）", False),
)


def derive_capability_candidates(surfaces: list[dict]) -> list[dict]:
    caps = []
    for kind, cap_id, provider, name, native in _CAP_TEMPLATES:
        first = next(
            (s for s in surfaces
             if s["kind"] == kind
             # INFERRED_CREDENTIAL（SDK 推断的传递性凭据）同样是凭据获取面：
             # 吸收后一律走 Secret Broker，与 CREDENTIAL 同推 secret.request。
             or (kind == "CREDENTIAL"
                 and "CREDENTIAL" in str(s.get("kind", "")).upper())
             or (kind == "REMOTE_PROVIDER" and s["classification"] == REMOTE_PROVIDER)),
            None)
        if first is not None:
            caps.append({
                "id": cap_id,
                "name": name,
                "level": "L0_REFERENCE",   # 只读过设计，未吸收实现——如实标 L0
                "provider": provider,
                "verdict": "new",
                "evidence": first["where"],
                "remote_removal": "初扫不产生 reRoot 提案——待人工按先验清单格式补",
                "native_candidate": native,
            })
    return caps


def _url_host(url: str) -> str:
    match = re.match(r"https?://([^/:?#]+)", url)
    return match.group(1).lower() if match else url


def _dep_id(host: str) -> str:
    return "vendor." + re.sub(r"[^a-z0-9_]+", "_", host)


def derive_external_dependencies(surfaces: list[dict]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for surface in surfaces:
        if surface.get("kind") != "NETWORK" or not surface.get("endpoint"):
            continue
        host = _url_host(surface["endpoint"])
        if host in seen:
            continue
        seen.add(host)
        out.append({
            "id": _dep_id(host),
            "verdict": "dependency",
            "host": host,
            "evidence": surface["where"],
        })
    return out


# --- 权限声明（Dify 插件 manifest 对标吸收） ----------------------------------
# 词表与 facai/broker.CAPABILITY_ACTIONS 逐字对齐（facai.capabilities.
# PERMISSION_FAMILIES；test_capabilities 有跨模块同步断言）。deny-by-default：
# 只有运行时面（kind 不带 DOC_ 前缀）带 file:line 证据的族才 allowed=true；
# DOC_REFERENCE/DOC_* 是已清点、非运行时裁决点的类，永不放开权限。


def derive_permissions(surfaces: list[dict]) -> list[dict]:
    """初扫权限声明：六族全枚举，未声明=拒绝。

    映射（只认运行时面；文档/示例 DOC_ 命中永不放开）：
    - SHELL_EXEC → shell.exec（scope 钉在子进程调用点）；
    - CREDENTIAL / INFERRED_CREDENTIAL → secret.request（应走 Secret Broker）；
    - NETWORK（运行时硬编码端点）→ network.*（scope 按主机；其中
      REMOTE_PROVIDER 类面另放 provider.invoke——远端 provider 调用须改根至
      Facai Model Router）；
    - filesystem.write / docker.run 初扫无启发式规则 → 恒 false。
    返回顺序固定为 PERMISSION_FAMILIES；每条 {action, allowed, scope,
    resources, evidence}。
    """
    perms = {p["action"]: dict(p)
             for p in denied_permissions("初扫无该族运行时面（deny-by-default）")}
    shell = next((s for s in surfaces if s.get("kind") == "SHELL_EXEC"), None)
    if shell is not None:
        perms["shell.exec"].update(
            allowed=True,
            scope="子进程/shell 调用点（吸收后经 Facai Broker 收口）",
            resources=[], evidence=shell["where"])
    cred = next((s for s in surfaces
                 if s.get("kind") in ("CREDENTIAL", "INFERRED_CREDENTIAL")), None)
    if cred is not None:
        key = str(cred.get("env_key") or cred.get("sdk") or "")
        perms["secret.request"].update(
            allowed=True,
            scope="凭据读取面（吸收后一律经 Facai Secret Broker 下发）",
            resources=[key] if key else [], evidence=cred["where"])
    runtime_net = [s for s in surfaces if s.get("kind") == "NETWORK"
                   and s.get("endpoint")]
    if runtime_net:
        hosts = sorted({_url_host(s["endpoint"]) for s in runtime_net})
        perms["network.*"].update(
            allowed=True,
            scope="运行时硬编码端点（按主机圈定；REMOTE_PROVIDER 者为远端裁决点，待人工复核改根）",
            resources=hosts, evidence=runtime_net[0]["where"])
    remote = [s for s in runtime_net
              if s.get("classification") == REMOTE_PROVIDER]
    if remote:
        hosts = sorted({_url_host(s["endpoint"]) for s in remote})
        perms["provider.invoke"].update(
            allowed=True,
            scope="远端模型/推送 provider 调用（须改根至 Facai Model Router）",
            resources=hosts, evidence=remote[0]["where"])
    return [perms[fam] for fam in PERMISSION_FAMILIES]


# capability 候选条目实际需要放开的族（Dify 式：各条目只声明它自己需要的）
_ENTRY_FAMILIES = {
    "shell.exec": ("shell.exec",),
    "secret.request": ("secret.request",),
    "model.invoke": ("network.*", "provider.invoke"),
}


def _entry_permissions(cap_id: str, source_perms: list[dict],
                       ext_host: str | None = None) -> list[dict]:
    """单条能力条目的权限节视图：六族全枚举，只放开该条目需要的族。

    其余族固定 allowed=false + 通用 scope——条目级声明不搭源级便车。
    外部依赖条目（vendor.<host>）放开 network.* 且 resources 钉在该主机上。
    """
    by_action = {p["action"]: p for p in source_perms}
    if cap_id.startswith("vendor."):
        opens = ("network.*",)
    else:
        opens = _ENTRY_FAMILIES.get(cap_id, ())
    out = []
    for fam in PERMISSION_FAMILIES:
        perm = dict(by_action[fam])
        if fam not in opens:
            perm["allowed"] = False
            perm["scope"] = "本条目未声明该族（deny-by-default）"
            perm["resources"] = []
            perm["evidence"] = ""
        elif ext_host is not None:
            perm["resources"] = [ext_host]
            perm["evidence"] = by_action[fam].get("evidence", "")
        out.append(perm)
    return out


def build_capability_graph(name: str, license_id: str, surfaces: list[dict],
                           diff: dict, cap_candidates: list[dict],
                           ext_deps: list[dict]) -> dict:
    """vendor/capability-graph/<name>.json：候选/依赖落位 + Dify 式 permissions 节。

    源级 permissions（六族全枚举，deny-by-default）落在顶层
    ``permissions``（带 ``facai.permissions/1`` schema 标记）；每条能力条目
    另带自己的 ``permissions`` 节与 ``license`` 留痕，注册侧经
    facai.capabilities.manifest_grant_templates 映射为受限 grant 模板。
    逐条 verdict 与 remote_removal 仍待人工复核。
    """
    source_perms = derive_permissions(surfaces)
    declared = [p["action"] for p in source_perms if p["allowed"]]
    denied = [p["action"] for p in source_perms if not p["allowed"]]
    entries = [dict(c) for c in cap_candidates]
    for dep in ext_deps:
        entries.append({
            "id": dep["id"],
            "name": f"外部依赖 {dep['host']}（初扫依赖候选，非能力）",
            "level": "L0_REFERENCE",
            "provider": "ToolProvider",
            "verdict": "dependency",
            "evidence": dep["evidence"],
            "remote_removal": "待人工判定是否可自托管/内网化",
            "native_candidate": False,
        })
    ext_hosts = {dep["id"]: dep["host"] for dep in ext_deps}
    for entry in entries:
        # license 逐条留痕（copyleft/UNKNOWN 追踪语义与登记侧一致）
        entry["license"] = license_id
        entry["permissions"] = _entry_permissions(
            entry["id"], source_perms, ext_hosts.get(entry["id"]))
    remote_n = sum(1 for s in surfaces if s["classification"] == REMOTE_PROVIDER)
    docref_n = sum(1 for s in surfaces if s["classification"] == DOC_REFERENCE)
    return {
        "schema": SCHEMA_GRAPH,
        "source": name,
        "license": license_id,
        "method": (
            "facai/ops/ingest.py 启发式初扫：先扫源码扩展名文件、扫完且配额未满才扫"
            "其余文件（文档/示例命中 kind 加 DOC_ 前缀、URL 引用单列 DOC_REFERENCE，"
            "不挤占源码配额）；正则扫 https?:// 常量 / os.getenv|os.environ|process.env "
            "键 / subprocess|child_process 调用点 / 已知 SDK 客户端构造"
            "（OpenAI(/Anthropic(/httpx auth/requests.Session auth → INFERRED_CREDENTIAL），"
            "逐条 file:line 证据，每档上限 200 面；能力候选按面种推导，未做人工 "
            "Capability Miner 比对——本文件是骨架，逐条 verdict 与 remote_removal 待复核。"
            "permissions 节照 Dify 插件 manifest 模式：六族 capability action 全枚举、"
            "deny-by-default，只有运行时面带 file:line 证据的族才 allowed=true。"),
        "summary": (
            f"初扫 {len(surfaces)} 面（REMOTE_PROVIDER={remote_n}，"
            f"DOC_REFERENCE={docref_n}）；"
            f"能力候选 {len(cap_candidates)} 条 / 外部依赖 {len(ext_deps)} 个主机；"
            f"diff: new={len(diff['new'])} already_owned={len(diff['already_owned'])} "
            f"external_dependency={len(diff['external_dependency'])}；"
            f"权限声明 allowed={len(declared)}/{len(PERMISSION_FAMILIES)}"
            f"（deny-by-default）。骨架待人工补全。"),
        "permissions": {
            "schema": SCHEMA_PERMISSIONS,
            "declared": declared,
            "denied": denied,
            "entries": source_perms,
        },
        "capabilities": entries,
    }


# --- 覆盖守卫：人工清单绝不许被初扫草案冲掉 ----------------------------------


def _guard_draft_overwrite(path: Path, label: str) -> None:
    if not path.exists():
        return
    try:
        summary = str(json.loads(path.read_text(encoding="utf-8")).get("summary", ""))
    except (OSError, ValueError):
        summary = ""
    if not summary.startswith(_DRAFT_SUMMARY_PREFIX):
        raise IngestError(
            f"{path} 已存在且不是本流水线的初扫产物（疑为人工清单/图，{label}）："
            "拒绝覆盖——请改用 --name 换名收录，或人工确认后移走旧件")


# --- 主流程 ------------------------------------------------------------------


def _default_vendor_root() -> Path:
    env = os.getenv("FACAI_VENDOR_ROOT")
    if env:
        return Path(env)
    repo_root = Path(__file__).resolve().parents[2]
    sibling = repo_root.parent / "vendor"
    if sibling.is_dir():
        return sibling
    return repo_root / "vendor"


def run_ingest(target: str, name: str | None = None, offline: bool = False,
               vendor_root: str | Path | None = None,
               limit: int = SURFACE_LIMIT) -> dict:
    """收录一个源：clone→初扫→manifest→verifier→报告。绝不登记 Registry。"""
    target = str(target).strip()
    if not target:
        raise IngestError("target 不能为空")
    is_url = _is_git_url(target)
    if is_url and offline:
        raise IngestError("--offline 只收本地路径，拒绝 git URL（不做任何网络动作）")
    name = name or derive_name(target)
    vendor = Path(vendor_root) if vendor_root else _default_vendor_root()
    manifests_dir = vendor / "authority-manifests"
    graphs_dir = vendor / "capability-graph"
    manifest_path = manifests_dir / f"{name}.json"
    graph_path = graphs_dir / f"{name}.json"

    # 1. clone
    if is_url:
        clone_status, source_root = ensure_clone(target, vendor / name)
    else:
        source_root = Path(target)
        if not source_root.is_dir():
            raise IngestError(f"本地路径不存在或不是目录：{source_root}")
        clone_status = "local"

    # 2. scan（启发式初扫）
    surfaces, scan_meta = scan_source(source_root, limit=limit)
    license_id = detect_license(source_root)

    # 3. manifest（schema 与既有九源一致；reRooting=空待人工）
    manifest = {
        "target": f"{name} (初扫收录实例 {source_root})",
        "origin": target if is_url else f"本地路径 {source_root}",
        "managementClass": (
            "T2 PARTIALLY_MANAGED —— 初判：收录流水线启发式初扫产物，未经人工复核与 "
            "re-root；管理级别以 Verifier 判定为准（见收录报告 verification 段）"),
        "summary": (
            f"初扫（启发式，非人工清单）：先扫源码扩展名、扫完且配额未满才扫其余文件；"
            f"扫描 {scan_meta['files_scanned']} 个文本文件（源码 "
            f"{scan_meta['source_files_scanned']}、其余 {scan_meta['other_files_scanned']}），"
            f"识别 {scan_meta['surface_count']} 面（源码档 {scan_meta['source_surface_count']}、"
            f"文档/示例 DOC_ 降权 {scan_meta['doc_surface_count']}、其余文件 "
            f"{scan_meta['other_surface_count']}；截断={scan_meta['truncated']}/"
            f"{scan_meta['doc_truncated']}，每档上限 {scan_meta['limit']}）；"
            "分类规则：模型/推送端点→REMOTE_PROVIDER、env 凭据→CREDENTIAL "
            "LOCAL_BROKERABLE、子进程→SHELL_EXEC LOCAL_BROKERABLE、SDK 客户端构造→"
            "INFERRED_CREDENTIAL LOCAL_BROKERABLE（inferred via <SDK>）、文档/示例 "
            "URL 引用→DOC_REFERENCE（不进 REMOTE_PROVIDER 误报池）、其余 UNKNOWN；"
            "reRooting 留空待人工。"),
        "surfaces": surfaces,
        "reRooting": [],
    }
    cap_candidates = derive_capability_candidates(surfaces)
    ext_deps = derive_external_dependencies(surfaces)
    discovered = [{"id": c["id"], "verdict": c["verdict"],
                   "native_candidate": c["native_candidate"]} for c in cap_candidates]
    discovered += [{"id": d["id"], "verdict": d["verdict"]} for d in ext_deps]
    # 能力 diff 以现行 Foundry 登记册为基线（docs/CAPABILITY_REGISTRY.json 同源）
    diff = capability_diff(discovered, seed_foundry())
    graph = build_capability_graph(name, license_id, surfaces, diff,
                                   cap_candidates, ext_deps)

    _guard_draft_overwrite(manifest_path, "authority-manifest")
    _guard_draft_overwrite(graph_path, "capability-graph")
    manifests_dir.mkdir(parents=True, exist_ok=True)
    graphs_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    graph_path.write_text(json.dumps(graph, ensure_ascii=False, indent=2),
                          encoding="utf-8")

    # 4. verdict：新收录源无任何接线声明（gate_wired/secret_brokered/... 均不声明）
    #    → Verifier 如实给 T2_PARTIAL；license 探不出就如实 UNKNOWN。
    report = verify_source(name, surfaces, {"license": license_id})
    verification = {
        "status": report["verdict"],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "checks": report["checks"],
        "unmanaged": report["unmanaged"],
        "reasons": report["reasons"],
    }

    return {
        "source": name,
        "target": target,
        "origin": manifest["origin"],
        "root": str(source_root),
        "clone": {"status": clone_status, "path": str(source_root)},
        "scan": scan_meta,
        "manifest": str(manifest_path),
        "capability_graph": str(graph_path),
        "permissions": graph["permissions"],
        "capability_diff": diff,
        "verification": verification,
        "verdict": report["verdict"],
        "license": license_id,
        "registered": False,
    }


def print_report(result: dict) -> None:
    scan = result["scan"]
    diff = result["capability_diff"]
    verification = result["verification"]
    by_class = scan["by_classification"]
    by_kind = scan.get("by_kind", {})
    print("== FACAI 收录流水线 [9] INGESTION AUTOMATION（初扫=启发式，非人工清单）==")
    print(f"[源]       {result['source']}  ({result['target']})")
    print(f"[clone]    {result['clone']['status']} -> {result['clone']['path']}")
    print(f"[scan]     {scan['surface_count']} 面"
          f"（REMOTE_PROVIDER={by_class.get(REMOTE_PROVIDER, 0)} "
          f"LOCAL_BROKERABLE={by_class.get(LOCAL_BROKERABLE, 0)} "
          f"UNKNOWN={by_class.get(UNKNOWN, 0)}），"
          f"文件 {scan['files_scanned']} 个（源码 {scan.get('source_files_scanned', '?')}"
          f"/其余 {scan.get('other_files_scanned', '?')}），"
          f"截断={'是' if scan['truncated'] else '否'}/{'是' if scan.get('doc_truncated') else '否'}")
    # DOC_REFERENCE 独立一节（INGEST_FIRST_RUN §7-2）：文档/示例链接引用不算
    # REMOTE_PROVIDER 误报池成员，不产生 remote authority dependency。
    docref_n = by_class.get(DOC_REFERENCE, 0)
    doc_n = scan.get("doc_surface_count", 0)
    inferred_n = by_kind.get("INFERRED_CREDENTIAL", 0)
    if docref_n or doc_n:
        print(f"[docref]   DOC_REFERENCE={docref_n} 面（文档/示例 DOC_ 降权命中 "
              f"{doc_n}）——已清点、非运行时裁决点，remoteAuthorityDependency=false，"
              "不进 REMOTE_PROVIDER 误报池")
    if inferred_n:
        print(f"[inferred] INFERRED_CREDENTIAL={inferred_n} 面——凭据经已知 SDK 内部"
              "读取（传递性凭据，inferred via <SDK>），应走 Facai Secret Broker")
    print(f"[manifest] {result['manifest']}")
    print(f"[graph]    {result['capability_graph']}")
    perms = result["permissions"]
    print(f"[perms]    权限声明 allowed={len(perms['declared'])}/{len(PERMISSION_FAMILIES)}"
          f"（deny-by-default） declared={perms['declared']} denied={perms['denied']}；"
          "注册时经 capabilities.manifest_grant_templates 映射为受限 grant 模板")
    print(f"[verdict]  {verification['status']}  未纳管面={len(verification['unmanaged'])}"
          f"  降级理由: {'; '.join(verification['reasons']) or '无'}")
    print(f"[diff]     新增能力候选={len(diff['new'])} {diff['new']}  "
          f"外部依赖={len(diff['external_dependency'])} {diff['external_dependency']}  "
          f"已拥有={len(diff['already_owned'])}")
    print("[registry] 未登记（本命令绝不自动登记）——登记必须经 "
          "facai/ops/verifier.py:register_with_gate 门，人工或后续批次触发")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m facai.ops.ingest",
        description=("收录流水线一条命令：clone→初扫→AuthorityManifest+CapabilityGraph"
                     "→Verifier 判定→收录报告（绝不自动登记 Registry）。"))
    parser.add_argument("target", help="本地路径或 git URL")
    parser.add_argument("--name", default=None, help="收录名（默认取 target 尾段）")
    parser.add_argument("--offline", action="store_true",
                        help="离线模式：跳过 clone，只收本地路径")
    parser.add_argument("--vendor-dir", default=None,
                        help="vendor 根目录（默认 <仓库同级>/vendor，或 FACAI_VENDOR_ROOT）")
    parser.add_argument("--limit", type=int, default=SURFACE_LIMIT,
                        help=f"初扫面上限（默认 {SURFACE_LIMIT}）")
    args = parser.parse_args(argv)
    try:
        result = run_ingest(args.target, name=args.name, offline=args.offline,
                            vendor_root=args.vendor_dir, limit=args.limit)
    except IngestError as exc:
        print(f"ingest 失败：{exc}")
        return 1
    print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
