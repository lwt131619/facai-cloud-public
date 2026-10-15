# INGEST 第二次实网收录报告 — paul-gauthier/aider（2026-10-06）

[9] Ingestion Automation（`facai/ops/ingest.py`）对第二个真实开源仓库的实网收录记录。
目的：在**中等体量 Python 仓库**（与首个实网收录对象 openai/swarm 的小体量形成对照）上
验证三修后的扫描器（源码优先 + DOC_REFERENCE + INFERRED_CREDENTIAL，
见 `docs/INGEST_FIRST_RUN.md` §7→§10）。本报告由实网收录波次撰写；所有数字均来自当日实跑。

## 1. 结论摘要

- **管线可用**：一条命令 clone→初扫→双 manifest→Verifier 判定→报告，实网 clone **一次成功**
  （无需重试、无需 ghproxy 镜像），exit=0。
- **三修扫描器在大仓库上成立**：aider 克隆 691 文件、180 个源码扩展名文件**全部入扫**
  （147 个 .py 零遗漏），源码档 195 面、未截断；180 个文档 URL 全部单列 DOC_REFERENCE，
  REMOTE_PROVIDER 误报率 **0/44**（swarm 修复前是 46/46=100% 文档误报）。
- **INFERRED_CREDENTIAL=0，经查证为真阴性，非漏报**：aider 自身源码**零处**直接构造
  `OpenAI(/Anthropic(` 客户端，LLM 调用全部经 litellm 抽象（`aider/llm.py:37` 惰性导入）；
  真实 OpenAI 客户端构造发生在 litellm **依赖内部**，对本扫描器天然不可见——这是
  「二阶传递盲区」，已记为扫描器下一轮改进反馈（§6）。
- **verdict 如实**：`T2_PARTIAL`（5 PASS / 5 PENDING），未纳管面 150——与 swarm 修复前的
  「92 面全文档噪音」不同，这 150 面全部出自源码/配置文件，是真实复核工作清单。
- **防越界**：绝不自动登记——INDEX.json 9 源与 CAPABILITY_REGISTRY.json 均不含 aider
  （程序化复核通过）。

## 2. 命令与运行

实跑命令（仓库根 `facai-cloud`，Python 3.12.10）：

```
C:/Users/user/AppData/Local/Programs/Python/Python312/python.exe -m facai.ops.ingest \
  https://github.com/paul-gauthier/aider --name aider \
  --vendor-dir "C:/Users/user/.zcode/workspace/default/facai-handoff/vendor"
```

| 运行 | 结果 |
|---|---|
| 第 1 次 | **成功** exit=0（实网 clone 一次通过；重试与镜像方案均未触发） |

克隆出处：`origin = https://github.com/paul-gauthier/aider`，HEAD
`5dc9490bb35f9729ef2c95d00a19ccd30c26339c`（2026-05-22）。任务书预估「约 1k 文件」，
实测（跳过 .git/node_modules 等目录后）**691 文件**，其中源码扩展名文件 180——
比预估小，如实记录。

## 3. 产物清单（全部为新建，未触碰既有 vendor 内容）

| 产物 | 路径 |
|---|---|
| 浅克隆 | `vendor/aider/`（691 文件） |
| Authority Manifest | `vendor/authority-manifests/aider.json`（395 面） |
| Capability Graph 骨架 | `vendor/capability-graph/aider.json`（40 条目 = 候选 3 + 外部依赖 37） |

## 4. 面计数与 Verdict

**扫描**：395 面 = 源码档 195 + 文档/示例 DOC_ 降权 180 + 其余文件 20；
扫描文本文件 268（源码 180、其余 88）；`truncated=False`（源码档未截断）/
`doc_truncated=True`（其余档达 195+200=395 封顶，319 个其余文件只扫到 88 个——
文档面如实不扫全，它们不是裁决面）。

分类分布（`by_classification`）：

