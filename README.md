# Morning Radar

Morning Radar 是一个个人定时情报晨报：聚合 AI 科技、重点公司市场数据、GitHub 项目动态
和开发者社区信号，去重合并后生成可追溯的中文晨报、静态网站与微信摘要。

它强调“少而重要”：同一事件只出现一次，事实、分析和不确定性分开，所有链接来自真实
采集输入。市场变化仅作信息展示，不提供投资建议。

## v0.4 Editorial Layer（Shadow）

项目现在在完整 Story 批次形成后、最终简报生成前运行可移植的 VDVXDV Editorial Layer，
分别记录读者 placement/treatment 与后台 evidence value。默认 `enabled: true` 且
`shadow_mode: true`：每天生成 `data/editorial/YYYY-MM-DD.json`，但不改变读者简报。

Editorial 超过 Story 安全上限、Provider 失败、结构校验失败、缺少决策或 SUPPORT 关系非法
时，整批标记 degraded，并完整沿用旧 relevance/importance 排序路径。它不会截断完整 Story
批次，也不会影响 Story、趋势或 Tendency 的保存和计算。Active mode 只有在单独的真实
DeepSeek held-out Eval 达标后才应人工开启。

真实 held-out Eval 只通过 GitHub Actions 的 `Editorial Held-out Eval` 手动工作流运行。
它从 GitHub Secret 读取 `DEEPSEEK_API_KEY`，优先从 Repository Variables 读取模型与
Base URL（兼容现有同名 Secrets），只执行冻结 Prompt 的独立评估入口，不运行正式 Pipeline、
发布或通知。每次运行上传 `raw_model_output.json`、`validated_results.json` 和
`metrics.json`；单次结果用于报告，不能自动修改 Prompt 或开启 Active mode。

## v0.1 能做什么

- 采集 RSS/Atom、GitHub Releases/仓库指标、Hacker News 和配置内公司行情；
- 新闻严格筛选过去 24 小时；市场项保留最近有效交易日快照，再规范化 URL/标题、保守
  聚类并结构化评分；
- 生产默认用 `DeepSeekProvider` 生成摘要，保留 `OpenAIProvider` 备用，Fixture 使用离线
  `FakeAIProvider`；
- 保存至少 7 天可累积 JSON 历史，检测有证据的主题、公司、GitHub、产品和市场信号；
- 构建响应式首页、历史页和单日页；
- 通过 WxPusher 发送短摘要，并保证同日幂等；
- 通过 GitHub Actions 每天新加坡时间 07:37 运行并部署 GitHub Pages。

v0.1 不做用户系统、后台、App、搜索、实时监控、全市场扫描、价格预测、多 AI 投票、
受保护平台抓取、服务器或数据库服务。完整边界见 [产品文档](docs/PRODUCT.md)。

## 目录

```text
config/              关注范围和运行阈值（YAML）
data/                原始元数据、Story、Signal、快照、晨报与幂等状态
docs/                产品、架构、数据模型、来源规范、Roadmap
fixtures/            完全离线的演示和测试输入
prompts/             五类可版本化 AI 任务提示
src/morning_radar/   采集、处理、AI、趋势、建站、通知和 CLI
templates/           Jinja2 页面模板
site/                GitHub Pages 输出
tests/               单元与集成测试
```

## 本地安装

需要 Python 3.12 和 Git。不要把真实 `.env` 提交到仓库。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m morning_radar run --fixtures
```

macOS/Linux：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m morning_radar run --fixtures
```

Fixture 不访问网络、不调用真实 AI、不发送微信。完成后打开 `site/index.html`。验证项目：

```powershell
python -m pytest
python -m ruff check src tests
```

## 真实运行

复制 `.env.example` 中的变量名到你自己的安全环境配置，但不要创建含真实秘密的提交。
PowerShell 当前会话示例：

```powershell
$env:DEEPSEEK_API_KEY = "你的密钥"
$env:DEEPSEEK_MODEL = "你选择且账号可用的模型名"
$env:DEEPSEEK_BASE_URL = "https://api.deepseek.com"
$env:GITHUB_TOKEN = "可选；本地提高 GitHub API 限额"
$env:WXPUSHER_APP_TOKEN = "可选"
$env:WXPUSHER_UIDS = "UID_1,UID_2"
$env:PUBLIC_SITE_URL = "https://你的用户名.github.io/仓库名"
python -m morning_radar run
```

