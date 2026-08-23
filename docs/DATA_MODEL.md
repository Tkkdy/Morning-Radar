# 数据模型

所有核心模型使用 Pydantic，时间必须带时区，并可稳定序列化为 JSON。

## RawItem

采集器的统一输出。它保存真实 URL、来源、作者、发布时间、抓取时间、短摘要、
有限长度摘录、候选主题/实体和来源特有元数据。稳定 ID 优先由规范化 URL 的哈希生成。
缺少发布时间允许保留，但会降低可信度；不保存完整正文。

## Story

表示同一事件的多来源合并结果。它记录规范标题、分类、实体、产品、主题、时间、
原始条目 ID、全部来源 URL、主来源、事实、分析、不确定性和四项评分。生命周期状态
仅在证据明确时使用 `rumor`、`official_teaser`、`announced`、`available` 或 `updated`，
否则为 `unknown`。

## Signal

表示有结构化证据支持的趋势信号。它包含信号类型、主题、窗口天数、支持事件、
来源数、公司数、指标历史、强度、解释和不确定性。单一来源重复不能构成趋势。

## BriefItem

晨报中的单项展示对象，包含栏目、标题、发生了什么、为何重要、市场/社区反应、
不确定性、来源 URL 和 Story ID。其 URL 必须是对应 Story 已验证 URL 的子集。

## DailyBrief

单日最终产物，包含日期、时区、生成时间、今日重点、市场与公司、AI 与开源、
趋势雷达、开发者讨论、方向观察、认知延伸、继续观察和运行统计。方向观察与认知延伸
允许为空；空栏目不在网页显示。

## DailyEditorialDecisions

表示某日完整 Story 批次的独立编辑判断，记录 profile version、Shadow/Active 状态、是否
degraded，以及每个 Story 的 placement、treatment、reader/evidence value、fact status、
置信度、news delta、趋势链接和 SUPPORT 目标。Story 的结构与 URL 合法不等于事实已独立
验证。`verified_fact` 必须有输入 `source_refs` 证据；官方来源可以验证其发布、价格、许可证、
政策等客观动作，市场来源可以验证客观数字，但官方 benchmark 或能力主张仍需要独立实践、
测试或复现才能视为已验证能力。

Editorial 决策不嵌入或改写 Story。DROP 仍可通过 `retain_for_trends` 保留证据，SUPPORT 只能
指向同批存在的非 DROP、非 SUPPORT Story。

## 存储布局

- `data/raw/YYYY-MM-DD.json`：必要采集元数据。
- `data/stories/YYYY-MM-DD.json`：去重、聚类后的事件。
- `data/signals/YYYY-MM-DD.json`：可解释趋势。
- `data/snapshots/github/`、`market/`：每日指标快照。
- `data/briefs/YYYY-MM-DD.json`：最终晨报。
- `data/editorial/YYYY-MM-DD.json`：按 profile version 保存的独立编辑判断批次。
- `data/state/`：通知幂等与采集缓存头等小型状态。
