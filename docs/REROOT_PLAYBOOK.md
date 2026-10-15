# REROOT_PLAYBOOK — FACAI 改根执行手册

- 生成：2026-10-05。来源：五组改根规划 JSON（goose-改根 / opencode-改根 / hermes-改根 / facai-桌面改根 / facai-本体收口），共 **33 条任务**。
- 句柄锚定：`docs/CAPABILITY_GRAPH.md` 去远程化汇总节（`CAPABILITY_GRAPH.md:103-172`）+ 各源详情行；`vendor/authority-manifests/{goose,opencode,hermes-local,facai-remote-integrations}.json`。
- 本会话复核记录（句柄核对，非能力验证）：
  - 通读 `CAPABILITY_GRAPH.md:95-214` 并抽验被引行 ：85（autoclaw.im.delivery-outbox）、:233（opencode.provider_baseurl_config 详情）、:239（opencode.session_share 详情）、:258（tools.managed_gateway 详情）、:285（facai-proxy.pivot-9900 详情）——全部与五组 JSON 引用一致；
  - 用 Python 枚举四份 manifest：goose **26** surfaces、opencode **17**、hermes-local **20**、facai-remote-integrations **21**，五组 JSON 引用的锚点全部命中；
  - **索引修正**：facai-本体组 JSON 对 facai-remote-integrations.json 的 surface 编号有 6 处漂移，本手册一律用本次枚举核实后的编号——n8n 出站=surfaces[6]（组内写作[7]）、n8n 回调=surfaces[7]（写作[8]）、notify 渠道回传=surfaces[8]（写作[9]）、secretbox 端点守卫★=surfaces[12]（写作[13]）、registry 模型调用=surfaces[13]（写作[14]）、relay outbox=surfaces[17]（写作[16]）；wechat 接口版本强制=surfaces[3]、渠道投递=surfaces[4][5]、TronGrid=surfaces[0][1] 不变。
- 纪律：源码树只读；本文件是本轮唯一产出，不改任何代码/配置。

---

## # 执行序

风险口径：**低**=可逆、无重建、不改运行行为面；**中**=改运行行为或需回归测试/涉密迁移；**高**=需重建+回归、自建签名链或放宽安全守卫；**决策**=OWNER 裁决项。