三个 DeepSeek 变量都没有代码内隐式默认值，避免项目在不知情时改变模型、成本或 API
端点。生产运行必须提供 DeepSeek Key、Model 和 Base URL；WxPusher 缺配置时只跳过通知。
`OpenAIProvider` 仅作为备用实现保留，不是当前流水线默认值。其他命令：

```powershell
python -m morning_radar run --dry-run
python -m morning_radar build-site
python -m morning_radar collect
python -m morning_radar test-notification
```

Dry Run 写入 `.tmp/dry-run/`，不修改生产状态或发送通知。手动重复通知必须显式加
`--force-notify`。

## GitHub Actions 与 Pages

1. 在 GitHub 创建空仓库，把本地仓库关联并由你主动推送。
2. 仓库 Settings → Secrets and variables → Actions 添加：
   `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`、`WXPUSHER_APP_TOKEN`、
   `WXPUSHER_UIDS`、`PUBLIC_SITE_URL`。工作流优先使用自带 `${{ github.token }}`，无需额外
   PAT。
3. Settings → Pages → Source 选择 **GitHub Actions**。
4. Actions → Daily Morning Radar → Run workflow。第一次建议勾选 Fixture，确认后再真实运行。

工作流支持 Fixture、Dry Run、强制通知三个手动参数；定时 Cron 为 UTC 23:37，即新加坡
次日 07:37。它先运行测试/Ruff，再生成并提交有变化的 `data/` 与 `site/`，用官方 Pages
Actions 部署成功后才发送 WxPusher，最后单独提交通知幂等状态。Fixture、Dry Run 或 Pages
部署失败都不会发送生产通知。工作流只由定时或手动事件触发，提交状态不会形成 push 循环。

## 修改观察范围

- RSS/Atom 或 HN：编辑 `config/sources.yaml`；
- 主题关键词：编辑 `config/topics.yaml`；
- 公司：编辑 `config/companies.yaml`，同时提供真实 `source_url`；
- GitHub 项目：编辑 `config/repositories.yaml` 的 owner/repo；
- 开发者公开 Feed：编辑 `config/people.yaml`（v0.1 仅提供结构，启用前应配 Fixture/测试）。

新增来源必须符合 [来源指南](docs/SOURCE_GUIDE.md)：公开、稳定、无需登录，并同时增加
Fixture 和无网络测试。核心代码不应因新增观察对象而修改。

## 费用、隐私与故障排查

AI 调用受 `config/app.yaml` 的候选数、输入字符数和每日逻辑调用数限制。候选会在首次
AI 分类前确定性排序和截断；`maximum_ai_calls` 统计逻辑任务，真实 HTTP 请求（含重试）
另行记录。`relevance_threshold` 控制晨报候选，`importance_threshold` 控制头条资格。
这两个阈值仍是 Shadow/degraded 时的兼容路径；Active Editorial mode 使用 placement 决定
读者候选。
可恢复 AI 输出失败会明确记录降级，只复用已验证的 Story 事实和来源，不生成替代判断。
先用 Fixture 验证，再逐步增加来源；GitHub Pages 默认公开，晨报不得放个人信息或秘密。

- `DEEPSEEK_MODEL is required`：设置模型名；项目不会替你选择或硬编码。
- `DEEPSEEK_BASE_URL is required`：确认本地环境或 Actions Secret 已配置兼容 API 地址。
- GitHub 403/限流：确认 `GITHUB_TOKEN`；单仓库失败不会阻断其他来源。
- 没有微信：检查三项 WxPusher 变量，运行 `test-notification`，不要在日志粘贴 Token。
- Pages 失败：确认 Source 为 GitHub Actions，并检查 workflow 的 Pages 权限。
- 页面为空：查看 Actions 的采集/AI 阶段日志和 `data/raw/`；证据不足时空栏目是预期行为。
- 重建页面：运行 `python -m morning_radar build-site`。

完整架构见 [ARCHITECTURE.md](docs/ARCHITECTURE.md)，最终部署步骤集中在
[HANDOFF.md](HANDOFF.md)。
