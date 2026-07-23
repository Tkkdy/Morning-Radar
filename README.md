# Morning Radar

Morning Radar 是一个个人定时情报晨报项目，聚合 AI 科技、重点公司市场数据、
GitHub 项目动态和开发者社区信号。它强调少而重要、事实可追溯、同一事件只出现一次，
并明确区分事实、分析与不确定性。

## v0.1 目标

v0.1 将提供：

- RSS/Atom、GitHub、Hacker News 和少量重点公司市场数据采集；
- URL 与标题去重、轻量事件聚类、评分和可解释趋势信号；
- 单一 OpenAI Provider 的结构化分析，Fixture 模式不调用真实 AI；
- 固定结构中文晨报、响应式静态站点和历史归档；
- WxPusher 摘要通知、GitHub Actions 定时运行和 GitHub Pages 发布。

v0.1 不提供用户系统、后台、App、全文搜索、实时监控、投资建议、多 AI 投票、
受保护平台抓取或服务器/数据库服务。

## 当前状态

项目正在按里程碑实现。可运行命令、Windows/macOS/Linux 安装步骤、部署流程、
费用控制和故障排查会在完整流水线完成后补齐。

## 目录概览

```text
config/              可编辑关注范围和运行参数
data/                结构化历史、快照与幂等状态
docs/                产品、架构、数据模型和来源规范
fixtures/            离线演示与测试数据
prompts/             可版本化的 AI 任务提示
src/morning_radar/   Python 业务代码
templates/           静态页面模板
site/                GitHub Pages 输出
tests/               单元与集成测试
```

