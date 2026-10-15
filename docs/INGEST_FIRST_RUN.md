# INGEST 首次实网收录报告 — openai/swarm（2026-10-05）

[9] Ingestion Automation（`facai/ops/ingest.py`）对真实开源 Agent 仓库的首次实网收录记录。
目的：验证管线实战可用，并为 Verifier 启发式改进提供实测反馈。
本报告由收录波次工程师撰写；所有数字均来自当日实跑。

## 1. 结论摘要

- **管线可用**：一条命令完成 clone→初扫→双 manifest→Verifier 判定→报告，全程无人工干预，exit=0。
- **verdict 如实**：`T2_PARTIAL`（6 项检查 PASS、4 项 PENDING），无接线声明即降级，未虚报。
- **失败安全**：首次运行遇瞬时网络故障 exit=1，**零残留产物**（fail-closed 验证通过）。
- **最关键发现（给 Verifier 的负面反馈）**：200 面配额被 `examples/` 里的抓取文章 JSON 吃光，
  **真实源码 `swarm/core.py` 一个面都没扫到**——控制实验证实：单独扫 `swarm/` 源码包得 0 面。
  verdict 结论碰巧正确（无接线声明必然 T2），但"未纳管面"清单是文档噪音，不是真实裁决面。

## 2. 选型

`https://github.com/openai/swarm.git`：MIT（产物 graph `license: MIT`，管线自探正确）、
288 个文件（62 个 .py，远小于 2000 上限）、OpenAI 官方实验性多 Agent 编排框架。
候选排除：AutoGPT 太大（任务书已排除）。网络回退方案（本地 vendor/goose 二次实测）未触发——
首次失败为瞬时抖动，重试后实网 clone 成功。

## 3. 命令与耗时

实跑命令（仓库根 `facai-cloud`，Python 3.12.10）：

```
C:/Users/user/AppData/Local/Programs/Python/Python312/python.exe -m facai.ops.ingest \
  https://github.com/openai/swarm.git --name swarm \
  --vendor-dir "C:/Users/user/.zcode/workspace/default/facai-handoff/vendor"
```

| 运行 | 结果 | 耗时 |
|---|---|---|
| 第 1 次 | **失败** exit=1：`git clone` 连接 github.com:443 超时（21s 后放弃），错误信息完整透传 stderr | 21s |
| 第 2 次 | 成功 exit=0：clone→扫描→落盘→判定 | **4s** |

第 1 次失败后验证 fail-closed：`vendor/authority-manifests/` 与 `vendor/capability-graph/` 均无
swarm 残留（grep 空），符合 `ingest.py:126-128` 失败即抛、不留半成品的设计。

## 4. 产物清单（全部为新建，未触碰既有 vendor 内容）

| 产物 | 路径 |
|---|---|
| 浅克隆 | `vendor/swarm/`（288 文件） |
| Authority Manifest | `vendor/authority-manifests/swarm.json`（200 面） |
| Capability Graph 骨架 | `vendor/capability-graph/swarm.json`（25 条目） |

未登记：`authority-manifests/INDEX.json` 的 9 源不含 swarm；
`docs/CAPABILITY_REGISTRY.json` 不含 "swarm"（均经 json 解析确认，见 §8）。

## 5. 面计数与 Verdict

**扫描**：200 面（达上限截断，`truncated=true`），扫描文本文件 94 个。
分类分布：`REMOTE_PROVIDER=46`、`UNKNOWN=154`、`LOCAL_BROKERABLE=0`。
200 面全部 `kind=NETWORK`；0 条 CREDENTIAL、0 条 SHELL_EXEC。

**产物质量（程序化校验，全过）**：

- 两份 JSON `json.load` 通过；manifest 六键与九源基线（goose.json）完全一致
  （target/origin/managementClass/summary/surfaces/reRooting）；`reRooting=[]` 如实留空待人工。
- 200 面 `where` 字段 100% 带 `file:line` 后缀，且 100% 能在克隆里找到对应文件（无伪造路径）。
- license 探测 MIT（`detect_license` 命中根目录 LICENSE）。
- 能力骨架：1 条能力候选 `model.invoke`（verdict=new，level=L0_REFERENCE，证据 README.md:131）
  + 24 条外部依赖主机，与人工判断方向一致（见 §6）。
