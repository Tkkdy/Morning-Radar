# Morning Radar v0.1 交接

代码、Fixture、测试和本地静态站点已准备好。以下事项涉及你的账号、密钥或远程仓库，
必须由你手动完成。

## 1. 先在本地验收

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest
python -m ruff check src tests
python -m morning_radar run --fixtures
```

打开 `site/index.html`，确认首页、历史页、来源链接和手机布局。Fixture 不需要任何密钥。

## 2. 创建或确认 GitHub 仓库

在 GitHub 创建仓库后，由你确认目标地址，再执行：

```powershell
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin master
```

如果远程默认分支要求 `main`，先由你决定是否改名。项目不会自动推送到未知远程。

## 3. 配置 Actions Secrets

Settings → Secrets and variables → Actions 添加：

- `DEEPSEEK_API_KEY`：DeepSeek API Key；
- `DEEPSEEK_MODEL`：你选择且账号可用的模型名（必填，无代码默认值）；
- `DEEPSEEK_BASE_URL`：DeepSeek OpenAI-compatible API 地址，例如
  `https://api.deepseek.com`（必填，无代码默认值）；
- `WXPUSHER_APP_TOKEN`：WxPusher 应用 Token；
- `WXPUSHER_UIDS`：一个或多个 UID，用英文逗号分隔；
- `PUBLIC_SITE_URL`：例如 `https://<用户名>.github.io/<仓库名>`。

不要额外创建 GitHub PAT；Actions 使用仓库自带 `GITHUB_TOKEN`。

## 4. 开启 GitHub Pages

Settings → Pages → Build and deployment → Source 选择 **GitHub Actions**。如果未开启，
`actions/configure-pages` / `deploy-pages` 会在日志中报错。

## 5. 首次 Actions 运行

Actions → Daily Morning Radar → Run workflow：

1. 第一次勾选 `fixtures=true`，其余保持默认；
2. 检查测试、生成、提交和 Pages 部署日志；
3. 打开 Actions 输出的 Pages URL；
4. 再运行真实模式（`fixtures=false`）；
5. 检查微信只收到短摘要，而不是整篇长晨报。

定时任务每天新加坡时间 07:37 运行。`force_notify` 只用于你明确要重发同日通知时。
真实模式默认使用 `DeepSeekProvider`；`OpenAIProvider` 仅作为备用代码保留，不需要配置
OpenAI Secret。

## 6. 调整观察清单

- 主题：`config/topics.yaml`
- RSS/HN：`config/sources.yaml`
- 公司：`config/companies.yaml`
- GitHub：`config/repositories.yaml`
- 开发者 Feed：`config/people.yaml`
- 阈值和成本上限：`config/app.yaml`

新增真实来源前，按 `docs/SOURCE_GUIDE.md` 增加 Fixture 和测试。

## 7. 安全回滚

先在 GitHub Actions 暂停工作流或临时禁用 schedule，再定位最后一个正常提交：

```powershell
git log --oneline
git revert <有问题的提交哈希>
git push
```

优先使用 `git revert` 保留审计历史，不使用 `git reset --hard`。若只需停止微信推送，
删除/撤销 WxPusher Secret 或 Token；网页生成仍可继续。若密钥疑似泄露，立即在对应平台
吊销并重新创建，不要只从 Git 历史删除文本。
