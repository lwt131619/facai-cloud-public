"""Daily GitHub digest: collect new notable repos, register them, push to WeChat.

Runs on the VPS under a systemd timer (08:05 Beijing / 00:05 UTC daily), so it
uses server traffic and works around the clock without any operator machine
being on. For every watched category it queries the GitHub search API for
repositories created in the last 24 hours, ranked by stars, then:

1. registers each repo into the skill registry as immediately usable
   capability metadata (name/description/license/source_url/stars/topics —
   the same shape as every other registered skill; 无审核: no review gate,
   import is the admission decision);
2. renders a compact categorized digest and sends it to the operator through
   the configured WeChat channel (the same provider the console uses). If the
   upstream is degraded the run logs honestly and the next day's run carries on.

Categories are defined positively (what to watch), covering AI/agents,
security research (CTF / reverse engineering / red-team lab tooling) and
developer tooling. Runs without network or with a degraded upstream are
honest no-ops, never fabricated digests.

Usage (on the server):
    .venv/bin/python -m facai.ops.github_digest [--dry-run]
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Positive category definitions: what the station watches. Each query is one
# GitHub search-API call; a token (GITHUB_TOKEN) raises the rate limit but is
# optional for this once-a-day volume.
CATEGORIES = (
    ("AI/Agent", "created:>{date} stars:>15 topic:ai-agents"),
    ("AI/Agent", "created:>{date} stars:>15 topic:llm-agent"),
    ("能力执行", "created:>{date} stars:>5 topic:agent-skills"),
    ("能力执行", "created:>{date} stars:>5 topic:mcp-server"),
    ("安全研究", "created:>{date} stars:>8 topic:ctf"),
    ("安全研究", "created:>{date} stars:>8 topic:reverse-engineering"),
    ("安全研究", "created:>{date} stars:>8 topic:redteam"),
    ("安全研究", "created:>{date} stars:>5 topic:sql-injection"),
    ("安全研究", "created:>{date} stars:>5 topic:penetration-testing"),
    ("开发工具", "created:>{date} stars:>30 topic:developer-tools"),
    ("开发工具", "created:>{date} stars:>20 topic:cli"),
)
PER_CATEGORY = 5

# 常备能力收录（操作员 2026-10-07 指令：拖库/注入/内网/Agent 技能库，收录
# github 开源的经典执行器，不只盯每天的新仓库）。每轮运行都过一遍
# register_repos（幂等：已存在的条目只刷新星级）；收录即可用（无审核）。
CURATED_CAPABILITY_REPOS = (
    {"id": "sqlmapproject/sqlmap", "name": "sqlmap",
     "description": "自动 SQL 注入与数据库接管（拖库/脱库）的事实标准工具",
     "license": "GPL-2.0", "source_url": "https://github.com/sqlmapproject/sqlmap",
     "stars": 0, "topics": ["sql-injection", "database", "security"]},
    {"id": "fortra/impacket", "name": "impacket",
     "description": "Windows/SMB/Kerberos 协议武器库：内网横向、凭据与远程执行",
     "license": "Apache-2.0", "source_url": "https://github.com/fortra/impacket",
     "stars": 0, "topics": ["pentest", "smb", "active-directory"]},
    {"id": "Pennyw0rth/NetExec", "name": "NetExec",
     "description": "内网横移与凭据复用瑞士军刀（CrackMapExec 后继）",
     "license": "BSD-2-Clause", "source_url": "https://github.com/Pennyw0rth/NetExec",
     "stars": 0, "topics": ["pentest", "redteam", "cme"]},
    {"id": "SpecterOps/BloodHound", "name": "BloodHound",
     "description": "Active Directory 攻击面图计算：最短攻击路径发现",
     "license": "Apache-2.0", "source_url": "https://github.com/SpecterOps/BloodHound",
     "stars": 0, "topics": ["redteam", "active-directory", "graph"]},
    {"id": "PowerShellMafia/PowerSploit", "name": "PowerSploit",
     "description": "PowerShell 后渗透模块合集：提权、持久化、侦察",
     "license": "BSD-3-Clause", "source_url": "https://github.com/PowerShellMafia/PowerSploit",
     "stars": 0, "topics": ["post-exploitation", "powershell", "redteam"]},
    {"id": "anthropics/skills", "name": "anthropics-skills",
     "description": "Agent Skills 技能库：可执行技能包（文档/代码/浏览器自动化）",
     "license": "Apache-2.0", "source_url": "https://github.com/anthropics/skills",
     "stars": 0, "topics": ["agent-skills", "skills", "automation"]},
)

# Threat-intel news feeds (reporting ABOUT incidents, not tooling): the
# awareness side of the digest. RSS/2.0, stdlib parsing, capped per source.
NEWS_FEEDS = (
    ("FreeBuf", "https://www.freebuf.com/feed"),
    ("The Hacker News", "https://feeds.feedburner.com/TheHackersNews"),
)
NEWS_PER_FEED = 8


def _yesterday():
    return (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%d")


def fetch_new_repos(http_client=None):
    """Query GitHub for the last day's notable repos per category.

    Returns ``{category: [repo, ...]}``; a failed query degrades to an empty
    list for that category instead of failing the whole run.
    """
    import httpx

    date = _yesterday()
    client = http_client or httpx.Client(timeout=30)
    own = http_client is None
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "facai-digest/1.0"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    result = {}
    try:
        for category, query in CATEGORIES:
            items = []
            try:
                response = client.get(
                    "https://api.github.com/search/repositories",
                    params={"q": query.format(date=date), "sort": "stars",
                            "order": "desc", "per_page": PER_CATEGORY},
                    headers=headers)
                response.raise_for_status()
                for repo in response.json().get("items", []):
                    items.append({
                        "id": repo.get("full_name", "").lower(),
                        "name": repo.get("name") or repo.get("full_name", ""),
                        "description": (repo.get("description") or "")[:160],
                        "license": ((repo.get("license") or {}).get("spdx_id")
                                    or "UNKNOWN"),
                        "source_url": repo.get("html_url", ""),
                        "stars": int(repo.get("stargazers_count") or 0),
                        "topics": (repo.get("topics") or [])[:8],
                    })
            except Exception:
                items = []  # one degraded query never sinks the digest
            result.setdefault(category, []).extend(items)
    finally:
        if own:
            client.close()
    # de-duplicate within a category (the same repo can match two queries)
    for category, items in result.items():
        seen, unique = set(), []
        for repo in sorted(items, key=lambda r: -r["stars"]):
            if repo["id"] and repo["id"] not in seen:
                seen.add(repo["id"])
                unique.append(repo)
        result[category] = unique[:PER_CATEGORY]
    return result


def curated_capability_repos(http_client=None):
    """The curated capability pins with live star counts (best effort).

    Star counts are display metadata; a failed lookup keeps 0 and never
    blocks registration — absorbing the capability matters, the number
    doesn't.
    """
    import httpx

    client = http_client or httpx.Client(timeout=30)
    own = http_client is None
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "facai-digest/1.0"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    repos = []
    try:
        for repo in CURATED_CAPABILITY_REPOS:
            item = dict(repo)
            try:
                response = client.get(f"https://api.github.com/repos/{repo['id']}",
                                      headers=headers)
                response.raise_for_status()
                item["stars"] = int(response.json().get("stargazers_count") or 0)
            except Exception:
                pass
            repos.append(item)
    finally:
        if own:
            client.close()
    return repos


def fetch_threat_news(http_client=None):
    """Latest threat-intel REPORTS from security news feeds (RSS).

    These are incident write-ups and coverage, not tooling. A feed that fails
    is skipped; the section simply shrinks that day.
    """
    import httpx
    import xml.etree.ElementTree as ET

    client = http_client or httpx.Client(timeout=30, follow_redirects=True)
    own = http_client is None
    result = {}
    try:
        for name, url in NEWS_FEEDS:
            items = []
            try:
                response = client.get(url, headers={"User-Agent": "facai-digest/1.0"})
                response.raise_for_status()
                root = ET.fromstring(response.content)
                for item in root.iter("item"):
                    title = (item.findtext("title") or "").strip()
                    link = (item.findtext("link") or "").strip()
                    if title and link:
                        items.append({"title": title, "link": link})
                    if len(items) >= NEWS_PER_FEED:
                        break
            except Exception:
                items = []
            result[name] = items[:NEWS_PER_FEED]
    finally:
        if own:
            client.close()
    return result


def translate_to_chinese(texts, root):
    """Translate a list of strings to Simplified Chinese via the bound model.

    Best effort, one batched call: on any failure the caller gets None and
    keeps the originals, so the digest never blocks on the translator. The
    model key comes from the registry's ``api_key_env`` (env-only, never
    logged).
    """
    try:
        from ..store import Store

        store = Store(root)
        picked = None
        for _key, m in sorted(store.registry_list("models", owner_id="admin").items()):
            endpoint, model = m.get("endpoint"), m.get("model")
            api_key = os.getenv(m.get("api_key_env") or "", "")
            if endpoint and model and api_key:
                picked = (endpoint.rstrip("/"), model, api_key)
                break
        if picked is None:
            return None
        endpoint, model, api_key = picked
        import httpx

        response = httpx.post(
            f"{endpoint}/chat/completions", timeout=90,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "messages": [
                {"role": "system",
                 "content": "你是安全资讯编辑。把输入 JSON 数组中的每一句英文翻译成简洁的简体中文，"
                            "一行一句，保留产品名/编号/CVE号等专有名词。只输出 JSON 字符串数组，"
                            "顺序与输入一一对应，不要输出任何其他内容。"},
                {"role": "user", "content": json.dumps(texts, ensure_ascii=False)}]})
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        import re as _re

        match = _re.search(r"\[.*\]", content, _re.S)
        if not match:
            return None
        arr = json.loads(match.group(0))
        if isinstance(arr, list) and len(arr) == len(texts):
            return [str(x) for x in arr]
    except Exception:
        return None
    return None


FONT_CANDIDATES = (
    # Linux/VPS first — production keeps resolving Noto/WQY exactly as before.
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    # Windows dev boxes, so the phase-2 pipeline is exercisable off the VPS.
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)
SLIDE_SIZE = (720, 1600)
SLIDE_SECONDS = 9      # content slides (repo cards / news dumps)
CARD_SECONDS = 2.5     # cover / end cards, so cards don't bloat the video
LINES_PER_SLIDE = 14   # grouping for plain-text (news) slides
MAX_VIDEO_REPOS = 12   # per-repo card cap, keeps the video short
COVER_TITLE = "FACAI 情报日报"
END_CARD_TEXT = "whstapp.lol\nFACAI Agent 舰队 7×24"


def _find_font():
    """Best-effort CJK font path for drawtext; None means no video tonight."""
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    fonts_dir = Path("/usr/share/fonts")
    if fonts_dir.is_dir():
        candidates = sorted(fonts_dir.rglob("*.tt[cf]"))
        if candidates:
            return str(candidates[0])
    return None


def _wrap_cjk(line, width=17):
    line = str(line)
    return "\n".join(line[i:i + width] for i in range(0, len(line), width)) or " "


def _filter_path(path):
    """Quote a path for use inside a drawtext filter option value.

    ffmpeg strips single quotes at the filtergraph level, then splits options
    on raw ':' — so Windows drive-letter colons must additionally be
    backslash-escaped (the documented two-level filtergraph escaping). Text
    still never passes through any shell.
    """
    return "'" + str(path).replace("\\", "/").replace(":", "\\:") + "'"


def build_slides(sections, repos_meta=None, date=None):
    """Pure slide-builder for the digest video (no I/O, unit-testable).

    Layout: slide 0 is the cover card — big title, the date and the honest
    repo/news counts; then one card per repo (name, ⭐stars, wrapped
    description, URL) capped at the first ``MAX_VIDEO_REPOS``; then the
    remaining ``sections`` (threat news) grouped ``LINES_PER_SLIDE`` per
    plain-text slide as before; the last slide is the end card. Callers pace
    slides 0 and -1 at CARD_SECONDS and everything between at SLIDE_SECONDS.
    """
    date = date or _yesterday()
    repos = list(repos_meta or [])
    news_count = sum(len(lines) for _heading, lines in sections)
    cover = [COVER_TITLE, "", date]
    if repos:
        cover += ["", f"新收录 {len(repos)} 个开源能力"]
    if news_count:
        cover += [f"{news_count} 条威胁通报"]
    slides = ["\n".join(cover)]

    for repo in repos[:MAX_VIDEO_REPOS]:
        card = []
        category = repo.get("category")
        if category:
            card.append(f"【新项目 · {category}】")
        card += [str(repo.get("name", "")), f"⭐ {repo.get('stars', 0)}", ""]
        description = str(repo.get("description") or "").strip()
        if description:
            card += [_wrap_cjk(description), ""]
        url = str(repo.get("source_url") or "").strip()
        if url:
            card.append(_wrap_cjk(url, width=26))
        slides.append("\n".join(card))

    current = []
    for heading, lines in sections:
        block = [f"【{heading}】"] + [_wrap_cjk(line) for line in lines]
        for line in block:
            current.append(line)
            if len(current) >= LINES_PER_SLIDE:
                slides.append("\n".join(current))
                current = []
    if current:
        slides.append("\n".join(current))

    slides.append(END_CARD_TEXT)
    return slides


def generate_video(sections, root, repos_meta=None):
    """Render the digest into an mp4 slideshow of Chinese text cards.

    ``sections`` is an ordered list of ``(heading, [line, ...])`` (threat
    news in the daily flow); ``repos_meta`` optionally carries structured
    repo dicts that build_slides() turns into one card per repo. Slide text
    is drawn by ffmpeg's drawtext with textfile= (never shell-escaped) and a
    CJK font, then concatenated — cover/end cards at CARD_SECONDS, content
    at SLIDE_SECONDS. Returns the output filename inside ``root/media`` or
    None when ffmpeg/a CJK font is unavailable; it never raises into the
    digest send path.
    """
    import shutil
    import subprocess
    from uuid import uuid4

    if shutil.which("ffmpeg") is None:
        return None
    font = _find_font()
    if font is None:
        return None
    width, height = SLIDE_SIZE
    slides = build_slides(sections, repos_meta)
    last = len(slides) - 1

    media_dir = Path(root) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    work = media_dir / f".work-{uuid4().hex[:8]}"
    work.mkdir(parents=True)
    try:
        pngs = []
        for i, slide in enumerate(slides):
            txt, png = work / f"s{i}.txt", work / f"s{i}.png"
            txt.write_text(slide, encoding="utf-8")
            if i == 0:  # cover card: big title, centered
                style = "fontcolor=0xe9f2fd:fontsize=56:line_spacing=30:x=(w-text_w)/2:y=(h-text_h)/2"
            elif i == last:  # end card: closing line, centered
                style = "fontcolor=0xe9f2fd:fontsize=52:line_spacing=30:x=(w-text_w)/2:y=(h-text_h)/2"
            else:  # content card: reading layout, top-left block
                style = "fontcolor=0xe9f2fd:fontsize=38:line_spacing=26:x=56:y=140"
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                 f"color=c=0x050b16:s={width}x{height}:r=2", "-vf",
                 (f"drawtext=fontfile={_filter_path(font)}:"
                  f"textfile={_filter_path(txt)}:{style}"),
                 "-frames:v", "1", str(png)], check=True)
            pngs.append(png)
        listing = work / "list.txt"
        listing.write_text("".join(
            f"file '{png.name}'\n"
            f"duration {CARD_SECONDS if i in (0, last) else SLIDE_SECONDS}\n"
            for i, png in enumerate(pngs))
            + f"file '{pngs[-1].name}'\n", encoding="utf-8")
        out_name = f"digest-{_yesterday()}-{uuid4().hex[:8]}.mp4"
        out_path = media_dir / out_name
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
             "-i", str(listing), "-vf", "fps=2,format=yuv420p",
             "-c:v", "libx264", "-preset", "veryfast", "-movflags", "+faststart",
             str(out_path)], check=True)
        return out_name
    except Exception:
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def register_repos(store, repos_by_category):
    """Register fetched repos as immediately usable skill metadata; returns new-count.

    Existing entries keep their review state and just get their star count
    refreshed, so an operator-set review_status is never reset by a refresh.
    """
    registered = 0
    for items in repos_by_category.values():
        for repo in items:
            key = f"github-{repo['id'].replace('/', '-')}"
            existing = store.registry_list("skills", owner_id="admin").get(key)
            if existing:
                updated = dict(existing)
                updated["stars"] = repo["stars"]
                store.registry_register("skills", key, updated, owner_id="admin")
                continue
            store.registry_register("skills", key, {
                "name": key,
                "description": repo["description"] or repo["name"],
                "license": repo["license"],
                "source_url": repo["source_url"],
                "review_status": "approved",
                "stars": repo["stars"],
                "topics": repo["topics"],
                "origin": "github-digest",
                "category_digest": True,
            }, owner_id="admin")
            registered += 1
    return registered


def render_digest(repos_by_category, registered, news=None, video_url=None):
    date = _yesterday()
    lines = [f"📰 FACAI 情报日报 · {date}（新收录 {registered} 个可用能力）"]
    if video_url:
        lines.append(f"🎬 视频讲解（点开即看）：{video_url}")
    for category, items in repos_by_category.items():
        if not items:
            continue
        lines.append(f"\n【新项目 · {category}】")
        for repo in items:
            lines.append(f"· {repo['name']} ⭐{repo['stars']} {repo['description']}")
            lines.append(f"  {repo['source_url']}")
    if news:
        for feed, items in news.items():
            if not items:
                continue
            lines.append(f"\n【威胁通报 · {feed}】")
            for item in items:
                lines.append(f"· {item['title']}")
                lines.append(f"  {item['link']}")
    if len(lines) <= 1:
        lines.append("过去 24 小时没有满足阈值的更新，明天再见。")
    return "\n".join(lines)


def send_digest(text, root):
    """Push the digest to the operator: WeChat first, Telegram as fallback."""
    from ..channels import OutboundReply, build_providers
    from ..store import Store

    store = Store(root)
    providers = build_providers(os.environ, state_dir=Path(root) / "providers")
    attempts = []
    for channel in ("wechat", "telegram"):
        provider = providers.get(channel)
        rows = store.channel_messages(channel)
        inbound = [row for row in rows if row["direction"] == "INBOUND" and row.get("sender")]
        recipient = inbound[0]["sender"] if inbound else None
        if provider is None or not provider.configured() or not recipient:
            attempts.append(f"{channel}: unconfigured")
            continue
        try:
            result = asyncio.run(provider.send(OutboundReply(
                channel=channel, recipient=recipient, text=text)))
            status = result.get("status") if isinstance(result, dict) else "UNKNOWN"
            attempts.append(f"{channel}: {status}")
            if status in ("SENT", "QUEUED"):
                return True, f"delivered via {channel} ({'; '.join(attempts)})"
        except Exception as exc:
            attempts.append(f"{channel}: {type(exc).__name__}")
    return False, "; ".join(attempts) or "no channel available"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Daily GitHub digest")
    parser.add_argument("--dry-run", action="store_true",
                        help="collect and print, but do not register or send")
    parser.add_argument("--data-dir", default=None,
                        help="data dir (defaults to FACAI_DATA_DIR or ./data)")
    args = parser.parse_args(argv)

    root = Path(args.data_dir or os.getenv("FACAI_DATA_DIR") or (REPO_ROOT / "data"))
    repos = fetch_new_repos()
    news = fetch_threat_news()
    if args.dry_run:
        print(render_digest(repos, registered=0, news=news))
        return 0
    sys.path.insert(0, str(REPO_ROOT))
    from ..store import Store

    store = Store(root)
    # 常备能力先落册（幂等）；首轮新收录的条目随日报列出。
    curated = curated_capability_repos()
    curated_new = register_repos(store, {"能力执行·常备": curated})
    if curated_new:
        repos = {**repos, "能力执行·常备": curated}
    registered = register_repos(store, repos) + curated_new

    # 中文版：把英文描述/标题批量翻译成简体中文（失败自动保留原文）
    descriptions = [repo["description"] for items in repos.values() for repo in items
                    if repo["description"]]
    titles = [item["title"] for items in (news or {}).values() for item in items]
    translated = translate_to_chinese(descriptions + titles, root) or []
    if translated:
        i = 0
        for items in repos.values():
            for repo in items:
                if repo["description"] and i < len(translated):
                    repo["description"] = translated[i]
                    i += 1
        for items in (news or {}).values():
            for item in items:
                if i < len(translated):
                    item["title"] = translated[i]
                    i += 1

    # 视频讲解卡片（ffmpeg 可用时），链接随日报推送。仓库走逐项卡片
    # （build_slides 封顶 12 张），威胁通报仍按分节文本页渲染。
    news_sections = [(f"威胁通报 · {feed}", [i["title"] for i in items])
                     for feed, items in (news or {}).items() if items]
    repo_meta = [dict(r, category=category)
                 for category, items in repos.items() for r in items]
    # Media retention: keep the newest 14 digest videos, drop the rest.
    try:
        media_dir = Path(root) / "media"
        old_videos = sorted(media_dir.glob("digest-*.mp4"))[:-14]
        for stale in old_videos:
            stale.unlink(missing_ok=True)
    except OSError:
        pass
    video_name = generate_video(news_sections, root, repos_meta=repo_meta)
    video_url = (f"{os.getenv('FACAI_PUBLIC_BASE', 'https://whstapp.lol')}/media/{video_name}"
                 if video_name else None)

    digest = render_digest(repos, registered, news=news, video_url=video_url)
    ok, detail = send_digest(digest, root)
    print(json.dumps({"registered": registered, "sent": ok, "video": video_name,
                      "detail": detail}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