| 分类 | 数 | 说明 |
|---|---|---|
| REMOTE_PROVIDER | 44 | **100% 出自源码文件**：aider/urls.py 15（URL 注册表，17 行全为端点常量）、scripts/homepage.py 5、aider/analytics.py 2（us.i.posthog.com）、aider/onboarding.py 2 + aider/openrouter.py 1（openrouter.ai/api/v1/auth/key——真实运行时密钥校验端点）、aider/models.py 2 等 |
| LOCAL_BROKERABLE | 41 | CREDENTIAL 21 + SHELL_EXEC 20（INFERRED_CREDENTIAL=0，见 §5） |
| UNKNOWN | 130 | NETWORK 87 + CONFIG 43，全部出自源码/配置文件 |
| DOC_REFERENCE | 180 | 独立分类独立计数；逐面核验 `remoteAuthorityDependency` 全 false、kind 全 DOC_NETWORK |

**产物质量（程序化校验，全过）**：

- 两份 JSON `json.load` 通过；manifest 六键与九源基线（goose.json）完全一致；
  `reRooting=[]` 如实留空待人工；graph `license: Apache-2.0` 为管线自探（`detect_license`
  命中根目录 LICENSE.txt），与上游声明一致。
- 395 面 `where` 字段 **100% 带 `file:line` 后缀、100% 能在克隆里找到对应文件**（零伪造）。
- **源码覆盖率**：180 个源码扩展名文件全部被扫描（源码档不截断），其中 **147 个 .py 零遗漏**
  （粗对账 181 差异的 1 个是 `aider/website/assets/asciinema/asciinema-player.min.js`，
  被 `SKIP_SUFFIXES`（`ingest.py:82`）正确排除）。**.py 面占比 = 186/395 = 47.1%**；
  出面的 .py 文件 56/147 = 38%（其余有面文件为 .sh/.json/.scm/.yaml 等）。
- Verifier 判定（收录时与事后只读重算两次一致）：`T2_PARTIAL`，
  PASS：owner_root / self_grant / action_ir / grant_revocation / direct_exec_inventory；
  PENDING：brokerable_paths / secret_access / remote_dependencies / provider_result /
  evidence_linkage；未纳管面 150。
  注意 **brokerable_paths 由 swarm 的平凡 PASS 变为真实 PENDING**——41 个
  LOCAL_BROKERABLE 面是实实在在待收口的 broker 工作项，这是扫描器进化带来的信号质量提升。

**能力骨架**：三条候选模板**全部触发**（swarm 修复后为 0 条）——

| 候选 | 证据（首命中） | 语义 |
|---|---|---|
| shell.exec | aider/commands.py:974 `result = subprocess.run(` | 真实子进程执行面 |
| secret.request | aider/help.py:138 `os.environ["TOKENIZERS_PARALLELISM"]="true"` | **子串误报**（TOKENIZERS 含 "TOKEN"），非凭据；真实凭据面见 §5 的 21 面 |
| model.invoke | aider/analytics.py:57 `posthog_host="https://us.i.posthog.com"` | 推送/遥测端点，非模型调用；真实模型调用走 litellm 不可见（§5） |

首轮命中语义与 swarm 先例一致（候选证据=分类序首面），逐条 verdict 待人工复核，
如实记录两处证据不精确。

## 5. INFERRED_CREDENTIAL 专项核验（任务书点名项）

**结论：INFERRED_CREDENTIAL = 0 面。经源码级查证，这是真阴性——aider 不直接构造
LLM SDK 客户端，不是扫描器漏报；但暴露了「二阶传递盲区」。**

查证过程（本波次实跑的 grep 与行读）：

```
grep -rn --include="*.py" -E "\b(Async)?(OpenAI|Anthropic)\s*\(" vendor/aider/
  → 0 匹配（全仓库零直接构造点）
grep -rn --include="*.py" -E "requests\.Session\s*\(|httpx\.(Async)?Client\s*\(" vendor/aider/
  → 3 处 httpx，均无 auth 参：
    aider/main.py:524-525  httpx.Client(verify=False) / AsyncClient(verify=False)
    aider/scrape.py:199    httpx.Client(headers=…, verify=…)（网页抓取，非凭据）
  → INFERRED httpx/requests 规则不触发，符合规则设计（仅 auth/headers 传凭据态才报）
```

