---
name: model-fleet-status-probe
description: 模型舰队健康探针：枚举 serverless 推理目录（GLM、DeepSeek、Qwen、Kimi、Llama、Mistral 等三百余款），逐款探活可用性与首字延迟，按任务类型维护路由表（创意/推理/长窗/代码各留主备），异常自动故障转移并出每日模型保鲜报告。适用于执行工作阶段的模型绑定、验证结果阶段的连通性核验、每日循环的保鲜巡检。
license: NOASSERTION
source_url: https://featherless.ai/status
---

# 模型舰队健康探针（来源：Featherless.ai Status 可观测面）

## 目录枚举

Featherless 的 status 页是现成的"当天可用模型"清单：按厂商分组
（Apertus/Arcee/GLM/DeepSeek/Qwen/Kimi/Llama/Mistral/…），每款即开即用。
每日抓取该页做差分：新增了哪些模型、哪些下架——新增即评估收录，
这就是"模型保鲜 12 小时"的平替实现。

## 探活与路由

1. 探活：对目录内主用模型发最小推理请求，记录首字延迟与错误率
2. 路由表：按任务类型维护主备（创意生成/数学推理/长窗摘要/代码各一主一备）
3. 故障转移：主用连续两次失败即切备用并记录事件
4. 保鲜报告：每日差分 + 探活结果落 docs，异常非零退出可挂巡检

## 与 facai 的对接

- 转换台派发前的模型绑定健康预检（避免把阶段派给失联模型）
- github_digest 的姐妹循环：模型目录每日差分进案例库
- verifier 的连通性核验清单来源