```text
Phase 0 前置资产（不动源码，可并行）──────────────────────────────
A0.1  ollama pull GLM：`ollama pull glm-4.7-flash`（30B 本地档，推荐）或 `ollama pull glm4:9b`（轻量档）。
      注意 glm-5.3-flash 在 Ollama 库属 cloud 档，本地 pull 不可得，不作为本地档。
      依赖门：G1/G2/G7；F4 若用本机 Ollama 作上游。            风险：低
A0.2  起本地 SearXNG 127.0.0.1:8888。依赖门：H3。              风险：低
A0.3  n8n 自托管：cd /opt/facai-cloud/deploy/n8n && umask 077 &&
      printf 'N8N_ENCRYPTION_KEY=%s\n' "$(openssl rand -hex 32)" > .env && docker compose up -d
      （compose.yaml 已绑 127.0.0.1:5678，镜像 pin n8n:2.41.5）。依赖门：F2。风险：低
A0.4  OSV 本地镜像决策：未就绪则 G4/G5 的 OSV_ENDPOINT 暂不设
      （查询失败→扩展安装被拒，fail-closed 可接受但影响装扩展体验）。风险：决策
A0.5  FACAI 内部 git 镜像 URL 定案（H2 的 <FACAI_INTERNAL_MIRROR>）。风险：决策
A0.6  opencode 密钥迁移准备：auth.json 现存 13 条明文 key → Facai secretbox；
      先备份 auth.json.facai-migrated。依赖门：O5。             风险：中（涉密）
A0.7  whisper GGUF 四文件入自有分发并预置 <root>/data/models/；
      文件名必须与 URL 尾段一致：model-tiny-q80.gguf / whisper-base-q8_0.gguf /
      whisper-small-q8_0.gguf / whisper-medium-q8_0.gguf。依赖门：G8。风险：低
A0.8  models.dev 快照镜像/本地目录文件搭设（O4 源、G12 方案B 备选）。风险：低
A0.9  desktop 图标衍生件 staging（源树外生成，见 P9）。          风险：低

Phase 1 env/config 零风险组（组间顺序 O→G→H→F：opencode 冒烟已全通最稳，
        goose 次之，hermes 涉主模型切换有备份惯例，facai 本体涉服务重启放最后）──
 1.1  O1 opencode.jsonc 增 facai-glm provider + model/small_model
      （必须编辑现存 C:\Users\user\.config\opencode\opencode.jsonc——加载序
      config.json→opencode.json→opencode.jsonc 后者覆盖，不能另写新文件）。风险：低
 1.2  O2 opencode.jsonc 顶层 "autoupdate": false。                风险：低
 1.3  O3 opencode.jsonc 顶层 "share": "disabled"。                风险：低（能力关闭，非降权）
 1.4  O4 生成 C:\facai\opencode\models.json（从本机 cache 裁剪、api 字段全指
      127.0.0.1:9900/11434）+ launcher 成对注入 OPENCODE_MODELS_PATH 与
      OPENCODE_DISABLE_MODELS_FETCH=1（只设 PATH 不设 DISABLE 时后台 60min
      refresh 仍会按 cache mtime 出网，必须双设）。               风险：低
 1.5  O5 OPENCODE_AUTH_CONTENT 注入（依赖 A0.6）+ 不写 wellknown 条目 +
      不执行 opencode account login + 注入激活期间禁用 auth 写命令
      （auth/index.ts:72-80 set 会把 env 数据合并写回明文盘）。    风险：中
 1.6  O6 ~/.npmrc registry 内网化（镜像未就位先冻结面：不写 registry，
      仅预热 cache\packages\<pkg> + offline=true 兜底）。         风险：低
 1.7  G1 GOOSE_PROVIDER=ollama / GOOSE_MODEL=glm-4.7-flash /
      OLLAMA_HOST=http://127.0.0.1:11434（env 或 config.yaml 固化；依赖 A0.1）。
      注意不在 config.yaml providers map 给 provider 单独配远端 entry
      （providers.rs:79-84 map 优先于 GOOSE_MODEL fallback）。    风险：低
 1.8  G2 GOOSE_ADDITIONAL_CONFIG_FILES=<FACAI 策略 yaml>（压缩策略随 FACAI 注入）。
                                                                  风险：低
 1.9  G3 GOOSE_MODE: approve + GOOSE_PATH_ROOT=<FACAI 数据根>
      （permission.yaml 随之落 <root>/config/）。                 风险：低（审批变严）
 1.10 G6 GOOSE_DOCS_ROOT=<FACAI 自托管文档根 URL>。               风险：低
 1.11 G11 declarative provider：<config_dir>/custom_providers/facai-glm.json
      （结构=DeclarativeProviderConfig；base_url 可指 9900/v1 或 11434/v1，
      requires_auth=false 免 key 落盘）。                         风险：低
 1.12 G8 GGUF 预置（依赖 A0.7 + GOOSE_PATH_ROOT）。               风险：低
 1.13 G14 运行时遥测双保险：GOOSE_TELEMETRY_OFF=1（彻底移除在 P3）。风险：低
 1.14 G9 roaming：首选零动作（FACAI 构建不加 roaming feature，`goose roam` 面整体
      消失）；保留则 GOOSE_ROAM_RELAYS + GOOSE_ROAM_RELAY_TOKEN
      （fail-closed 不静默回退；P4 未打前哨兵值 ["disabled"] 不可用）。风险：低
 1.15 H1 D:/hermes/config.yaml：备份→:132 provider:"custom:facai-glm"→
      providers 段增 facai-glm(127.0.0.1:9900/v1, chat_completions)→
      :99 default:"glm-5.3-flash"；备用路径（GLM_BASE_URL env）与主案二选一不同改。
                                                                  风险：中（主模型切换）
 1.16 H2 两运行树 `git remote set-url origin <FACAI_INTERNAL_MIRROR>`（D:/hermes/
      hermes-agent 与 C:/hermes/hermes-agent）+ touch 两家目录 .skip_upstream_prompt
      （依赖 A0.5；无镜像时以 config.yaml:2199 check:false 单独成立，已在位）。风险：低
 1.17 H3 D:/hermes/.env 追加 SEARXNG_URL=http://127.0.0.1:8888（依赖 A0.2）；
      可选 FIRECRAWL_API_URL/KEY 指自托管 firecrawl；禁设任何 TOOL_GATEWAY_*。
                                                                  风险：低
 1.18 H4 阴影家目录 C:/Users/user/AppData/Local/hermes/config.yaml 补
      auth: adopt_external_logins: false（该键缺省按 True 生效，是唯一残留借入面）。
                                                                  风险：低
 1.19 H5 telemetry 保持关闭（D:/hermes:2171-2172 enabled/send 双 false 已在位，
      验证即可；不新增 endpoint 键）。                            风险：低
 1.20 F2 /etc/facai-cloud/providers/n8n.env（N8N_WEBHOOK_URL 指 127.0.0.1:5678 +
      N8N_SIGNING_SECRET，同值配进 n8n workflow HMAC 节点）+ systemctl restart
      facai-cloud（依赖 A0.3）。                                  风险：低
 1.21 F3 通知收敛 relay：只部署 relay.env（RELAY_SHARED_SECRET，同值配进 AutoClaw
      bridge）；不部署 telegram.env/whatsapp.env；清空 data/providers/wechat.env。
      （FACAI_NOTIFY_CHANNELS 显式化属 P11 可选。）               风险：中（桥端需同批配密钥）
 1.22 F4 模型出口换血：facai.env 增 FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1 与
      GLM_LOCAL_KEY=local-ollama → Ollama 侧 systemctl edit ollama 设
      Environment=OLLAMA_HOST=0.0.0.0:11434（守卫恒拒回环，必须私网监听）+ ollama pull glm4
      → POST /api/v1/my/models 注册 user-model（成为默认调度/转换模型，唯一无码默认位路径）
      → PUT /api/v1/registries/models/glm-local（供日报翻译/显式解析）
      → deepseek.env 改名 .disabled + restart（DEEPSEEK_API_KEY 消失后
      github_digest.py:169 空 key 跳过）。                        风险：中（默认模型换血）

Phase 2 补丁组（P10→P7→P8→P9→P1→P2→P3→可选；详见「# 补丁清单」）────
 2.1  P10 tron_sync.py:38 一行 env 化 → 随后 F1：providers/tron.env
      （TRON_API_BASE=http://127.0.0.1:9090；删 TRON_API_BASE 对应 TRONGRID_API_KEY 行）。
      顺序即风控：先打补丁再设 env，且自托管 java-tron 节点就绪前不设
      （未就绪即切→auto-approve 停摆、退人工 approve 兜底）。     风险：低（补丁）/中（切源）
 2.2  P7 桌面推理端点 env 化（两常量）→ D1 用 set FACAI_INFERENCE_URL 验证。风险：低
 2.3  P8 审批门恢复（git checkout HEAD 4+1 文件）→ vitest 10/10 + typecheck +
      dev 起 Gateway 页手工验证 High risk 徽标/强警告/Enable anyway。风险：中
 2.4  P9 图标 6 文件落位 → npm run build && npx electron-builder --win。风险：低
 2.5  P1 goose egress Allow→RequireApproval。                     风险：中（行为变审批）
 2.6  P2 goose acp top_up_url 重写（本地链该错误路径本就不触发，双保险）。风险：低
 2.7  P3 goose 构建特性裁剪（online-model-meta/telemetry/update 三开关）
      + 全量重建 + 回归（goose doctor / goose run）。             风险：中-高
 2.8  可选：P4（仅保留 roaming feature 时）、P5（与 P3 的 online-model-meta 移除
      二选一）、P6（更新源内部化，不推荐，被 N4 替代）、P11（F3 显式化一行）、
      P12（仅 Facai 与 Ollama 同机且不开私网时）。                风险：见补丁清单

Phase 3 native_fill / 长期项（依赖前两阶段或自建基建）────────────────
 3.1  N1 facai-cloud 补桌面契约端点（D2 前置）：facai/api.py 新增
      POST /api/device/code、POST /api/device/token（RFC 8628 设备码流+轮询节流+
      审批页挂 /login 或新增 /device）与 GET/POST/PATCH /api/agents
      （RemoteAgent 七字段强校验，401 语义，删除永不传播）；
      Mission 深度集成按桌面组 T2-B：kanban 五实体↔/api/v1/missions 映射层、
      run-stream.ts:34-52 四端点+七项协商平移为 /api/v1/runs。
      完成后桌面 set HERMES_API_URL=https://<facai-cloud 域名> 切换。风险：中（新认证面）
 3.2  N2 Facai tools/web 拆分填充验收：web_search_tool/web_extract_tool
      （web_tools.py:295/:385）本地后端接 SearXNG 容器 + 自托管 firecrawl，
      出网经 Facai Broker 白名单收口（H3 的能力侧收口）。          风险：低
 3.3  N3 OSV → Facai Broker 恶意包预检承接：Broker 跑 OSV 快照库
      （osv.dev 数据定期同步/导出 MAL-* 库）+ 本地静态审计（参考 hermes
      skills.ast_audit 思路），goose OSV_ENDPOINT 指 Broker 路由。 风险：中
 3.4  N4 更新通道 → Facai 版本管理器/发布流水线承接：opencode 侧后续 patch
      installation/index.ts:208 Installation.latest() 与 :147 upgradeCurl 改指
      FACAI 签名 release manifest + 内网安装脚本；goose 二进制升级纳入 FACAI 包管理
      （tagged 备份+cmp 校验协议可参考 autoclaw.patch.observable-binary，
      图谱 :100）；桌面 autoUpdater.ts:348-355 owner/repo 一行补丁属桌面构建期。风险：中-高
 3.5  N5 会话分享能力填充：Facai 会话导出/快照模块（session 导出 JSON/HTML 落本地）
      + opencode.web_session_ui（P2 队列，图谱 :98）覆盖『可看』；
      确需公网分享再自建 share 服务挂 enterprise.url 升 L2。      风险：低-中
 3.6  N6 插件生态填充：Facai ToolProvider/MCP 本地注册表（goose
      mcp.extension_manager 为 P1 候选通道，图谱 :131）；opencode plugin 面冻结为
      只读复用现有 node_modules（注意 @opencode-ai/plugin 1.18.15 与 CLI 1.18.34
      版本偏斜，内网化时经镜像重装对齐）。                        风险：中
 3.7  N7 autoclaw.im.delivery-outbox 并入 Facai（图谱 :85，native=✅）：租约式四态
      队列/effect_id 幂等（main.cjs:17905-17930）→ 并入 facai/api.py:303-311
      _relay_outbox + facai/channels/relay.py，把 QUEUED 记录升级为真队列，
      桥端零改动兼容（U3 的填充落地）。                           风险：中
 3.8  N8 OWNER 裁决项（只登记不代裁）：① hermes 源树两处『本地产品』补丁
      （source_check.py:383 更新检查摘除、tool_backend_helpers.py:22 entitlement
      移除）是否部署到 D:/hermes 与 C:/hermes 两运行树；② 阴影家目录
      %LOCALAPPDATA%\hermes（PATH 首选启动器指向它、provider=ollama-cloud→ollama.com）
      并入或废弃——不裁决则经 PATH 直启 hermes 仍走 ollama.com；
      ③ agnes/hb-api/abliteration 三条死配置清理。                风险：决策
 3.9  N9 n8n 彻底收起（U4 填充落地）：出站签名/重试语义（n8n.py:178-209）迁
      Facai 自有事件桥 + api.py:2058-2117 回调改走 Broker 入站 Action IR。风险：中

依赖主线速记：
  A0.1→G1→{G2,G7,G11}；A0.2→H3→N2；A0.3→F2；A0.4→G4/G5(OSV 半边)→N3；
  A0.5→H2；A0.6→O5；A0.7→G8；P10→F1；P7→D1(env 半边)；P3→G12/G13/G14(构建半边)；
  N1→D2；F4 注册的 user-model 是 goose/opencode CLI 子进程模型出口的公共上游
  （CLI 侧收口=配置接力，见 goose 组 T1 与 opencode 组 T1）。
跨组依赖（诚实声明）：9900 pivot 上游收口（本地 GLM/Ollama + key 池迁 secretbox.py
  注入 + 删内置共享 token，图谱 :172）归 autoclaw/proxy 组执行；该半完成前，
  经 9900 的任何链路（opencode O1、hermes H1、桌面 D1）不得声明『推理全本地』。
```