- `managementClass` 初判 `T2 PARTIALLY_MANAGED`，与 Verifier 结论一致。

**Verifier 判定**（`verify_source`，收录时与事后只读重算两次一致）：
`T2_PARTIAL`，未纳管面 92，降级理由：`关键项 PENDING（未纳管）：secret_access,
remote_dependencies, provider_result, evidence_linkage`。

| 检查 | 结果 | 依据 |
|---|---|---|
| owner_root / self_grant / action_ir / grant_revocation | PASS×4 | 实跑 authority 核心与门（伪造 grant/吊销/探针均按预期拒绝） |
| direct_exec_inventory | PASS | 200 面全部带枚举内 classification |
| brokerable_paths | PASS | 无 LOCAL_BROKERABLE 面，无 broker 路径需覆盖（**平凡通过，见 §7 漏项**） |
| secret_access / remote_dependencies / provider_result / evidence_linkage | PENDING×4 | 新收录源无任何接线声明 → 如实降级 |

## 6. 真实远程依赖 top5（人工判断基线）

按"运行时是否真的发请求、裁决权是否真在远端"排序：

1. **api.openai.com — Chat Completions 推理**（唯一运行时远程依赖）
   `swarm/core.py:29` 构造 `OpenAI()` 客户端；`swarm/core.py:69`
   `self.client.chat.completions.create(**create_params)`。模型可用性/补全/上下文裁决全在远端，
   是本仓库唯一真正的 remote decision point。
2. **OPENAI_API_KEY 凭据流**（经依赖传递，非 swarm 自身代码）
   swarm 源码零处 `os.getenv/os.environ`；API key 由 openai SDK 在 `OpenAI()` 构造时内部读取。
   这是本仓库最真实的凭据面，但扫描器不可见（见 §7）。
3. **platform.openai.com** — key 管理入口，仅出现于 README/文档链接（README.md:131 等），非运行时。
4. **beta/help/chat.openai.com** — examples 抓取的客服文章 JSON 里的链接（占 46 个
   REMOTE_PROVIDER 面的大头），纯文档噪音，非运行时。
5. **readthedocs / huggingface / weaviate / pinecone 等** — 均为示例文档链接；swarm 运行时不连接
   其中任何一个。

人工结论：swarm 的真实裁决面 = **1 个远程推理端点 + 1 条传递性凭据流**。其余全部是文档。

## 7. 与人工判断的偏差点评（Verifier 启发式改进反馈）

### 对的地方

- **诚实性链条完整**：clone 失败零残留；verdict 无虚报；reRooting 留空；graph 逐条标
  "初扫推导待人工"；绝不自动登记（stdout 明示，且代码路径确实不写 INDEX/Registry）。
- **证据可回溯**：200 面 where 字段 100% 真实存在，抽查行内容与文件一致。
- **能力候选方向正确**：`model.invoke` 恰是人工判断的唯一真实能力，level=L0 如实。
- **schema 与九源基线零漂移**，收录物可直接进入人工复核流程。

### 漏的地方（按严重度）

1. **截断偏置——真源码零覆盖（最严重）**。`scan_source`（`facai/ops/ingest.py:211-214`）按
   os.walk 字母序先到先得（`ingest.py:192-195`），`examples/customer_service_streaming/data/`
   里 13+ 个抓取文章 JSON 每文件含数十个 URL，在第 45/94 个文件处吃光 200 面配额，
   `swarm/core.py` 等真实源码从未被扫描。控制实验：对 `swarm/` 源码包单独跑
   `scan_source` → **6 文件 0 面**（连 core.py:69 的远程调用正则都漏了，因为 URL 是 SDK
   抽象的，源码里不出现字符串端点）。**改进建议**：源码扩展名（.py/.ts/.rs/.go…）不限配额
   或优先扫描；数据/文档文件的 URL 单列 `DOC_REFERENCE` kind 并配独立低配额。
2. **REMOTE_PROVIDER 误报率 46/46=100%**。`_REMOTE_HINTS_RE`（`ingest.py:88-92`）对
   markdown/文章文本里的链接与代码里的端点常量一视同仁。本次 46 个"remote decision point"
   没有一个是运行时调用。Verifier 的 `remote_dependencies` PENDING 证据因此全无信号。
