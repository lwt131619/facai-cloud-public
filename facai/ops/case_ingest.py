"""FACAI 案例爬取入库（咨询案例 → cases 注册表）。

操作员指令 2026-10-13：把外部咨询/情报案例爬进 facai。诚实边界先说清：
facai 此前只有目标性抓取（github_digest 的 GitHub/威胁新闻 fetcher），
没有通用爬虫；本模块补的是最小可用的「抓取 → 正文抽取 → 边界筛查 →
案例入库」管道，不是全站爬虫（无调度、无去重队列、无 JS 渲染）。

治理纪律（与 skills 收录同源，一条不弱化——但按数据/能力分野分级）：

- 外源正文是不可信数据：每条案例必须过 BoundaryGovernor.screen 四类筛查。
  案例是数据不执行：法律/道德/授权类命中记录为题材标记（screen_notes
  随条目永久可见），不拦截；**宪章类（提示注入）照拦**——案例正文日后会
  作为上下文喂给模型，"忽略之前的所有指令"式的正文是真实投毒向量。
- cases 是数据不是能力：不进 skills 注册表、不参与派发、无远程权限可
  切断（它自己不执行任何东西）。
- 幂等：同 URL 内容未变 → skip；变了 → 更新并记 previous_sha（来源
  改了什么永远可追溯）。
- 所有者主权：入库 owner 域隔离；跳过/警告全部留痕在报告里，不静默。

CLI: ``python -m facai.ops.case_ingest <url>... --out report.json``
（离线测试用 httpx.MockTransport 注入，不打真网。）
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

FETCH_TIMEOUT = 20.0
MAX_BODY_BYTES = 2_000_000          # 超过即告警跳过：案例不是镜像站
CASE_TEXT_LIMIT = 30_000            # 入库正文的字符上限
_SUPPORTED_TYPES = ("text/html", "text/plain", "application/xhtml+xml")
# 公开技术博客（CSDN 等）按浏览器 UA 提供正文；默认 httpx UA 会被 403。
_BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class _TextExtractor(HTMLParser):
    """Stdlib 正文抽取：去掉 script/style/head/noscript，文本拼接归一。"""

    _SKIP = {"script", "style", "head", "noscript", "template",
             "nav", "footer", "aside"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("p", "br", "li", "tr", "h1", "h2", "h3", "h4", "section"):
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip_depth and data.strip():
            self._chunks.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", "".join(self._chunks))).strip()


def fetch_and_extract(http_client, url: str) -> dict:
    """One URL → {title, text} or raise ValueError with the honest reason."""
    response = http_client.get(url)
    if response.status_code != 200:
        raise ValueError(f"HTTP {response.status_code}")
    content_type = (response.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type and content_type not in _SUPPORTED_TYPES:
        raise ValueError(f"暂不支持的内容类型 {content_type}（PDF 等二进制源留待后续）")
    if len(response.content) > MAX_BODY_BYTES:
        raise ValueError(f"页面超过 {MAX_BODY_BYTES // 1000}KB 上限")
    raw = response.text
    if content_type in ("text/html", "application/xhtml+xml"):
        extractor = _TextExtractor()
        extractor.feed(raw)
        return {"title": extractor.title.strip()[:200], "text": extractor.text()[:CASE_TEXT_LIMIT]}
    return {"title": "", "text": raw[:CASE_TEXT_LIMIT]}


def case_id_for(url: str) -> str:
    return "c-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


# 案例筛查策略（2026-10-13 操作员指令：收录钓鱼/鱼叉/渗透教学案例）：
# skills 与 cases 的分野在执行性——skills 会跑，四类全 enforce；cases 是
# 参考数据（不执行、不派发），法律/道德/授权类命中只记录为题材标记
# （screen_notes 随条目永久可见），不拦截。唯一继续拦截的是宪章类：
# 案例正文日后会作为上下文喂给模型，提示注入式的正文是真实的投毒向量。
CASE_BLOCK_CLASSES = ("charter",)


def ingest_cases(governor, registries, urls, owner_id="admin", http_client=None) -> dict:
    """Run the pipeline over a URL list; one report row per URL."""
    import httpx
    client = http_client or httpx.Client(timeout=FETCH_TIMEOUT, follow_redirects=True,
                                         headers={"user-agent": _BROWSER_UA})
    try:
        rows = [ingest_one(governor, registries, client, url, owner_id) for url in urls]
    finally:
        if http_client is None:
            client.close()
    registered = sum(1 for r in rows if r["decision"] == "registered")
    updated = sum(1 for r in rows if r["decision"] == "updated")
    skipped = sum(1 for r in rows if r["decision"] == "skipped")
    blocked = sum(1 for r in rows if r["decision"] == "blocked")
    return {"schema": "facai.case-ingest/1",
            "at": datetime.now(timezone.utc).isoformat(),
            "total": len(rows), "registered": registered, "updated": updated,
            "skipped": skipped, "blocked": blocked, "results": rows}


def ingest_one(governor, registries, http_client, url: str, owner_id="admin") -> dict:
    url = url.strip()
    row = {"url": url, "decision": None}
    try:
        page = fetch_and_extract(http_client, url)
    except ValueError as exc:
        row.update(decision="skipped", reason=f"抓取失败：{exc}")
        return row
    except Exception as exc:                     # 网络/解码等一切外源异常
        row.update(decision="skipped", reason=f"抓取异常：{type(exc).__name__}")
        return row
    return register_case(governor, registries, url, page["title"], page["text"],
                         owner_id=owner_id)


def register_case(governor, registries, url: str, title: str, text: str,
                  owner_id="admin") -> dict:
    """The governed admission step, shared by the fetch path and pre-fetched
    bodies (anti-bot sources fetched by session tooling still pass through
    here — no ungoverned side door into the case library)."""
    row = {"url": url, "decision": None}
    verdicts = governor.screen(text)
    blockers = [hit for verdict in verdicts for hit in verdict["hits"]
                if verdict["violates"] and verdict["class"] in CASE_BLOCK_CLASSES]
    # 题材标记（观察留痕）：敏感题材记录在条目上，永久可见，不静默。
    notes = [{"class": v["class"], "label": v["label"], "hits": v["hits"]}
             for v in verdicts if v["hits"]]
    if blockers:
        row.update(decision="blocked",
                   reason="宪章类命中（案例正文会进入模型上下文，注入式正文即投毒）：" +
                          "；".join(h["reason"] for h in blockers))
        return row

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    key = case_id_for(url)
    existing = registries.list("cases", owner_id=owner_id).get(key)
    entry = {"source_url": url, "title": title, "sha256": digest,
             "size_chars": len(text), "fetched_at": _now(),
             "text": text[:CASE_TEXT_LIMIT], "kind": "case",
             "screen_notes": notes}
    if existing is not None:
        if existing.get("sha256") == digest:
            row.update(decision="skipped", reason="内容未变化", case_id=key)
            return row
        entry["previous_sha"] = existing.get("sha256")
        registries.register("cases", key, entry, owner_id=owner_id)
        row.update(decision="updated", case_id=key,
                   previous_sha=entry["previous_sha"])
        return row
    registries.register("cases", key, entry, owner_id=owner_id)
    row.update(decision="registered", case_id=key, title=title)
    return row


def _now():
    return datetime.now(timezone.utc).isoformat()


def main(argv=None, http_client=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    out = None
    data_dir = Path("data")
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    if "--data" in argv:
        i = argv.index("--data")
        data_dir = Path(argv[i + 1])
        del argv[i:i + 2]
    if not argv:
        print("usage: python -m facai.ops.case_ingest <url>... "
              "[--data DIR] [--out report.json]")
        return 2
    # 直接组件构造（与 create_app 同一套装配）：governor 状态与注册表都落在
    # --data 指定的真实根目录，断电存活。
    from ..store import Store
    from ..registries import Registries
    from ..boundary import BoundaryGovernor
    store = Store(data_dir)
    report = ingest_cases(BoundaryGovernor(data_dir), Registries(store), argv,
                          http_client=http_client)
    print(f"cases: {report['registered']} registered / {report['updated']} updated / "
          f"{report['skipped']} skipped / {report['blocked']} blocked")
    for r in report["results"]:
        print(f"  [{r['decision']:>10}] {r['url']}  {r.get('reason', '')}")
    if out:
        Path(out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                             encoding="utf-8")
        print(f"report: {out}")
    return 0 if not report["blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
