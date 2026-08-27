# 架构说明

Morning Radar 保持 Python 模块化单体：一个包、一个仓库、一条主要流水线，不引入数据库
服务、微服务、向量数据库、重型 Agent 或前端框架。

```text
Collector RawItem
  → Candidate Admission / High-Recall Guardrail
  → Semantic Triage (DROP | BUILD | INVESTIGATE)
  → bounded Evidence Resolution
  → Claim × Evidence Story Boundary
  → Story
  → Continuity / Trend / Tendency / Editorial / Brief / Publishing
```

早期阶段 Recall First，允许 Candidate 保存 hypothesis、潜在新颖性、潜在影响和不确定性；
Evidence Resolution 与 Story Boundary Accuracy First；最终 Reader Selection Precision First。
信息淘汰速度不能快于信息理解速度。

## 信息模型

系统只有三个主要信息层级：

- `RawItem`：Collector 实际观察到的原始元数据。
- `Candidate`：系统针对一个或多个 RawItem 形成的待验证事件假设。
- `Story`：已跨过 Claim–Evidence Boundary、可供事实型下游消费的事件。

旧 `ResearchCase` 已由 Candidate lifecycle 取代。Evidence Resolution 是 Candidate 的调查能力，
不是第四个信息对象。未解决但仍有观察价值的 Candidate 可以投影为 `RadarSignal`，不能直接进入
Continuity、Trend、Tendency 或 Brief。

Candidate 分开保存三类状态：

- Semantic：`DROP / BUILD / INVESTIGATE`
- Evidence：`SUFFICIENT / PARTIAL / INSUFFICIENT / CONTRADICTED`
- Execution：`NOT_NEEDED / NOT_STARTED / EXECUTED / DEFERRED_BY_BUDGET /
  FAILED_NETWORK / FAILED_PARSE / FAILED_AI`

UNKNOWN、预算不足、网络超时和解析失败都不等于 DROP。DROP 必须有稳定的 semantic reason
code；INVESTIGATE 必须有 missing evidence、verification target 和 verification path；BUILD 只
代表可以尝试 Story Construction，不代表已经验证。

## 模块职责

- `collectors`：把 RSS、GitHub、Hacker News、市场或 Fixture 转为 `RawItem`。
- `candidates`：Candidate Admission、High-Recall Guardrail、Semantic Triage、claim-specific
  freshness guard 和 RadarSignal 投影。
- `evidence`：有界 direct fetch、Official Surface 验证、trust cache 与 Candidate Evidence 更新。
- `processing`：规范化、时间窗、保守聚类、Story Construction、Claim–Evidence 校验与评分。
- `ai`：统一 Provider contract；生产默认 DeepSeek，保留 OpenAI，Fixture 使用 Fake。
- `continuity`、`trends`、`tendencies`：只读取可信 Story，不读取未验证 Candidate。
- `editorial`：对完整 Story 批次做 Shadow reader/evidence judgement。
- `briefing`：在 `maximum_brief_items` 内生成最终晨报。
- `diagnostics`：为每个正式接收的 RawItem 保存 compact Decision Trace。
- `storage`、`publishing`、`notifications`：JSON 持久化、静态网站和幂等通知。
- `pipeline`：顺序编排并隔离来源、AI、Evidence 和下游故障。

## Candidate Admission 与 Triage

Candidate Admission 回答“Raw 是否值得形成事件 hypothesis”；Semantic Triage 回答“接下来值得
消耗什么资源”。工程上二者只使用一次 batch AI Triage，不叠加旧 classify call。

首次语义理解不再由 Story 最坏调用成本反推。`maximum_triage_batch_items` 与
`maximum_triage_input_characters` 控制 Triage；`maximum_story_candidates` 独立控制昂贵 Story
Construction。High-Recall Guardrail 只产生 `MUST_TRIAGE`，绝不产生 MUST_STORY、MUST_BRIEF
或 relevance 真值。

模型 prior 可以帮助形成 hypothesis、potential novelty/impact、alternative explanation 和调查
方向，但不能进入 Candidate Evidence、Story fact、official status、release confirmation 或 URL。

## Evidence Resolution

Evidence Resolution 按一个具体 claim gap 工作，并受 `maximum_investigations` 硬上限约束：