3. **传递性凭据盲区（假阴性）**。env 正则（`ingest.py:75-81`）只扫仓库自身文本；
   `OpenAI()`（core.py:29）经 openai SDK 内部读取 OPENAI_API_KEY 这条真实凭据流
   完全不可见，导致 `LOCAL_BROKERABLE=0`、`brokerable_paths` 平凡 PASS。**改进建议**：
   对已知消费凭据的 SDK 构造点（openai/anthropic/google 等）补 `INFERRED_CREDENTIAL` 面，
   标注"凭据经依赖传递，应走 Secret Broker"。
4. **"未纳管面=92"是噪音而非信号**。92 个未纳管面全是文档 URL 面；真正的未纳管裁决面
   （远程推理调用）不在清单里。verdict 碰巧正确只因"无接线声明必降级"，但给人工复核的
   工作清单会误导优先级。

## 8. 验证记录（本波次实际执行的检查）

```
python json.load ×2         → vendor/authority-manifests/swarm.json、capability-graph/swarm.json 均解析通过
schema 六键比对 goose.json  → 完全一致
where 字段 file:line 校验    → 200/200 带行号；200/200 文件存在于克隆
INDEX.json 解析             → sources 为 9 源字典，不含 "swarm"
CAPABILITY_REGISTRY.json    → 全文不含 "swarm"
verify_source 只读重算      → 与收录时 stdout 一致（T2_PARTIAL，6 PASS/4 PENDING）
```

说明：INDEX.json 当日 12:19 被刷新一次（其 `generated_at`/各源 `checked_at` 同秒），经代码定位
写入者是 `verifier.py:465 verify_index`（只刷新已登记九源），**不在 ingest 调用链上**
（`ingest.py:468-471` 仅写两份 manifest），判定为并行波次的 verifier 运行，与本收录无关。

## 9. 纪律声明

- 未登记 Registry：收录产物停在 vendor 草案层，登记待人工过 `register_with_gate` 门。
- 本任务无 pytest 目标：波次仅新增本文档与 vendor 新文件，未改任何代码
  （`git status` 仅 docs/INGEST_FIRST_RUN.md 新增）；未重跑 943 全量（并行波次正在改树，
  结果无法归因，如实说明）。
- vendor 既有内容未触碰：goose/opencode 等既有清单、图与检出版原样；仅新增 swarm 三件
  （克隆目录 + 两份 manifest）。

## 10. 修复后复验（同日，扫描器三修落地后复跑）

本节由修复波次补录。§7 三条反馈已全部落地为代码：`facai/ops/ingest.py`（源码优先扫描、
DOC_REFERENCE 分类、INFERRED_CREDENTIAL 面），并同步扩枚举三处——`facai/authority.py`
（分类常量 +DOC_REFERENCE）、`facai/ops/verifier.py`（KNOWN_CLASSES）、
`facai/ops/authority_manifest.py`（_CLASS_KEYS：DOC_REFERENCE→已清点无需改根）。
任务书原指名「§8 修复后复验」，因 §8/§9 已被验证记录与纪律声明占用，顺延为 §10。

复跑命令（--offline 复用 §4 浅克隆，旧 draft 因「初扫」前缀被覆盖守卫放行原地更新）：

```
python -m facai.ops.ingest C:/Users/user/.zcode/workspace/default/facai-handoff/vendor/swarm \
  --name swarm --offline --vendor-dir C:/Users/user/.zcode/workspace/default/facai-handoff/vendor
```

exit=0。如实记录两处元数据变化：`origin` 由 git URL 变为「本地路径 …/vendor/swarm」
（--offline 语义使然）；文档档 200 面打满截断（`doc_truncated=true`）——文档 URL 面
如实不扫全，它们不是裁决面。

