# 架构说明

Morning Radar 采用模块化单体：一个 Python 包、一个仓库、一条主要流水线。这样保留
清晰职责边界，又避免 v0.1 引入服务编排、数据库和队列的运维成本。

```text
采集 → 清洗 → 去重 → 事件合并 → 评分 → 趋势 → 晨报 → 网页 → 推送
```

## 模块职责

- `collectors`：把 RSS、GitHub、Hacker News、市场或 Fixture 转为统一 `RawItem`。
- `processing`：规范化 URL/标题、按时间窗过滤、去重、聚类与评分。
- `ai`：定义最小 Provider 接口；生产默认使用 DeepSeek，保留 OpenAI 备用，测试使用
  确定性的 Fake。
- `trends`：读取近 7 天结构化历史并生成有证据的 `Signal`。
- `briefing`：把事件和信号组织为数量受限的固定结构 `DailyBrief`。
- `storage`：用 JSON 原子写入原始元数据、事件、信号、快照和幂等状态。
- `publishing`：用 Jinja2 从 JSON 构建首页、归档页和单日页。
- `notifications`：只发送短摘要；状态写入 `data/state/` 防止重复发送。
- `pipeline`：顺序编排以上模块，并隔离单个外部来源故障。
- `cli`：提供面向用户和 GitHub Actions 的稳定入口。

## 关键边界

- 配置文件负责“观察什么”，业务代码负责“怎样处理”。
- 所有时间在内部为带时区对象，对用户统一展示 `Asia/Singapore`。
- 外部适配器统一设置超时、有限重试和 User-Agent。
- AI 返回值先做结构校验，并校验所有 URL 都属于输入证据。
- 去重后的候选在首次 AI 调用前按来源可信度、配置优先级和时间确定性排序，并同时受
  `maximum_ai_items` 与逻辑调用预算约束。相似标题只在共享强实体、发布动作且时间接近时
  进入同一候选组，最终是否同一事件仍由 AI 判断。
- 新闻使用严格小时窗口；带 `latest_market_trading_day` 标记的市场项可保留正常长周末内
  的最近交易日，稳定交易日身份不会重复制造市场趋势。
- JSON 是 v0.1 的持久化格式；数据量和单用户运行方式不需要 SQLite。
- Fixture 路径不访问网络、不调用真实 AI、不发送通知。

## 失败与降级

每个采集器独立捕获和记录错误；成功来源继续进入流水线。AI 格式错误重试一次。分类
失败时不伪造 relevance/importance，安全地生成空候选；单个 merge/score 失败只跳过该
候选；brief 失败时仅用已验证 Story 的标题、事实和来源生成带明确降级说明的合法简版；
direction observation 失败则省略。所有降级都会写 warning/error，brief/direction 降级也
记录在 `DailyBrief.run_stats`。配置错误、预算耗尽和业务校验错误仍会失败。

`maximum_ai_calls` 保持“逻辑 AI 操作”语义；`network_ai_requests` 在每次真实 HTTP 请求
前增加，因此包含网络及 structured-output 重试。缺少 WxPusher 配置只跳过通知，不影响
存储和建站。DeepSeek 输出仍需通过 Pydantic 和来源 URL 校验才能进入业务流程。

生产 workflow 先提交晨报并成功部署 Pages，再创建逐 UID WxPusher 发送任务；只有顶层
请求和全部 UID 任务均成功时才写 `sent`，随后单独提交状态。workflow 没有 push 触发器，
因此状态提交不会递归运行流水线。