1. 协调本轮已有 Evidence。
2. 对现有 destination URL 做 bounded direct fetch。
3. 使用配置内 Known Official Surface 做 deterministic trust verification。
4. 当前 claim 得到有权限的 Evidence 后停止；不执行开放式 Web Search 或自由 follow-links。

Official Surface 身份来自 `config/official_surfaces.yaml` 的小型可信 seed、确定性子域关系和
`data/state/official_surfaces.json` trust cache。缓存记录 entity、relationship、verified_via、
verified_at、status 和 confidence，并在失效期后重新验证。AI 没有认证权。

Evidence fetch 独立于 Collector HTTP：拒绝 IP literal、`.local`、任意端口，以及解析到
loopback/private/link-local/multicast/reserved/unspecified 地址的目标；每次 redirect 重新验证。
请求有 timeout、有限 retry 和明确 UA，不发送 Cookie/Authorization，不执行 JavaScript，只接受
明确文本/HTML/JSON 类型并限制响应大小。单个失败只更新 execution state。

## Story Boundary

Discovery provenance 与 Evidence provenance 分开保存：HN source/discussion 说明“如何发现”，
Candidate Evidence 说明“谁能证明”。Story 每条 fact 必须有一条同文 claim support，绑定真实
Evidence ID、claim type、evidence scope、claim scope 和 scope-supported 结果。

硬边界：`Claim Scope ≤ Evidence Scope`。

- discovery-only input 不能支持 Story fact；
- practitioner observation 可以支持该观察者/部分账户的具体现象，不能扩大为正式 GA；
- verified official primary 可以证明主体自己的发布、能力、价格和 rollout 声明；
- 官方性能数字必须保持“官方宣称”归因；
- novelty/first claim 需要独立 Evidence；
- Evidence 冲突或核心 claim 不闭环时，Story Construction 可以拒绝 BUILD。

Story 继续隔离 `facts / analysis / uncertainties`。只有 Story 能进入事实型下游。

## Budget

`maximum_ai_calls` 仍表示逻辑 AI 操作，输入字符和 Provider token usage 分开记录。B0.5 使用
Protected Minimum + Shared Pool：Triage、Story、Editorial、Continuity、Tendency、Brief 各有
最低保障；阶段完成后未使用资源回到共享池，阶段可以借用不会威胁后续最低运行的余额。

Budget Sweep 同时记录 logical calls、input characters、actual token usage（Provider 支持时）、
proxy cost、HTTP fetch、investigation workload 和 runtime。当前不实现 autonomous optimizer。

## Decision Trace 与持久化

每天新增：

- `data/candidates/YYYY-MM-DD.json`
- `data/diagnostics/YYYY-MM-DD.json`

Trace 覆盖 Raw acceptance、freshness、routine filter、Candidate admission、guardrail、Triage、
investigation、Evidence state、Story Construction、Reader Selection 和 final disposition。只保存
decision、reason code、state transition 和短 rationale，不保存文章全文或模型 chain-of-thought。

原有 Raw、Story、Signal、Brief、Continuity、Tendency、Editorial JSON 保持兼容。历史 Story 新增
字段都有安全默认值，不要求重写旧快照。

## 下游与失败降级

Continuity、Trend、Tendency、Brief、Publishing 和 Notifications 不做非必要重构。Editorial 继续
`shadow_mode: true`，最终 Brief cap 不因内部 Recall 增加而变化。

Collector 单源失败继续隔离；Triage 失败保留 unresolved Candidate；Evidence 网络失败保留原
semantic disposition；Story 失败留下拒绝 Trace；Brief 失败只复用已验证 Story facts。Fixture
路径不访问网络、不调用真实 AI、不发送通知。

## Evaluation

`python -m morning_radar.evaluation.b05` 使用冻结的历史 Raw input 提供：

- Legacy vs B0.5 same-budget architecture comparison；
- B0.5 多档 Budget Sweep；
- Major-event Recall、Evidence Integrity、资源成本、runtime 与 marginal cost proxy。

FakeAI replay 用于确定性 routing/cost 回归，不替代真实 Provider 的 Potential Impact、Potential
Novelty、false-positive 和 claim wording 语义评估。真实语义评估必须作为独立手动任务运行，且
不能触发生产 Pipeline、发布或通知。