### 10.1 前后对比（§5 基线 → 本次复跑）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 面数 | 200（截断） | 208 = 源码档 8 + 文档档 198 + 其余文件 2（源码档未截断） |
| 扫描文件 | 94（出面 45 个，**0 个 .py**） | 132 = 源码 62（全部 .py 全扫）+ 其余 70 |
| REMOTE_PROVIDER | 46（100% 文档误报） | **0** |
| UNKNOWN | 154 | 2（.gitignore / .pre-commit-config.yaml） |
| LOCAL_BROKERABLE | 0 | **8**（全部 INFERRED_CREDENTIAL，sdk=openai） |
| DOC_REFERENCE | —（噪音混在 46+154 里） | **198**（独立分类独立计数，remoteAuthorityDependency 全 false） |
| core.py 覆盖 | 0 面（§7-1 控制实验） | **`swarm/core.py:29  client = OpenAI()`** 在案 |
| 未纳管面 | 92（全是文档 URL 噪音） | 16（8 brokerable + 8 cred，全部真实裁决相关） |
| verdict | T2_PARTIAL（6 PASS/4 PENDING） | T2_PARTIAL（7 PASS/3 PENDING，如实不虚升） |

### 10.2 三条反馈逐项复验

1. **源码优先 ✅**：62 个 .py 全部入扫，core.py 的面不再被 examples JSON 挤掉；
   源码配额独立 200（本次仅用 8）。`direct_exec_inventory` 证据由「200 面全 NETWORK」
   变为「208 个面全部带枚举内的 classification」。
2. **DOC_REFERENCE ✅**：198 个文档/示例 URL 单列新分类，不再进 REMOTE_PROVIDER 误报池；
   46/46=100% 误报率归零（REMOTE_PROVIDER=0）。
3. **INFERRED_CREDENTIAL ✅**：8 个 openai SDK 构造点全部识别（swarm/core.py:29、
   examples/customer_service_streaming/prep_data.py:5 等 7 处 examples），全部
   LOCAL_BROKERABLE + `inferred via openai`；§6-2 指认的「密钥经 openai SDK 内部读取」
   传递凭据流从不可见变为 8 面在案。

### 10.3 如实说明（不虚报）

- **`remote_dependencies`/`provider_result` 由 PENDING 转 PASS 的真实含义**：swarm 自身
  文本零硬编码端点（端点被 SDK 抽象），不是「无远程依赖」——真实远程性已由
  INFERRED_CREDENTIAL 承载，体现在 brokerable_paths/secret_access 两项 PENDING。
  人工复核仍应按 §6-1 处理 api.openai.com 的改根。
- **`model.invoke` 候选消失**：其唯一触发证据原是 README.md:131 的文档 URL（恰是 §7-2
  指认的误报源）；修复后候选按面种推导不再由文档触发。真实调用点以 INFERRED_CREDENTIAL
  （core.py:29）在案，模型调用候选待人工按先验清单格式补提案。
- **`secret.request` 候选证据**指向 examples/…/prep_data.py:5（字母序首个命中面）而非
  core.py:29——首轮命中语义与修复前一致（原 model.invoke 证据同样是文档首命中）。
- **外部依赖 24→2**（raw.githubusercontent.com / github.com，出自 .gitignore 与
  .pre-commit-config.yaml）：其余主机全在文档档里，不再是「依赖候选」。
- **INDEX.json（9 源）与 CAPABILITY_REGISTRY.json 仍不含 swarm**：复验只原地更新
  draft，绝不自动登记（程序化复核通过）。

### 10.4 验证记录（本节实际执行的检查）

```
复跑 ingest CLI           → exit=0，stdout 含 [docref] 198 / [inferred] 8 独立两节
程序化校验 11 项          → 全过：json.load×2；manifest 六键==goose；208 面 where
                            100% 带 file:line 且 100% 文件存在；DOC_REFERENCE 198
                            remoteAuthorityDependency 全 false；INFERRED 8 全
                            LOCAL_BROKERABLE 且 sdk=openai；by_class/by_kind 复核；
                            INDEX 9 源不含 swarm；Registry 全文不含 swarm；graph
                            summary/caps 复核；secret.request 证据在案
verify_source 只读重算    → 与收录时 stdout 一致（T2_PARTIAL，7 PASS/3 PENDING，
                            未纳管面 16）
pytest tests/test_ingest.py -q → 23 passed（15 旧断言无一改动 + 8 新增）
全量 pytest -q            → 957 passed（947 基线 + 本波 8 + 并行波次 console-layout
                            2；零失败）
```
