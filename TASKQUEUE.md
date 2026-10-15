# FACAI Cloud Worker 任务队列（云端 Worker 每小时取一件执行）

> Worker: VPS opencode(glm-4.5-flash) · 产出推 queue-work 分支 · 主会话审后合并 master

- [x] 在 docs/WORKER_LOG.md 记录本 worker 的首次运行（时间、模型、一句话自述），没有该文件则创建

- [x] 给 facai/authority_gate.py 的 mission grant 加 SQLite 持久化（cf8a874 已落地：attach_store 重载+重启存活测试）
- [x] 在 facai/api.py 渠道回复链路加"审批交互"（46b31d0 交付：GRANT_REQUIRED 中文消息+回复"批准"签发一次性 grant；04eb2bf 升级 A 方案：一次批准管全程 uses=剩余步骤）
- [x] 给 facai/outcomes.py 的 ActionIR 提案加 risk_level 字段（46b31d0 交付：shell.exec/docker.run 默认 high，filesystem.write/network.* medium，纯标注不改判定）
- [x] facai/billing.py 加 80% 软阈值告警（46b31d0 交付：user_alerts 每账期恰好一次）
- [x] facai/api.py 客户绑定流程加 test_credential 预检（46b31d0 交付：真实 getMe/bot-info，失败 422 带上游原因；重复条目已合并）
- [x] 对标吸收：relay 协议公开化补 Discord/Slack 两通道适配器（2be4549 随每日备份扫入交付：discord=桥 HMAC X-Facai-Signature、slack=原生 v0 HMAC+重放窗口；签名 webhook→dedupe→全 DAG 派发、bot push、TEXT_TOO_LONG/UNCONFIGURED 诚实语义，test_discord/test_slack 共 37 用例；NOTIFY_CHANNELS 与 dispatch 家族已扩；全量 1116 绿）
- [x] 对标吸收：Dify 插件 manifest 模式——Capability Foundry 的收录 CLI 产出带权限声明的 capability manifest，注册时映射为受限 grant 模板（2c29408 随 2026-10-06 daily backup 交付：ingest.py derive_permissions/_entry_permissions 六族 deny-by-default + capabilities.py grant_templates_for/manifest_grant_templates 受限映射；test_ingest.py §i 两用例断言 permissions 节/schema/逐条 scope；主会话 2026-10-11 复核 1171 绿后补勾）
- [x] Discord/Slack 客户自助绑定：CUSTOMER_BINDABLE_CHANNELS 扩容 + 凭证预检（discord GET /users/@me、slack auth.test）+ 控制台/移动表单（5a45633 交付控制台面板+移动表单+真后端 Playwright 用例；17a1d5a 补 Channel test-push 端点（TEST_PUSH_TEXT 走 NOTIFY 同路）；api.py:298 已含四通道、:376/:380 预检实现；主会话 2026-10-11 复核后补勾）
- [ ] WhatsApp 配对码宿主（§二十一 新波候选：免扫码终端的第二宿主形态，服务器自持会话之外）