任务动作分布（33 条）：env 级 10、config 级 17、patch 任务 4 条（展开为下方 12 个补丁项：6 必做 + 6 可选/条件）、split_native 1（H3）、native_fill 1（D2）。

---

## # 统一 env 清单

五组 env 合并一块。用法：**[facai-cloud] 段贴 `/etc/facai-cloud/providers/*.env`**（systemd `EnvironmentFile=-/etc/facai-cloud/providers/*.env` 已通配加载，deploy/facai-tron-sync.service:19-20 等）；**[goose]/[opencode]/[hermes] 段是 Windows 侧工具**，经 Facai launcher 注入或 `set`（goose 亦可用 `%APPDATA%\goose\config.yaml` 固化，勿贴进 Linux providers）。`<...>` 为 Owner 占位符必须填实；注释行默认不启用；密钥统一经 Facai secretbox 派发，不落明文仓。

```ini
# =====================================================================
# FACAI 改根统一 env 清单（五组合并，2026-10-05，来源 REROOT_PLAYBOOK）
# =====================================================================

# ---------- [facai-cloud] 模型端点守卫 + 本地 GLM（F4；secretbox 守卫 surfaces[12]，回环恒拒） ----------
FACAI_ALLOW_PRIVATE_MODEL_ENDPOINTS=1
GLM_LOCAL_KEY=local-ollama
# 可选：仅随补丁 P12（secretbox 回环分支）启用，默认保持注释
# FACAI_ALLOW_LOOPBACK_MODEL_ENDPOINTS=1
# 注：服务端 Ollama 需 systemctl edit ollama 设 Environment=OLLAMA_HOST=0.0.0.0:11434
#     （这是 ollama 守护进程配置，与下方 goose 客户端 OLLAMA_HOST 语义不同，勿混填）

# ---------- [facai-cloud] 资金链本地节点（F1；先打补丁 P10 本键才生效；节点未就绪前不启用） ----------
TRON_API_BASE=http://127.0.0.1:9090
# 删除/不设下行——远程配额裁决随 header 消失（tron_sync.py:118-120 无 key 不加 TRON-PRO-API-KEY）：
# TRONGRID_API_KEY=

# ---------- [facai-cloud] n8n 自托管（F2；deploy/n8n/compose.yaml，127.0.0.1:5678） ----------
N8N_WEBHOOK_URL=http://127.0.0.1:5678/webhook/<your-path>
N8N_SIGNING_SECRET=<openssl rand -hex 32>

# ---------- [facai-cloud] 通知收敛 relay（F3；未打 P11 时以"仅部署本段+停用其它渠道 env"零代码达成） ----------
RELAY_SHARED_SECRET=<openssl rand -hex 32>
# 可选：仅随补丁 P11 生效（显式声明默认链=relay）
# FACAI_NOTIFY_CHANNELS=relay
# 停用面：不部署 telegram.env / whatsapp.env；deepseek.env 改名 .disabled；清空 data/providers/wechat.env

# ---------- [facai-desktop] 端点（D1/D2；D1 的 env 需先打补丁 P7 才生效，P7 前桌面已硬指 localhost:9900） ----------
FACAI_INFERENCE_URL=http://127.0.0.1:9900/v1
HERMES_API_URL=https://<facai-cloud 域名>
# HERMES_API_KEY=<经 secretbox 派发>          # 可选

# ---------- [goose]（Windows 侧；G1/G2/G3/G4/G5/G6/G8/G9/G14 + G11 走文件不入 env） ----------
GOOSE_PROVIDER=ollama
GOOSE_MODEL=glm-4.7-flash
OLLAMA_HOST=http://127.0.0.1:11434
GOOSE_MODE=approve
GOOSE_PATH_ROOT=<FACAI 数据根，绝对路径>
# GOOSE_ADDITIONAL_CONFIG_FILES=<FACAI 策略 yaml 绝对路径>    # 可选：压缩策略随 FACAI 注入
OSV_ENDPOINT=http://<FACAI-OSV-镜像>/v1/query
SECURITY_COMMAND_CLASSIFIER_ENABLED=true
SECURITY_COMMAND_CLASSIFIER_ENDPOINT=http://127.0.0.1:11434/v1
SECURITY_PROMPT_CLASSIFIER_ENABLED=true
SECURITY_PROMPT_CLASSIFIER_ENDPOINT=http://127.0.0.1:11434/v1
GOOSE_TELEMETRY_OFF=1
GOOSE_DOCS_ROOT=<FACAI 自托管文档根 URL>
# 仅当构建保留 roaming feature 时才需要（推荐：构建不含 roaming，面整体消失，见 P3 说明）：
# GOOSE_ROAM_RELAYS=["https://<FACAI-relay>/"]
# GOOSE_ROAM_RELAY_TOKEN=<token>
# declarative provider 免 env：<config_dir>/custom_providers/facai-glm.json（G11）
# whisper GGUF 免 env：预置 <root>/data/models/ 四文件（G8）

# ---------- [opencode]（Windows 侧 launcher 注入；O1-O6；O1 的 provider JSON 亦可直接写进 opencode.jsonc） ----------
OPENCODE_DISABLE_AUTOUPDATE=1
OPENCODE_DISABLE_SHARE=1
OPENCODE_DISABLE_MODELS_FETCH=1
OPENCODE_MODELS_PATH=<FACAI 模型目录文件，如 C:\facai\opencode\models.json>
# OPENCODE_CONFIG_CONTENT={"model":"facai-glm/glm-4.6","small_model":"facai-glm/glm-4.6","provider":{"facai-glm":{"npm":"@ai-sdk/openai-compatible","name":"Facai GLM","options":{"baseURL":"http://127.0.0.1:9900/v1","apiKey":"facai-local"},"models":{"glm-4.6":{"tool_call":true,"reasoning":false,"attachment":false,"temperature":true,"limit":{"context":128000,"output":8192},"cost":{"input":0,"output":0}}}}}}
# OPENCODE_AUTH_CONTENT={"facai-glm":{"type":"api","key":"<Owner 授权密钥，经 secretbox 派发>"}}   # 敏感；注入激活期间禁 auth 写命令
NPM_CONFIG_REGISTRY=http://127.0.0.1:4873/
# 显式固定家目录（免平台漂移，launcher 七件套余量）：
XDG_CONFIG_HOME=<FACAI 数据根>\opencode\config
XDG_DATA_HOME=<FACAI 数据根>\opencode\data
XDG_CACHE_HOME=<FACAI 数据根>\opencode\cache

# ---------- [hermes]（D:/hermes/.env；H1 备用路径 / H3 拆分填充 / H5 诊断） ----------
# GLM_BASE_URL=http://127.0.0.1:9900/api/paas/v4   # 备用路径（zai 语义保留）；与主案 providers 条目二选一，勿同改
SEARXNG_URL=http://127.0.0.1:8888
# FIRECRAWL_API_URL=<自托管 firecrawl>             # 可选：补抓取能力（web_tools.py:128 挂点）
# FIRECRAWL_API_KEY=<secretbox 派发>               # 可选
# HERMES_DIAGNOSTICS_BASE_URL=http://127.0.0.1:<本地收集端口>   # 可选：诊断上传改根（diagnostics_upload.py:13）
# ===== 禁令 =====
# 禁止设置 TOOL_GATEWAY_SCHEME / TOOL_GATEWAY_DOMAIN / <VENDOR>_GATEWAY_URL（hermes Nous 网关出口保持关闭，U1）
# 禁止开启 D:/hermes/config.yaml telemetry.shared_metrics 的 enabled/send 任一为 true
# 禁止部署 telegram.env / whatsapp.env / wechat.env（F3 收敛后）
```

