# -*- coding: utf-8 -*-
"""一次性运行器：把会话工具预取的正文走 register_case 治理路径入库，
然后执行永冻授权复核（撤回一切开放端点 + invariant 报告）。
用法: py -3.12 -m facai.ops.prefetched_ingest <prefetched.json>
JSON 形如 [{"url":..., "title":..., "text":...}, ...]
"""
import json
import sys
from pathlib import Path

from ..boundary import BoundaryGovernor
from ..ops.case_ingest import register_case
from ..registries import Registries
from ..store import Store


def main(argv):
    data_dir = Path(argv[0])
    items = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    store = Store(data_dir)
    registries = Registries(store)
    governor = BoundaryGovernor(data_dir)
    rows = [register_case(governor, registries, it["url"], it["title"], it["text"])
            for it in items]
    for r in rows:
        print(f"[{r['decision']:>10}] {r['url']}  {r.get('reason', r.get('title', ''))[:60]}")

    # 永冻授权复核：撤回任何开放端点，然后证明"远程 < 本地所有者"。
    open_entries = [e for e in governor.ledger.list() if e["allowed"]]
    for entry in open_entries:
        governor.revoke_remote(entry["id"])
    report = governor.below_local_report()
    print(f"freeze-check: ledger={report['total']} open={report['open']} "
          f"holds={report['holds']} revoked_now={len(open_entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
