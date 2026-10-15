# Morning Radar D：Tendency 页面与受控输入交付

状态：正式代码、隔离端到端与本地浏览器演示完成。生产端到端 NOT TESTED；未部署。桌面与窄屏浏览器检查已完成，工具差异及证据见下文。

## 基线与保留范围

- 实际基线：`f67f5a2d07a0f3e8412f0e4f408e18ea7c4c4404`，C 收尾提交；开工时隔离工作区干净。
- 已确认 A `bb3ef351671bc5fcd54eed789e5c3c4306db5be0`、A 复核 `e507d1041cdabfbce1ea3191f9ae8883a7cf4424`、B1 `b7a48efcd67d5afb0672f95612418a9661666207` 均为祖先。未退回 master。
- D 分支：`codex/radar-next-release-tendency-web`。
- Worktree：`<LOCAL_HOME>/.codex/worktrees/radar-next-release-batch-a/Morning Radar v0.1`。
- D 实施提交：`cdd2da02ae92e147036c767cb4d0584d19c3f1aa`；最终 HEAD 为其后的本报告归档提交，完整 SHA 在交付消息中给出。归档未改变实现代码或测试边界。
- 已读产品基线、C/D 任务书、C 完整报告及有效工程规则。只检查接入所需的投递、回执、版本、证据与导出接口。

## 写入路径与部署边界

| 环节 | 本机演示 | 正式部署适配（代码已实现，未上线） |
| --- | --- | --- |
| 阅读 | loopback 服务生成 Jinja 页面 | 同样模板生成静态 Pages 构建物 |
| 编辑身份 | 启动者持有本次运行随机口令，换取一小时 HttpOnly / SameSite=Strict 会话 | GitHub 登录；摄取端从 GraphQL author/editor 核对配置白名单 |
| 首先保存 | C 事务中的 web_input 原文、操作身份和接收结果 | GitHub Issue 持久正文；这一步不冒充 C 已接收 |
| C 接收 | 实际文件锁内核对期望版本，追加事务；再调用原 submit/receipt | 有界只读 poll → 验身份 → 快照所取得正文、来源 ID、内容指纹和取得时间 → 同一 accept_input |
| 后续运行 | 独立 run_core 读取相同事务目录，自动恢复中断的接收 | 干净运行环境直接从 GitHub 持久入口补扫；恢复已归档 C 历史后再摄取和运行 |
| 回执 | 正式事务派生，刷新和重启可查 | 构建后在回执页按 Issue 号和提交 ID 查；摄取前仅能确认原 Issue 已保存 |
| 更新页面 | GET 重新构建本地视图 | 构建/发布为单独阶段；显示构建版本，未有发布回执时一直写“发布未确认” |

选择 GitHub 原生入口，是因为现有部署为静态 Pages，不新增服务账号、数据库或日常手写 JSON 工作流。本机 HTTPServer 是 loopback 开发适配，**不是已部署的云端写入服务**。本机 `.tmp` 的文件不会自动出现在 Actions；正式输入源是 GitHub，独立环境必须运行已实现的摄取命令。

GitHub 适配只发固定 GraphQL 查询，未实现任何远端 mutation。超时 20 秒、连接/超时失败最多两次尝试，每次最多两页、每页 25 个 Issue，响应流上限 2 MB。保留分页游标；扫完一轮重回起点，包括关闭及编辑后的 Issue，不依赖一次事件。若接口或大小限制失败，游标不跨过未接收页，下一轮仍可恢复；持续超限需明确调整读取窗口，不会静默略过。

可信作者/最后编辑者必须都获授权；标签、标题、正文署名不授予权限。操作身份按仓库+来源 ID+正文版本生成，同版本变内容冲突；后续编辑取得新版本，已接收原文不改写。第一次摄取之前被编辑掉的内容无法还原，不宣称保存了未取得的版本。授权但格式错误的正文进入 inbox_rejected 事务，回执列表可展开恢复。

## 实际修改

