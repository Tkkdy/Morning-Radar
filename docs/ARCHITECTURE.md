# 架构说明

Morning Radar 采用模块化单体：一个 Python 包、一个仓库、一条主要流水线。这样保留
清晰职责边界，又避免 v0.1 引入服务编排、数据库和队列的运维成本。

```text
采集 → 清洗 → 去重 → 事件合并 → 评分 → 趋势 → 晨报 → 网页 → 推送
```

## 模块职责

- `collectors`：把 RSS、GitHub、Hacker News、市场或 Fixture 转为统一 `RawItem`。
- `processing`：规范化 URL/标题、按时间窗过滤、去重、聚类与评分。
- `ai`：定义最小 Provider 接口；生产仅有 OpenAI，测试使用确定性的 Fake。
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
- JSON 是 v0.1 的持久化格式；数据量和单用户运行方式不需要 SQLite。
- Fixture 路径不访问网络、不调用真实 AI、不发送通知。

## 失败与降级

每个采集器独立捕获和记录错误；成功来源继续进入流水线。AI 格式错误重试一次，
再次失败则跳过对应分析，不使用占位事实。缺少 WxPusher 配置只跳过通知，不影响
存储和建站。生产必需的 OpenAI 配置缺失则在进入生产 AI 阶段时给出明确错误。