配套文件键（非 env，勿混入本块）：goose `<config_dir>/config.yaml` 的 `GOOSE_TELEMETRY_ENABLED: false`；opencode `opencode.jsonc` 的 `"autoupdate": false`、`"share": "disabled"`、provider facai-glm 条目；hermes `config.yaml` 的 provider/default/updates.check/adopt_external_logins。

---

## # 补丁清单

12 项：P1/P2/P7/P8/P9/P10 必做，P3 强烈建议（构建级收口），P4/P5/P6/P11/P12 可选/条件。goose、hermes-desktop 源树改动前先建分支/记录 facai-changes.patch。

### P1 · goose egress 检测升级为拦截（必做）
- **文件/行**：goose 源树 `crates/goose/src/security/egress_inspector.rs:365-379`（注册点 `crates/goose/src/agents/agent.rs:778` 不动）
- **改动**：固定返回的 `InspectionAction::Allow`（只 LOG 不拦）改为 `InspectionAction::RequireApproval(Some("egress: " + domains))`，LOG 结构保留；补丁记入 fork 的 facai-changes.patch
- **回滚**：revert 该单 hunk（或从 facai-changes.patch 摘除对应段），回到"只 LOG 不拦"
- **风险**：中——工具外联由静默放行变审批事件，需审批链可用；误拦面后续接 Broker 白名单

### P2 · goose ACP top_up_url 重写（必做，双保险）
- **文件/行**：`crates/goose/src/acp/server.rs:1699-1707`（函数 credits_exhausted_prompt_error :1685）
- **改动**：`data.insert("url",…)` 前加重写：url 非 FACAI 域则替换为 FACAI 计费入口常量（或直接删除该 insert 行）
- **回滚**：revert 单 hunk；本地 provider（ollama）无 credits_exhausted 路径，回滚零功能影响
- **风险**：低

### P3 · goose 构建特性裁剪（强烈建议，一次重建收三面）
- **文件/行**：`crates/goose-cli/Cargo.toml`——:100 default 去掉 `"telemetry"`（定义 :123）；:111 default 去掉 `"online-model-meta"`（定义 :113）；:105/:126 default 去掉 `"update"`（或保留并在构建时 `cargo build --features disable-update`，:146 已有 `disable-update = []`）
- **生效点**：`crates/goose/src/lib.rs:31-32`（model_catalog 模块 cfg 裁除）、`lib.rs:39-40`（posthog 模块裁除，硬编码 POSTHOG_URL/KEY 随符号消失）、`crates/goose-cli/src/cli.rs:1045/1401/2919`（`goose update` 子命令整体消失）、`crates/goose-cli/src/commands/update.rs:308-312`（disable-update 时 bail "Update is disabled in this build."）。另：roaming 本就不在 default features（:95-112），FACAI 构建不加即无 roam 面，零改动
- **回滚**：恢复 Cargo.toml 对应行并重建
- **风险**：中-高——需全量重建+回归；移除的是遥测/在线模型目录/自更新面，能力本体不受损（模型目录走构建期内嵌快照，goose-provider-types/build.rs:5-13 zstd 内嵌、构建不联网）
- **互斥**：与 P5 二选一（online-model-meta）；与 P6 二选一（update）

### P4 · goose roam relay 哨兵禁用（可选，仅保留 roaming feature 时）
- **文件/行**：`crates/goose-cli/src/commands/roam.rs:120-127`（build_relay_settings）
- **改动**：识别哨兵值 `GOOSE_ROAM_RELAYS=["disabled"]` 返回 `RelaySettings::Disabled`（goose-roaming/src/relay.rs:25,73 已有 RelayMode::Disabled 分支，仅缺产出路径；现空列表回落四条 mesh-llm 默认 relay）
- **回滚**：revert 单 hunk
- **风险**：低-中

### P5 · goose CATALOG_URL 指内部镜像（可选，与 P3 online-model-meta 移除二选一）
- **文件/行**：`crates/goose/src/model_catalog.rs:5`
- **改动**：`CATALOG_URL = "https://models.dev/api.json"` → FACAI 镜像 URL（api.json 定期同步快照；刷新自带 ETag+15s 超时+大小上限，goose-provider-types/src/canonical/registry.rs:143-154）
- **回滚**：revert 单行
- **风险**：低