- `web_input.py`：严格 Pydantic 输入字段，支持自由文本及明确“一行一条”，新假设/个人观察/外部材料/纠偏分别传递。可选链接作为 user_hypothesis 线索进入 C，不能升级为事实。新材料不在保存时抓取网页。
- `core_store.py`：实际写锁内执行编辑期望版本检查，冲突与原始稿件一起持久保存；旧版本不能覆盖新版本。同一操作重试幂等，不以相同正文或业务日期去重。
- `web_input.recover_inputs` + `core.run_core`：进程若在保存 web_input 后、C submission/receipt 完成前退出，下一轮独立运行最多恢复 100 项，剩余项使运行保持 partial。不存在必须依赖浏览器内存才能恢复的窗口。
- `github_input.py` / `config/tendency_web.yaml` / Issue Form：正式可部署的持久输入适配与所有者白名单。测试用保存的 synthetic API 样本和 MockTransport，未读取真实凭据或真实 Issue。
- `web_pages.py` / `templates/tendency/`：独立列表、轻筛选、当前详情、稳定修订页、完整当时证据快照、回执与编辑页。区分首次记录、实质修订、检查尝试和构建时间。Early、活跃、归档、推翻分别可见；无判断、待拆分、部分完成、失败、读取错误与 legacy 状态不混写。
- 详情展示旧约束、条件观察/推断、共同机制、缺口、其他解释、改变判断的观察；证据显示来源性质、材料版本、事件身份及具体条件引用。历史页不以新证据替换旧依据，用户纠偏与外部更新/分析重评估分开。
- `web_server.py`：只绑定 `127.0.0.1`；Host/Origin、会话、CSRF、请求体大小、字段和路径边界在服务端执行。口令只在工作目录的私有运行文件，不放 HTML/JS/URL/localStorage。禁用请求日志，公开异常使用固定文案，CSP 与转义防止原文成为脚本。
- `web_cli.py` / `cli.py`：build、serve、ingest-github 正式入口，早于日常 pipeline 实例化；CLI 输出路径拒绝生产 data/site 和其祖先。
- `SiteBuilder.build(tendency_workspace=...)`：显式可选接线及晨报双向导航。旧调用行为、status.json 消费者和生产流水线保持兼容，B2 再开启统一构建。

### C 接口局部修复

确认缺陷：C `core_validation.materialize` 原先直接用本次 decision 的 parent 字段创建后续 Revision。分支之后的普通修订要求 decision 不带分支字段，导致父谱系丢失；若仅保留父 ID，旧 store 又会错误要求父命题仍停在最初版本。

修复：后续修订继承已保存父谱系，写锁拒绝更改谱系；只有新分支检查父命题的当前版本。回归覆盖“创建分支 → 父命题前进 → 子命题重评估”，原父版本链接仍可读。没有回填或改写旧记录。

## 浏览器演示与材料性质

全部为 **synthetic**：沿用 C 港区试验预制轨迹，`SyntheticEditorProvider` 是明确的演示响应，不代表真实模型发现或判断质量。没有真实新闻材料或已保存真实模型输出混入本次 D 演示。

本地页面：`http://127.0.0.1:8768/tendency/index.html`。

隔离数据 `.tmp/d-web/demo/`，独立静态副本 `.tmp/d-web/static/tendency/index.html`。可直接用浏览器打开静态 HTML，服务关闭后仍可读；其编辑页没有可提交表单，并明确说明不可写。

实际操作轨迹：

1. 浏览器输入一段含两个判断的长中文自由文本，保存为 `web-3854d3ce879c0111fcf81907`；先看到“C 已接收/等待拆分”。
2. 独立 Python 进程调用正式 C runner + synthetic 响应，第一轮拆分，第二轮形成两个独立假设；刷新真实回执显示两个 seed 及关联判断。
3. 两个页面同时打开同一修订 `rev-81d528eaf28341f6a7ad29a9de144abb`。第一页投递纠偏 `web-03dab10882c635217350cfce`。受既有字符预算限制，首轮目标延期，下一轮生成用户纠偏版本 `rev-8db3b5e0aacc437fb11c0d39c6a4db43`；并未提高预算或把延期报成采纳。
4. 第二页使用原期望版本提交，服务实际返回 409；冲突稿 `web-11bcf369247d73e060f145ff` 持久保留，未进入核心修订队列。
5. 在新版上追加个人观察，仍保留个人适用范围与待处理状态。没有把“我观察到”改成普遍事实。
6. 停止原服务并启动新进程，重新打开首份回执，原文/seed/关联仍在；打开最初 Early `rev-06ad5d35d21143d1b249db37ffe0ea86`，仍是甲乙范围和当时两份证据，不含后来反例。