aider 的真实 LLM 客户端构造方式：`aider/llm.py:30-37` 以惰性导入
`importlib.import_module("litellm")` 把全部模型调用委托给 litellm 依赖
（aider 官方文档亦按 litellm 约定要求 provider 前缀，
https://aider.chat/docs/llms.html 、https://docs.litellm.ai ）。
真正的 `OpenAI()` 客户端构造与 `OPENAI_API_KEY` 读取发生在 **litellm 包内部**
（site-packages），仓库自身文本扫描天然不可见——INFERRED_CREDENTIAL 的
「只扫仓库自身文本」口径对此无解，属依赖侧盲区而非规则失灵。

**缓解证据（凭据流并未全盲）**：aider 自身代码的密钥 env 流被既有 env 规则抓住 21 面
CREDENTIAL，LLM 供应商密钥尽数在案：

- OPENAI_API_KEY ×4：aider/main.py:616（`os.environ["OPENAI_API_KEY"]=args.…` 写入）、
  aider/voice.py:184（`os.getenv`）、scripts/recording_audio.py:22、
  tests/basic/test_ssl_verification.py:14
- ANTHROPIC_API_KEY ×2：aider/main.py:613、tests/basic/test_main.py:947
- OPENROUTER_API_KEY ×3：aider/main.py:786、aider/onboarding.py:53、test_main.py:1093
- GITHUB_COPILOT_TOKEN：aider/models.py:954；DEEPSEEK/GEMINI/GITHUB_TOKEN/PEPY_API_KEY 等

即：aider 的凭据图景 = **21 面自身 env 流（在案、LOCAL_BROKERABLE）+ 1 条 litellm 依赖
内部凭据流（盲区，靠 §4 的 openrouter/auth 端点与 analytics 面间接侧写）**。

## 6. 与 swarm 实跑的对比（扫描器进化是否生效）

基线数字取自 `docs/INGEST_FIRST_RUN.md` §5（修复前实跑）、§10.1（三修后复跑）：

| 指标 | swarm 修复前 | swarm 修复后复跑 | **aider 本次** | 进化判定 |
|---|---|---|---|---|
| 仓库形态 | 288 文件/62 .py | 同左 | **691 文件/147 .py** | 中等体量新样本 |
| 面数 | 200（截断） | 208 = 源码 8+文档 198+其余 2 | **395 = 源码 195+文档 180+其余 20**（源码档未截断） | — |
| 源码覆盖 | **0 个 .py 出面** | 62 个 .py 全扫 | **180 源码文件全扫，147 .py 零遗漏；.py 面占比 47.1%** | ✅ 源码优先在大仓库成立 |
| REMOTE_PROVIDER | 46（**100% 文档误报**） | 0 | **44（100% 出自源码文件，0 文档）** | ✅ 误报池彻底隔离 |
| DOC_REFERENCE | —（噪音混在 46+154） | 198 独立计数 | **180 独立计数，remoteAuthorityDependency 全 false** | ✅ |
| INFERRED_CREDENTIAL | 不可见 | 8 面（sdk=openai，真构造点） | **0 面（真阴性：litellm 抽象）** | ⚠️ 规则有效但有二阶盲区 |
| LOCAL_BROKERABLE | 0 | 8 | **41**（cred 21 + shell 20） | ✅ 真实工作面浮出 |
| brokerable_paths | 平凡 PASS | PASS（8 面） | **PENDING（41 面待收口）** | ✅ 从平凡通过变真实信号 |
| verdict | T2_PARTIAL（6P/4PEND，未纳管 92=全噪音） | T2_PARTIAL（7P/3PEND，未纳管 16） | **T2_PARTIAL（5P/5PEND，未纳管 150=全真实）** | ✅ 如实且不虚升 |
| 能力候选 | model.invoke（证据=README 文档 URL 误报） | 0 条 | **3/3 全触发**（shell.exec/secret.request/model.invoke） | ✅ 源码触发 |
| 外部依赖主机 | 24（多为文档主机） | 2 | 37（含 openrouter/posthog/github 等，多数亦为源码常量） | — |