### P6 · goose 更新源内部化（可选，不推荐——高成本，被 N4 替代）
- **文件/行**：`crates/goose-cli/src/commands/update.rs:325`（下载 URL github.com/aaif-goose/goose/releases）、:129-133（attestation URL）、:273-277（Sigstore TrustedRoot + GITHUB_ACTIONS_ISSUER 策略）
- **改动**：三处换 FACAI release 源 + FACAI attestation 服务 + FACAI 签名链（必须同步替换签名策略，否则内部产物过不了 SLSA 校验被 fail-closed 拒装）；桌面主 feed 另需 autoUpdater.ts:348-355 一行补丁（属桌面构建期）
- **回滚**：revert 三 hunk
- **风险**：高——需自建整套签名+attestation 服务（Sigstore 生产 TrustedRoot 与 GitHub Actions 签发体系绑定，不可本地复用，见 U 节诚实边界）

### P7 · 桌面推理端点 env 化（必做）
- **文件/行**：hermes-desktop `src/main/agent-config-providers.ts:328`、`src/renderer/src/constants.ts:431-433`
- **改动**：`const HERMESONE_BASE_URL = process.env.FACAI_INFERENCE_URL?.trim() || "http://localhost:9900/v1";`；renderer 预设 `{id:"reproxy",…baseUrl}` 同法
- **回滚**：`git checkout HEAD -- <两文件>`（两处均单常量）
- **风险**：低

### P8 · 桌面高危工具审批门恢复（必做，收录前置）
- **文件/行**：`src/renderer/src/screens/Gateway/Gateway.tsx`、`src/shared/messaging-platforms.ts`、`src/shared/messaging-platforms.test.ts`；建议同批 `src/shared/agent-capabilities.ts`（能力协商诚实性）
- **改动**：`git checkout HEAD -- <上述文件>` 精确恢复 risk 审批门（messaging-platforms.ts:71/96 risk 字段、:131/138/144 terminal/file/code_execution 三 toolset 标 risk:"high"、:992 透出；Gateway.tsx:548 pendingRiskKey、:583 requestToolsetToggle 拦截、:854 高危徽标、:875 强警告面板、:898 Enable anyway）。依赖面已核存活：i18n 词条、main.css:10014-10087 样式、AlertTriangle 导入（Gateway.tsx:3）。禁止顺带恢复 .agents/.claude 两份 SKILL.md（另一议题）
- **回滚**：无需（恢复即上游态）；如需再删参考 facai-changes.patch 行 944-1174
- **风险**：中——恢复高危确认交互；基线 vitest 10/10 规划期已跑，恢复后用例名须变回 "surfaces per-platform toolsets with explicit high-risk markers"

### P9 · 桌面品牌图标替换（必做，资产级）
- **文件/行**：`build/icon.ico`、`build/icon.icns`、`build/icon.png`、`resources/icon.png`、`src/renderer/src/assets/iconv2.png`、`src/renderer/src/assets/hermes-icon.svg`
- **改动**：以 `facai-handoff/facai-cloud/facai/static/icon-512.png`（512×512 RGBA 锚图标，已视觉核验）为源，PIL 生成衍生件覆盖 5 处（ICO 含 16-256 多尺寸）；SVG 用 base64 内嵌 PNG 包装（`<svg…><image href="data:image/png;base64,…"/></svg>`）保持 BrandLogo.tsx:6 / HermesLogo.tsx:1 import 路径零改动；随后 `npm run build && npx electron-builder --win`
- **回滚**：`git checkout HEAD -- <6 文件>`
- **风险**：低——纯资产，重建后生效

### P10 · facai TRON_API_BASE env 化（必做先行，一行）
- **文件/行**：`facai/ops/tron_sync.py:38`（os 已在 :25 导入；fetch_transfers:146 / sync_once:285 的默认参数模块加载时绑定该常量，无需同改）
- **改动**：`DEFAULT_API = "https://api.trongrid.io"` → `DEFAULT_API = (os.getenv("TRON_API_BASE") or "https://api.trongrid.io").rstrip("/")`
- **回滚**：revert 单行（env 未设时默认行为不变）
- **风险**：低；配套 `/etc/facai-cloud/providers/tron.env`（0640）：`TRON_API_BASE=http://127.0.0.1:9090` + 删除 `TRONGRID_API_KEY` 行

### P11 · facai 通知渠道 env 化（可选一行，F3 显式化）
- **文件/行**：`facai/notify.py` 顶部补 `import os`；:29
- **改动**：`NOTIFY_CHANNELS = tuple(c.strip() for c in (os.getenv("FACAI_NOTIFY_CHANNELS") or "relay").split(",") if c.strip())`（默认收敛 relay；channel_notify_cursor :118-140 已持久，不回放旧事件）
- **回滚**：revert；不打补丁时 F3 用"仅部署 relay.env"零代码路径同样达成
- **风险**：低

### P12 · facai secretbox 回环放行分支（可选，仅同机部署且不开私网时）
- **文件/行**：`facai/secretbox.py:127` 与 `:131`；同步 `tests/test_user_models.py:77-82` 回环用例期望
- **改动**：增加 `FACAI_ALLOW_LOOPBACK_MODEL_ENDPOINTS=1` 分支放行回环模型端点（仅限模型端点校验路径），保留 `text.rstrip("/").endswith(":8765")` 对 Facai 自身 API 的拒绝
- **回滚**：revert 两 hunk + 恢复测试期望
- **风险**：中——放宽安全守卫；守卫矩阵 7 组基线规划期已实测，打后必须重跑回归

**patchCount = 12**（必做 6：P1/P2/P7/P8/P9/P10；构建级 1：P3；可选/条件 5：P4/P5/P6/P11/P12）。

---

## # 验证总表（能力真实恢复检查表）

33 项 = 五组任务 verification 合并。**状态列口径**：「组内已实测」=规划会话在该机实际执行并记录了输出；「待实施」=实施后必须逐条重跑。本手册生成会话仅做了句柄核对（图谱行抽验 + 四份 manifest 枚举），未执行任何能力验证，也未做任何实施动作。