截图：`.tmp/d-web/browser/multi-receipt.jpg`、`conflict.jpg`、`original-after-restart.jpg`、`mobile.jpg`、`desktop.jpg`。运行证据：`browser-process-1/2.json`、`browser-correction-process.json`、`browser-correction-recovery.json`。最终静态构建：3 个当前假设、6 个修订、4 份网页投递/冲突回执。

浏览器窄屏无横向溢出（工具设置 390px，页面实际报告 innerWidth 372 / documentWidth 357）；中文输入、键盘进入链接/展开依据、回执、冲突和重启已实际检查。可见侧栏页接受 1280px 设置后仍报告 372px；随后使用同一浏览器的独立后台页，实际报告 innerWidth 1280 / documentWidth 1265，已检查双列桌面布局、无横向溢出，Early 筛选正确显示两个条目，并保存 desktop.jpg。冲突回执的桌面截图也已检查。最终已恢复临时视口设置、关闭临时页。正常页面浏览器错误/警告日志为空。

## 本地使用

在上文隔离 worktree 的 PowerShell 中：

```powershell
$radarPython = '<LOCAL_HOME>/Documents/Morning Radar v0.1/.venv/Scripts/python.exe'
function Invoke-RadarLocal {
    & $radarPython -X utf8 -B -c 'import sys; sys.path.insert(0,"src"); from morning_radar.cli import main; raise SystemExit(main(sys.argv[1:]))' @args
}
# 当前 8768 演示服务已启动，不重复占用端口；重启或开另一隔离空间时使用：
Invoke-RadarLocal tendency-web serve --workspace .tmp/d-web/demo --port 8768 --synthetic
```

进入“投递想法 → 授权本机写入”，启动者从 `.tmp/d-web/demo/.local-write-passphrase` 读取本次随机口令。该文件不可发布；每次重启口令和会话失效，但投递及历史仍保留。前台运行可用 Ctrl+C 退出；本次后台进程 ID 存在 `.tmp/d-web/server.pid`，停止前核实其命令行确为本次 `.tmp/d-web/serve.py`，勿按过期 PID 停止其他进程。

编辑可粘贴多条自由文本，或选每行一个判断；保存后点击回执。保存不自动调用模型。详情“追加材料 / 纠偏”自动带入版本；冲突回执保留原稿，打开最新详情核对后重新投递，不能强制覆盖。

默认 Fake 核心处理不会捏造发现结果：

```powershell
Invoke-RadarLocal tendency-core run --workspace .tmp/d-web/demo --evidence-root .tmp/d-web/demo --provider fake
Invoke-RadarLocal tendency-web build --workspace .tmp/d-web/demo --output .tmp/d-web/static/tendency --synthetic
```

若仅继续本报告的 synthetic 流程，可显式调用 `web_demo.process_demo(Path('.tmp/d-web/demo'), Path.cwd())`；该函数拒绝已保存的非 synthetic 判断或投递。自由文本先拆分，下一轮再处理 seed；它不是生产 Provider。

正式 GitHub 摄取命令（**本次没有运行真实网络版本**）：

```powershell
Invoke-RadarLocal tendency-web ingest-github --workspace <隔离运行目录> --config config/tendency_web.yaml
```

可信运行环境须已有只读 GitHub API 凭据供进程读取 `GITHUB_TOKEN`；本批没有创建或修改 Secrets。通过 `--github-entry` 构建时生成配置仓库的原生表单跳转；不在页面放 Token。不要拿本机 `.tmp` 路径冒充云端输入。

## 实际验证

测试先检查写入行为，pytest 拒绝真实 socket.connect/create_connection，外部 HTTP 用 MockTransport；输出均在 `.tmp/d-web/`，Python -B、关闭 pytest cache。未安装依赖。

