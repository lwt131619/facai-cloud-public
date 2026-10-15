"""转换操作台高并发测压（2026-10-12 操作员指令：高并发是必然要做好测压）。

Storms a running FACAI station's conversion endpoints with bounded concurrency
and reports throughput plus latency percentiles. The default mix hits the
dry-run planner (POST /api/v1/conversion/plan) — it never creates missions and
never burns a paid model call — with an optional creation rate for
POST /api/v1/conversion/console.

Usage:
    py -3.12 -m facai.ops.loadtest_conversion \
        --base http://127.0.0.1:8000 --username admin \
        --password "$FACAI_ADMIN_PASSWORD" \
        --workers 32 --per-worker 50 --create-every 25

Exit code is non-zero when any request errored or p95 exceeds --p95-sla-ms.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import sys
import time

import httpx

PLAIN_SENTENCES = [
    "帮我写一个爬虫抓取商品价格并存成表格",
    "把官网首页改成深色风格并加上公告栏",
    "每天早上统计一次服务器错误日志并发给我摘要",
    "做一个翻译英文合同的小工具，输出逐段对照",
    "分析这份销售数据，找出下滑最狠的三个品类",
]

PLAN_WEIGHT = 9          # dry-run planner: the read-heavy hot path
CREATE_WEIGHT = 1        # full pipeline create (mission + jobs + assignment)


async def _login(client: httpx.AsyncClient, base: str, username: str,
                 password: str) -> dict:
    response = await client.post(f"{base}/api/v1/auth/login",
                                 json={"username": username, "password": password})
    response.raise_for_status()
    session = response.json()
    return {"X-CSRF-Token": session["csrf_token"]}


async def _worker(worker_id: int, client: httpx.AsyncClient, base: str,
                  headers: dict, per_worker: int, create_every: int,
                  latencies: list, errors: list, lock: asyncio.Lock) -> None:
    for i in range(per_worker):
        use_create = create_every and ((worker_id * per_worker + i) % create_every == 0)
        if use_create and random.random() < CREATE_WEIGHT / (CREATE_WEIGHT + PLAN_WEIGHT):
            payload, path, method = ({"text": random.choice(PLAIN_SENTENCES)},
                                     "/api/v1/conversion/console", "POST")
        else:
            payload, path, method = ({"text": random.choice(PLAIN_SENTENCES)},
                                     "/api/v1/conversion/plan", "POST")
        started = time.perf_counter()
        try:
            response = await client.request(method, f"{base}{path}",
                                            json=payload, headers=headers)
            if response.status_code >= 500:
                raise RuntimeError(f"HTTP {response.status_code}")
            elapsed_ms = (time.perf_counter() - started) * 1000
            async with lock:
                latencies.append(elapsed_ms)
        except Exception as exc:  # noqa: BLE001 — the storm records everything
            async with lock:
                errors.append(f"{type(exc).__name__}: {exc}"[:200])


def _percentile(sorted_values: list, fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, int(round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


async def run(args: argparse.Namespace) -> int:
    limits = httpx.Limits(max_connections=args.workers * 2,
                          max_keepalive_connections=args.workers)
    async with httpx.AsyncClient(limits=limits, timeout=30) as client:
        headers = await _login(client, args.base, args.username, args.password)
        latencies: list[float] = []
        errors: list[str] = []
        lock = asyncio.Lock()
        started = time.perf_counter()
        await asyncio.gather(*(
            _worker(i, client, args.base, headers, args.per_worker,
                    args.create_every, latencies, errors, lock)
            for i in range(args.workers)))
    elapsed = time.perf_counter() - started
    latencies.sort()
    total = len(latencies) + len(errors)
    report = {
        "base": args.base, "workers": args.workers, "per_worker": args.per_worker,
        "total_requests": total, "ok": len(latencies), "errors": len(errors),
        "elapsed_seconds": round(elapsed, 2),
        "rps": round(total / elapsed, 1) if elapsed else 0.0,
        "p50_ms": round(_percentile(latencies, 0.50), 1),
        "p95_ms": round(_percentile(latencies, 0.95), 1),
        "p99_ms": round(_percentile(latencies, 0.99), 1),
    }
    for key, value in report.items():
        print(f"{key:>18}: {value}")
    if errors[:5]:
        print("sample errors:")
        for line in errors[:5]:
            print(f"  - {line}")
    failed = bool(errors) or report["p95_ms"] > args.p95_sla_ms
    print("VERDICT:", "FAIL" if failed else
          f"PASS（p95 {report['p95_ms']}ms ≤ SLA {args.p95_sla_ms}ms，零错误）")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=None)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--per-worker", type=int, default=50)
    parser.add_argument("--create-every", type=int, default=0,
                        help="每 N 个请求穿插一次完整建任务（0=纯 dry-run）")
    parser.add_argument("--p95-sla-ms", type=int, default=1500)
    args = parser.parse_args(argv)
    if not args.password:
        import os
        args.password = os.getenv("FACAI_ADMIN_PASSWORD", "")
    if not args.password:
        parser.error("--password 或 FACAI_ADMIN_PASSWORD 必须提供")
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