| # | 能力面（图谱/manifest 句柄） | 验证：命令 → 期望 | 状态 |
|---|---|---|---|
| G1 | goose.agent.inference_loop（:127；goose.json surfaces[0]） | `ollama pull glm-4.7-flash` 后 `curl -s http://127.0.0.1:11434/api/tags` 含 "glm-4.7-flash"；`goose run --text "回复OK"` 正常中文回复且抓包/netstat 无 api.openai.com；`goose doctor` 显示 provider=ollama | 待实施（A0.1 先行） |
| G2 | agent.context_compaction（:128） | 长会话日志出现 "Exceeded auto-compact threshold"（agent.rs:2392）时抓包仅 127.0.0.1:11434；`goose run --text --debug` 摘要调用指向 ollama | 待实施 |
| G3 | permission.mode_gate（:129；surfaces[9]） | `goose doctor` 回显 GOOSE_MODE=approve；审批后 `<root>/config/permission.yaml` 生成（AlwaysAllow 记忆写入）；若用 smart_approve，工具调用时抓包仅 11434 | 待实施 |
| G4 | security.tool_inspection_chain（:130；surfaces[8][20][21]） | 加 npx/uvx 扩展 --debug 日志 OSV 请求目标=OSV_ENDPOINT 值，端点指本地空镜像时安装被拒；ENABLED 后注入模式提示触发分类器调用且目标 11434；P1 后 shell 执行 `curl https://example.com` 产生 RequireApproval 审批事件而非静默放行 | 待实施（P1 后） |
| G5 | mcp.extension_manager（:131；surfaces[23]） | 启动本地 stdio 扩展成功且 --debug OSV check 目标=OSV_ENDPOINT；config.yaml 无远程 MCP 条目时抓包零 MCP 外联 | 待实施 |
| G6 | skills.runtime_loading（:132） | `goose run --text "使用 load_skill 加载 goose-doc-guide 并复述文档根地址"` 输出 URL=GOOSE_DOCS_ROOT 配置值；抓包无 goose-docs.ai | 待实施 |
| G7 | agent.lead_worker（:133；锚点修正 orchestrator.rs:87-89/145-157） | `goose run --text "用 orchestrator 子代理总结当前目录"` --debug 子代理模型=glm-4.7-flash 且抓包仅 127.0.0.1:11434 | 待实施 |
| G8 | voice.local_dictation（:134；surfaces[14]） | 预置后断网听写选 Local 档可加载（is_downloaded 命中，无 huggingface.co 请求）；`dir <root>\data\models` 见对应 gguf | 待实施（A0.7 先行） |
| G9 | roaming.p2p_endpoint（:135；surfaces[10]） | 方案1 `goose roam --help` → "error: unrecognized subcommand 'roam'"；方案2 `goose roam share` 日志 relay=配置值且断自建 relay 时连接失败（不回退 mesh-llm）；方案3 日志 relays disabled / RelayMode::Disabled | 待实施 |
| G10 | acp.local_server（:136；surfaces[3] 关联） | 单测：构造 CreditsExhausted SystemNotification 调 credits_exhausted_prompt_error，断言 data.url=FACAI 值（纯函数可直测）；集成：本地 provider 全链 ACP 会话无任何 top_up_url | 待实施（P2 后） |
| G11 | provider.declarative_engine（:137） | `goose configure` provider 列表出现 facai-glm；`GOOSE_PROVIDER=facai-glm GOOSE_MODEL=glm-5.3-flash goose run --text "回复OK"` 正常且抓包仅 127.0.0.1:9900；custom_providers/facai-glm.json 存在 | 待实施 |
| G12 | model.catalog_refresh（:138；surfaces[11]） | 方案A 抓包无 models.dev 请求且 `goose doctor` 正常（模型目录来自内嵌快照）；方案B 镜像侧日志见 ETag 命中/刷新请求；方案C 启动日志 "failed to refresh remote model catalog" warn 且功能正常 | 待实施（P3 或 P5 后） |
| G13 | AUTOUPDATE（surfaces[6][7]） | 方案1 `goose update` → "Error: Update is disabled in this build."（update.rs:311 原文）；方案2 更新流量落 FACAI 源域名；桌面 `GITHUB_OWNER=<FACAI组织>` 后日志 "GitHubUpdater: API URL" 显示新 owner（githubUpdater.ts:467） | 待实施（P3/P6 后） |
| G14 | TELEMETRY（surfaces[12][13]） | 任一会话抓包无 us.i.posthog.com；P3 后 `findstr /m "posthog" goose.exe` 无匹配；`OTEL_SDK_DISABLED=true` 后无 OTLP 导出连接 | 运行时半边可先行；P3 后重跑 |
| O1 | provider_baseurl_config + session_compaction（:145/146/233；opencode.json surfaces[1]） | `opencode models` 含 facai-glm/glm-4.6；`opencode run -m facai-glm/glm-4.6 "回复OK"` 返回 OK；`curl -s -o /dev/null -w %{http_code} http://127.0.0.1:9900/v1/models` → 200 | 组内已实测（env 注入路径端到端「正常」）；固化进 jsonc 后重跑 |
| O2 | autoupdate（surfaces[0]） | `grep -n autoupdate opencode.jsonc` 见 "autoupdate": false；断网（封 opencode.ai/api.github.com）启动 TUI 一次正常、log 无 fetch 失败；对照（去开关+断网）出现升级检查失败日志证明开关真实生效 | 待实施 |
| O3 | session_share（:148/239；surfaces[7]） | `grep -n '"share"' opencode.jsonc` 见 "share": "disabled"；TUI 手动 share → 'Sharing is disabled in configuration'；封网跑全会话后 log 中 grep 'opncd.ai' 零命中 | OPENCODE_DISABLE_SHARE=1 run 组内已实测；jsonc 固化后重跑 |
| O4 | 模型目录本地化（surfaces[8]） | `OPENCODE_DISABLE_MODELS_FETCH=1 OPENCODE_MODELS_PATH=C:\facai\opencode\models.json opencode models` → 仅 Facai 定义条目、无 `opencode/` 前缀；封 models.opencode.ai 重跑 models+run 成功；`C:\Users\user\.cache\opencode\models.json` mtime 不再随运行更新 | 组内已实测（226 provider 坍缩为本地集、zen 条目消失）；固化后重跑 |
| O5 | 凭据/远程 config 面（surfaces[11][2][3]） | 移走 auth.json 后仅凭 OPENCODE_AUTH_CONTENT `opencode run -m facai-glm/glm-4.6 "回复OK"` → OK；断网重复成功；auth.json（如保留）`grep -c wellknown` 为 0；对照（去 env 且无 auth.json）应报缺 key | env 读取路径组内已实测；迁移（A0.6）后重跑 |
| O6 | plugin_hooks / npm（:147；surfaces[4]） | 镜像未就位：`NPM_CONFIG_REGISTRY=<不可达>` 下 run 与 plugin list 正常；镜像就位：`npm config get registry` → http://127.0.0.1:4873/，`opencode plugin install <测试包>` 成功且 cache\packages 出现新目录，封 registry.npmjs.org 重装仍成功；npm 安装法下 Installation.latest 落内网（installation/index.ts:225-230） | 推理零 npm 组内已实测（exit 0）；镜像侧待实施 |
| H1 | hermes 主模型供方（hermes-local.json surfaces[0][1]；本地供方=图谱 :172/:285） | `curl -s http://127.0.0.1:9900/v1/models` 含 glm-5.3-flash；`curl -s -X POST http://127.0.0.1:9900/v1/chat/completions -d '{"model":"glm-5.3-flash",…}'` → finish stop、content "pong"；`grep -n "provider:\|default:" D:/hermes/config.yaml` 第99行 default: glm-5.3-flash、第132行 provider: custom:facai-glm；改后重启会话，netstat 9900 出现 hermes PID、无 api.abliteration.ai 与 api.z.ai/open.bigmodel.cn 出网 | 前两项组内已实测（pong 端到端）；切换后重跑后两项 |
| H2 | 更新检查/更新源（surfaces[2]） | `grep -n "check:" D:/hermes/config.yaml` → 第2199行 check: false（组内已在位）；两树 `git remote get-url origin` → <FACAI_INTERNAL_MIRROR> 不含 github.com；`git -C D:/hermes/hermes-agent ls-remote origin` 镜像可达 | 待实施（依赖 A0.5） |
| H3 | tools.managed_gateway 拆分填充（:158/258；surfaces[3]） | `grep -rn "nousresearch" D:/hermes/.env D:/hermes/config.yaml` 零命中；`curl -s "http://127.0.0.1:8888/search?q=test&format=json"` 返回结果 JSON；hermes 执行 web_search 日志 backend=searxng（web_tools.py:126-131 级联选中）；运行树守卫未开时 `grep -n "return True" D:/hermes/hermes-agent/tools/tool_backend_helpers.py` 无命中 | .env 零命中组内已实测；SearXNG 侧待实施（A0.2） |
| H4 | Portal 身份/外部登录借入（surfaces[4]） | `grep -n "adopt_external_logins" D:/hermes/config.yaml` → 第2183行 false（组内已在位）；`python -c "import json;d=json.load(open(r'C:/Users/user/AppData/Local/hermes/auth.json'));print(list(d.get('providers',{}).keys()))"` → ['zai','ollama-cloud'] 无 nous（组内已实测）；阴影 config 加固后 `grep -n -A1 "^auth:"` 见 false；运行期无 portal/inference-api/welcome-api.nousresearch.com 连接 | 仅阴影家目录一步待实施 |
| H5 | 遥测/诊断上传（surfaces[7][11]） | `grep -n -A4 "^telemetry:" D:/hermes/config.yaml` → :2171 enabled: false、:2172 send: false（组内已在位）；`cut -d= -f1 D:/hermes/.env \| grep -iE "TELEMETRY\|TOOL_GATEWAY"` 零命中（组内已实测）；netstat/代理日志无 telemetry.nousresearch.com / portal.nousresearch.com；如设 HERMES_DIAGNOSTICS_BASE_URL，debug 上传时本地端收到 POST /api/diagnostics/upload-url | 已在位；负向断言实施期核 |
| D1 | desktop.provider-mirror（:64/116/192） | `curl -s -m 5 http://127.0.0.1:9900/health` 含 "status":"ok" "port":9900；`curl -s -m 8 http://127.0.0.1:9900/v1/models` 含 glm-5.3-flash；Providers 页选 ReProxy 预设发消息有回复；P7 后 `set FACAI_INFERENCE_URL=http://127.0.0.1:9900/v1` 重启发消息仍通 | 链路两段组内已实测（health/models/monitor_9900.log 18 条连续成功）；env 化待 P7 |
| D2 | 桌面壳云通道（:178） | `curl -s -X POST https://<cloud>/api/device/code -d '{"device_name":"probe"}'` → 含 user_code/device_code/verification_uri/expires_in/interval；批准前 poll /api/device/token → {"error":"authorization_pending"}；浏览器批准后再 poll → access_token+user；桌面登录弹窗 "Signed in as…"；Profiles 同步不再报 "Cloud agents unavailable (HTTP …)" | 待实施（依赖 N1 端点补齐） |
| D3 | messaging-gateway-catalog 前置 + chat-approval-flow 同源（:63/114/186 + :38/187） | `npx vitest run src/shared/messaging-platforms.test.ts` → 10/10 且用例名 "surfaces per-platform toolsets with explicit high-risk markers"；`npm run typecheck` 绿；`npm run dev` 起 Gateway 页 → Telegram 的 Terminal 工具集关再开 → 红色 "High risk" 徽标 + 强警告确认框 + 必须点 "Enable anyway" 才启用 | 待实施（P8；删除态基线 10/10 组内已跑） |
| D4 | 品牌资产（:60/182） | 像素级 `python -c "…print(a.size, list(a.getdata())==list(b.getdata()))"` → (512,512) True；`Image.open('build/icon.ico').info.get('sizes')` 含 (256,256)；`npm run dev` 任务栏/窗口为锚图标；打包后 exe 属性图标、Welcome/About 品牌位为锚图标 | 待实施（P9） |
| F1 | 资金裁决/配额（facai-remote-integrations.json surfaces[0][1]） | 补丁后 `TRON_API_BASE=http://127.0.0.1:9090 python -c "from facai.ops.tron_sync import DEFAULT_API; print(DEFAULT_API)"` → http://127.0.0.1:9090；`python -m facai.ops.tron_sync --once` 输出 {"new_transfers":N,"auto_approved":[…],"scanned":N} 且无 "TronGrid returned HTTP" 重试；真实 USDT 到账后 PENDING recharge 30s 内 credited、events source=chain-sync；停本地节点 --once 以 HTTPError 退出且常驻循环打印 "tron-sync pass failed" 存活、recharge 不被伪造入账 | 待实施（P10 + 自托管节点；U2） |
| F2 | n8n 出站/回调（surfaces[6][7]） | console n8n 面板 state=CONFIGURED（组内已用相同 env 实测判定路径）；创建测试 mission → 本地实例 Executions 出现事件且 X-Facai-Signature 验签通过；docker stop 容器后按 RETRYABLE_ERRORS 有界重试 N8N_MAX_ATTEMPTS=3 次后诚实记审计、流水线不阻塞；手动 POST /api/v1/n8n/callback（带签名）推进 step、错签 403 | 待实施（依赖 A0.3） |
| F3 | 通知收敛 relay（surfaces[8][17]） | `python -m pytest tests/test_channel_notify.py tests/test_relay_channel.py -q` 全绿；GET /api/v1/channels → telegram/whatsapp/wechat UNCONFIGURED、relay CONFIGURED；STEP_COMPLETED 后 `sqlite3 …/facai.sqlite3 "select channel,status,count(*) from channel_messages group by 1,2"` 出现 relay\|OUTBOUND\|QUEUED 且其余渠道无新 OUTBOUND；AutoClaw 签名拉取 ack 后变 SENT 且不再返回（幂等） | 待实施 |
| F4 | 模型出口换血（surfaces[12][13]） | `python -m pytest tests/test_user_models.py tests/test_registry_binding.py tests/test_gateway.py -q` 全绿（守卫矩阵 7 组组内已实测为基线：默认拒 http/私网/回环，flag 后私网 ACCEPT、回环恒 REJECT、:8765 尾缀拒）；POST /api/v1/my/models/{id}/test → {"status":"MODEL_COMPLETED"}；POST /api/v1/missions/auto → events 中 PROMPT_CONVERTED.payload.model_id=user-<id> 且 plan_source 非 deterministic；digest 手动跑一次，中文行由 Ollama 执行日志佐证（github_digest.py:187 content 非空）；期间 `ss -tnp \| grep deepseek` 为空 | 待实施（守卫矩阵与 n8n/DEFAULT_API 导入为组内已实测） |