| 检查 | 结果 |
| --- | --- |
| 修改前 C+站点基线 | 50 passed，6.38s |
| D/C 首轮 | 57 passed，1 failed、1 setup error；测试固定时钟造成最近运行排序断言不稳定，另有一次 Windows 临时 replace PermissionError |
| 修正时钟并重跑相关范围 | 73 passed，16.59s；Windows 异常未再出现，未据此改动全局文件写入机制 |
| 首轮完整 pytest | 717 passed，74.01s |
| 新增接收中断恢复后相关范围 | 62 passed，15.62s |
| **最终完整 pytest** | **718 passed，75.09s**；`full-final.log` / `full-final.xml` |
| Ruff / git diff --check | passed |
| 浏览器实际回执/版本冲突/重启/原始证据 | passed，synthetic；包括实际 1280px 桌面与约 372px 窄屏 |

最终完整命令等价于 `python -X utf8 -B -m pytest -o addopts= -q -p no:cacheprovider --basetemp=.tmp/d-web/full-final --junitxml=.tmp/d-web/full-final.xml`，由包装脚本在 pytest 启动前拒绝外部 socket。Ruff 为 `python -X utf8 -B -m ruff check --no-cache src tests`。

覆盖正式页面/适配/C runner，含身份伪造、CSRF、跨站、未知字段、可执行/内网 URL、路径越界、恶意 HTML、同操作重试/冲突、不同有意投递、原文先落盘、拆分部分成功/失败、独立进程读取、假 GitHub 持久源补扫/编辑快照、C 修订、旧页迟到和分支谱系。测试总数不证明真实模型解释或选题质量。

`.tmp/d-web/protection-verification.json` 六项均 true：原 HEAD/status/diff/已修改文件字节、原工作区生产 data/site、隔离 worktree 生产 data/site。原未跟踪资料、未提交改动与本地提交没有迁移或覆盖。

## 配置与 workflow 影响

- 新增 `config/tendency_web.yaml`：仓库 `Tkkdy/Morning-Radar`，授权名单 `[Tkkdy]`，每次最多两页；没有 Token、生产模型、价格、通知或发布时间配置。
- 新增 `.github/ISSUE_TEMPLATE/tendency.yml`：原生公开输入表单；本地未推送，因此远端尚不可用。
- 新增模板/CSS/JS，晨报导航通过可选参数开启。
- **没有新增或修改 workflow**，没有接管生产时间表、部署和通知。独立摄取接口已可部署调用；统一调度和共享历史归档由 B2 接线。

## 未验证、部署条件与 B2

未验证真实 GitHub 登录、权限/凭据、远端 Issue Form、GraphQL 实际仓库响应、远端并发提交和部署；本批禁止真实远端写入，因此没有创建 Issue/评论或触发 Actions。官方 API 字段核对依据：[GitHub Issues GraphQL](https://docs.github.com/en/graphql/reference/issues)；Issue Form 正文可编辑，因此使用本地取得版本快照：[GitHub Issue Form 说明](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)。

未验证真实模型发现/解释/语义去重、实时来源补证、真实费用和全项目每日 0.4 元预算。Fake 调用不证明这些指标。未验证硬件断电、网络文件系统；一次 Windows replace 异常作为已观察环境风险记录，不隐瞒。

B2 必须继续完成：

1. 获批后把 Issue Form 与入口配置带到正式分支，启用只读 GitHub 摄取；直接调用现有 `poll_github` / `accept_input`，无需另造投递格式或授权模型。
2. 每次干净运行恢复 C 的 transactions、runs、GitHub 游标，再摄取并运行 C。编辑摄取和定时运行复用 `morning-radar-generated-data` 互斥组；归档新的 `data/tendency-core/v2/`，不只保存派生页面。Git 冲突时停止该提交，取得最新历史后重放幂等操作；不 force push、不覆盖远端新历史。
3. 继续接入独立证据及 B1 有界补证。不得让 Tendency 永远只重读旧材料，也不能以最终晨报是否入选过滤证据。
4. 将发现、校准、投递恢复、页面构建与健康/费用汇总接到同一期版本。调用 SiteBuilder 的显式入口或 build_pages，并保留稳定假设/修订链接。
5. 统一发布回执关联构建版本，正常版本完成后通知一次；partial 与无变化分开。D 页面目前不伪造发布成功。
6. 另行获批后做限定费用的真实模型、来源、远端授权及端到端验证；保留桌面与移动端回归。

回滚：本批只保存本地提交，尚未集成。需要回滚代码时用新 revert 提交或停止使用 D 分支；不删除已产生的合法 transactions、原文、回执和历史。没有数据库迁移，不需要重写旧判断。