**总体判定：三条修复在 aider 上全部按设计生效**——源码优先把「examples 吃配额」问题
在 691 文件体量上结构性排除；DOC_REFERENCE 把 180 个文档链接从裁决面剥离，使 44 个
REMOTE_PROVIDER 首次全部对应真实源码常量（urls.py/openrouter/analytics）。
INFERRED_CREDENTIAL 在 swarm（直接构造 openai）上验证有效，在 aider（litellm 抽象）上
暴露**二阶传递盲区**：真实客户端构造在依赖内部。改进反馈两条，供后续扫描器波次：

1. INFERRED 规则表可补 `litellm` 构造/导入点（`import_module("litellm")`、
   `litellm.completion(` 调用面），标注「凭据经 litellm 传递」；
2. 或引入依赖侧扫描（site-packages 内 litellm 的客户端构造点），但须显式标注扫描范围
   越出仓库自身文本。

## 7. 如实说明（不虚报）

- `remote_dependencies` PENDING ≠ 无远程依赖：44 面源码端点在案（openrouter 密钥校验、
  posthog 遥测、aider.chat 自检更新），改根提案仍待人工。
- `secret.request` 候选证据指向 help.py:138（TOKENIZERS_PARALLELISM 子串误报）；
  `_CRED_HINTS_RE`（`ingest.py:104`）按子串命中 TOKEN，真实凭据以 §5 的 21 面为准。
- `os.environ` 成员检查是盲区：aider/commands.py:1256
  `if "OPENAI_API_KEY" not in os.environ:` 不读写、不入账（如实记录，1 处）。
- 外部依赖列表含解析噪音（`vendor.https_`、`vendor.http_`、`vendor.user`）——URL 正则
  吃到模板串/裸串；不影响 verdict，逐条人工复核时应剔除。
- 其余档 319 文件只扫 88（395 面封顶截断）：文档面如实不扫全，非裁决面；
  源码档 180 文件无截断，覆盖完整。
- 双语产物 summary 自述「启发式初扫、非人工清单」，未虚报人工复核状态。

## 8. 验证记录（本波次实际执行的检查）

```
实跑 ingest CLI（任务书原命令）        → exit=0，实网 clone 一次成功
json.load ×2                          → authority-manifests/aider.json、capability-graph/aider.json 均解析通过
manifest 六键比对 goose.json          → 完全一致；reRooting=[]
where 字段核验                        → 395/395 带 file:line；395/395 文件存在于克隆
分类核验                              → DOC_REFERENCE=180 全 DOC_NETWORK、remoteAuthorityDependency 全 false；
                                        LOCAL_BROKERABLE 41 = CREDENTIAL 21 + SHELL_EXEC 20；
                                        REMOTE_PROVIDER 44 全部出自源码文件
源码覆盖对账                          → 147 .py 全扫（min.js 由 SKIP_SUFFIXES 正确排除）；
                                        .py 面 186/395=47.1%
INFERRED_CREDENTIAL 专项              → manifest 中 0 面；grep 证实全仓库零 OpenAI(/Anthropic( 构造、
                                        httpx 3 处均无 auth；litellm 抽象定位 aider/llm.py:37
verify_source 只读重算                → 与收录时 stdout 一致（T2_PARTIAL，5 PASS/5 PENDING，未纳管 150）
防越界                                → INDEX.json 9 源不含 aider；CAPABILITY_REGISTRY.json 全文不含 aider
定向测试 tests/test_ingest.py -q      → 23 passed（定向健全性检查，非全量 972 基线）
```

## 9. 纪律声明

- 未登记 Registry：收录产物停在 vendor 草案层，登记待人工过
  `facai/ops/verifier.py:register_with_gate` 门；INDEX/Registry 经程序化复核均未含 aider。
- 未改任何源码：本波次仅新增本文档与 vendor 三件新产物（克隆目录 + 两份 manifest）；
  无 git commit。
- vendor 既有内容未触碰：swarm/goose/opencode 等既有清单、图与检出版原样。