---

## # unreplaceable 与拆分填充

判定口径：**功能本体在远端/外部、本地无等价组件，env/config/补丁只能收『指向』不能造『能力』** → 标 unreplaceable 并给拆分填充计划。33 条任务中 **4 条**涉此口径（hermes 1、facai-本体 3）；goose 14 条、opencode 6 条、桌面 4 条零 unreplaceable（各组规划结论，句柄逐条复核一致）。facai-本体组 honest_gaps 第 3 项（CLI 子进程模型出口不在本组）**不计入**——它是跨组依赖而非缺失能力，已由 goose 组 T1 与 opencode 组 T1 的改根方案覆盖，F4 注册的 user-model 条目即公共上游。

### U1 · hermes tools.managed_gateway 能力本体（图谱 :158/:258，L1_WRAPPED，native=false）
- **不可替代点**：firecrawl 抓取/perplexity 搜索由 Nous 托管供给、entitlement 由 Nous 账户裁决（managed_tool_gateway.py:196 docstring "entitled accounts only"）；TOOL_GATEWAY_DOMAIN 只能换 endpoint，且 :170-172 硬性要求 nous_user_token 非空——只把域名指本地而无本地令牌签发器＝假解锁，本计划明确不做。
- **拆分填充**：拆『托管搜索/抓取』需求 → Facai tools/web：搜索=本地 SearXNG 实例（SEARXNG_URL 现成挂点 web_tools.py:127/:218，级联位置 firecrawl-gateway 之后 ddgs 之前）；抓取=自托管 firecrawl（FIRECRAWL_API_URL/KEY，:128）；出网经 Facai Broker 白名单收口（与 goose security.tool_inspection_chain 收口思路同源）。落地：H3（env 半边）+ 步骤 3.2/N2（能力验收半边）。

### U2 · TRON 链上资金真伪的本地节点级校验（surfaces[0][1]）
- **不可替代点**：本地现无节点级校验能力；TronGrid REST API 服务端不开源，树内无现成兼容层（不虚构项目名）；自托管 java-tron 全节点首次同步需数小时~数天、磁盘数十 GB 起。P10 只把裁决根的『指向』收回本地。
- **拆分填充**：ops/tron_sync.py 已把『拉取』（fetch_transfers :146-158）与『记账/匹配』（record_transfers :180-201、match_recharges :204-269）解耦——兼容层只需重写 fetch_transfers 一个函数：facai/ops/ 新增 tron_index.py（本地节点事件订阅 → chain_transfers 同一 store 契约）；或注入第二数据源交叉校验、不一致人工复核（manifest 建议已给）。兼容层契约见 F1 任务（token_info.address 必须等于 USDT_TRC20 合约全串，:37/:193 合约校验是防伪入账闸，不得放宽）。落地：P10 + F1；既有兜底=store 管理员人工 approve（不依赖任何远端）。

### U3 · 渠道最后一段触达（surfaces[2][3][4][5]）
- **不可替代点**：notify 收敛后，operator 手机上真正弹出消息的那一跳仍由官方服务器裁决——Telegram Bot API / WhatsApp Cloud API（graph.facebook.com）/ 微信 iLink（ilinkai.weixin.qq.com）掌握投递真伪、认证（401/403）与配额。
- **拆分填充**：Facai 侧全部收口（outbox=投递根，F3）；P1 能力 autoclaw.im.delivery-outbox（图谱 :85，native=✅：租约式四态队列、effect_id 幂等、claim 联动 CHECK、重试调度，main.cjs:17905-17930）→ 并入 facai/api.py:303-311 _relay_outbox + facai/channels/relay.py，把 QUEUED 记录升级为真队列，桥端零改动兼容。落地：步骤 3.7/N7。微信接口版本强制（wechat.py:43，surfaces[3]）与 iLink 授权签发属渠道组范围，F3 先把 wechat 从默认链路降权。

### U4 · n8n 本体为 SUL 许可外部件（surfaces[6][7]）
- **不可替代点**：源码可用但非 OSI 开源，deploy/n8n/README.md 明确许可边界（不得 vendor 进 FACAI、不得作为对客工作流服务）；workflow 本身仍需人工在编辑器维护。自托管只是把裁决根移到本地实例，本体仍是外部件。
- **拆分填充**：短期=自托管（deploy/n8n/compose.yaml，127.0.0.1:5678 loopback-only，n8n 2.41.5，F2 零代码）；彻底收起=经 Facai Broker 入站能力替代 n8n 回调/出站——出站签名/重试语义（n8n.py:178-209）迁 Facai 自有事件桥 + api.py:2058-2117 回调改走 Broker 入站 Action IR。落地：步骤 3.9/N9，属后续专项。

### 诚实边界（非 unreplaceable，实施与验收时必须知道）
1. **Sigstore SLSA 验证链不可本地复用**（goose）：update.rs:273-277 TrustedRoot 绑定 Sigstore production、GITHUB_ACTIONS_ISSUER 绑定 GitHub Actions 签发体系；内部更新源须自建整套签名+attestation 服务——故 goose 更新先以 P3 关闭（成本≈0），语义拆给 Facai 版本管理器（N4）。
2. **desktop electron-updater 主 feed**（autoUpdater.ts:348-355 owner/repo 硬编码）不可 env 改，一行补丁属桌面构建期收口；githubUpdater 回退面 env 可改（GITHUB_OWNER/GITHUB_REPO）。
3. **OSV 本地镜像无现成实例**：OSV_ENDPOINT 只解决『指哪』；未就绪期间扩展安装 fail-closed 被拒（可接受但影响体验），承接计划=N3。
4. **RelaySettings::Disabled 无配置产出路径**：哨兵值需 P4（5-10 行）；不改码的等价解=构建不含 roaming feature（能力不保留，面整体消失）。
5. **GLM 本地能力上限受显存约束**：本机 Ollama 实测仅 qwen36 系 5 模型、无 GLM；本地档为 glm-4.7-flash（30B）/glm4:9b，glm-5.3-flash 属 Ollama cloud 档 pull 不到；pivot-9900 虽在 9900 口提供 glm-5.3-flash，上游收口归 autoclaw 组——半完成前经 pivot 的 GLM 链不等于纯本地。
6. **PostHog URL 为编译期常量**（posthog.rs:16-17）：env 只能关上报不能改端点；彻底移除只能 P3 关 telemetry feature（非 unreplaceable，仅注明 env 手段边界）。
7. **opencode 五个子面**：升级『可升级』本体、分享『可分享』本体、第三方 plugin 生态、models 目录内容治理（`opencode models --refresh` 仍会主动拉网，Owner 规范禁用）、wellknown/account 消极收口（不激活即整块跳过）——填充落点 N4/N5/N6。
8. **hermes 漂移与双家目录**：源树两处补丁未同步运行树、阴影家目录 %LOCALAPPDATA%\hermes 仍指 ollama.com——均属 N8 OWNER 裁决，不裁决则 PATH 直启 hermes 绕过改根。
9. **GLM_BASE_URL 备用路径**：依赖 pivot 的 /api/paas/v4 路由，仅实测路由存在（HTTP 200），与 z.ai 官方字段兼容性未端到端验证；主推 providers 条目方案（/v1 已端到端实测）不依赖该兼容性。
10. **pivot 路由别名**：经 9900 调 glm-5.3-flash 时响应 model 字段为 deepseek-flash（组内实测）——hermes 按请求 id 使用不受影响；上游路由归属 facai-proxy/autoclaw 组收口。
11. **facai-cloud 管理员守卫缺口**（组内实测发现）：管理员 PUT /api/v1/registries/models 完全绕过 secretbox 端点守卫（api.py:1334 仅限 tenant API-key 会话），AUTHORITY_SURFACE_INVENTORY.md:88 的『★关键闸门』实际只闸 /api/v1/my/models 与 tenant 路径——建议治理侧补管理员路径守卫；本手册方案仍按守卫规范统一用非回环地址保持全链一致。
12. **deepseek 默认位复活机制**：registries.py:151-162 defaults 恒置前 + :149 启动时从 DEFAULTS 重新 deepcopy、无通用 registry DELETE 端点——默认位换血唯一无码路径是 F4 的 user_model_id 恒优先（api.py:1627-1628）+ deepseek.env 停用，勿误删 DEFAULTS。
