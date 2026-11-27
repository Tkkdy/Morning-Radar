# Morning Radar B2：统一发布与跨 Runner 恢复

状态：正式协调代码与隔离验证完成；真实发布、模型质量及真实费用尚未验证。本批没有真实远端 push、合并、Actions、部署、通知、Issue 写入、Secrets 修改或付费调用。

## 基线

- 基线 d15220a6b9cb20041f5c3aa896e32d25d5f676ac（D 最终归档），开工干净。
- 已验证 A bb3ef351、A 复核 e507d104、B1 b7a48efc、C f67f5a2d、D cdd2da02/d15220a6 祖先关系。
- 分支 codex/radar-next-release-b2-integration。
- Worktree C:/Users/PsyDuck/.codex/worktrees/radar-next-release-batch-a/Morning Radar v0.1。
- 实施提交 a482cc573d1172a26e412fec091201a985c90898；之后交接文档提交的完整 SHA 见交付消息。
- 依据原工作目录 10-10 中 PRODUCT_BASELINE、B2 任务书及 D 完整报告；只核查本批所需接口。

## 工作流与持久化

1. Tendency Inbox Archive 由 Issue opened/edited/reopened/closed 或 workflow_dispatch 触发，只运行 D 身份校验与有界补扫，不调用模型、不部署、不通知。
2. Daily 排队后 checkout 最新 master；补扫并归档原文，再固定业务日/时区/截稿/代码/config/投递版本，正式 collect 并归档 checkpoint。
3. 正式 Daily/B1 与快速连续性 → 独立 C 发现/校准 → 必要深度连续性。自由文本拆分成功后至多再运行一次 C；不是平行推理核心。
4. 合法 C 事务逐笔 Git 归档；后续模型失败不撤销前面修订。
5. 冻结整期页面、清单和哈希，预检本地链接及 Pages 基路径，Git 归档 → 导出原包 → 持久上传意图 → Artifact → 持久部署意图 → Pages API → 真实 ID 查询 + 逐文件公开字节校验 → 归档确认 → 持久通知意图 → 发送 → 归档结果。
6. 原 Post-Daily 仅手动、只读 release status，停止旧 Tendency v1、A/B、生成和再部署；Daily 是唯一正常发布/通知所有者。不依赖 GITHUB_TOKEN push 触发另一条工作流。
7. Daily 与 Inbox 共用 morning-radar-generated-data 队列，cancel-in-progress=false、queue=max，避免嵌套工作流死锁。最多 100 个 pending，队列不是持久输入通道；补扫处理漏事件，不能恢复摄取前已被覆盖的中间编辑。
8. 固定代码 ref=master；不把 Issue 正文、标题插入 shell。GitArchive 只普通 push；非快进拒绝时保留本地提交、报 ArchiveConflict，不 force、不自动 rebase/覆盖派生 JSON。新 Runner 从权威历史与 GitHub 原文重放；若失败 Runner 被销毁，不能假称其孤立提交已在远端保存。

| 正式目录 | 内容 |
| --- | --- |
| data/tendency-core/v2/transactions | 原 C 投递、回执、修订、谱系 |
| data/tendency-core/v2/runs、github-cursor.json | 处理状态、补扫游标 |
| data/intake、raw、snapshots、state | checkpoint、候选命运、兼容状态 |
| data/evidence | B1 证据及事件关系，独立于是否选入晨报 |
| data/briefs、stories、continuity、tendencies、radar_signals | 既有产物与兼容历史 |
| data/usage、health | 物理调用、独立模块健康 |
| data/releases/v1/runs | 上下文、阶段、上传/部署状态 |
| data/releases/v1/packages/<release_id> | manifest.json 与冻结 site |
| data/releases/v1/notifications/<day>.json | 正常通知每接收方哈希状态 |
| data/releases/v1/resends | 明确授权编号下的重发状态 |

Git 文件白名单仅上述目录内 JSON/HTML/CSS/JS。不 git add . 或 git add data site，不提交锁、临时目录、口令、环境或虚拟环境；正常生产归档递归拒绝 synthetic=true。health/evidence/usage Artifact 是额外故障证据，不能替代正式 Git 持久层。

## 冻结、状态与局部修复

清单包含 release_id、业务日/时区、cutoff、代码 SHA、config hash、attempt、输入批次、投递版本、Brief 内容/兼容 artifact hash、C 修订、回执、证据/事务/健康索引、费用、完整性、上一有效发布、逐文件 SHA256 与 package_hash。export 只验证复制原包，不再采集或推理。新增 .gitattributes 仅关闭冻结包换行转换，保障跨 OS 字节一致。

CutoffStore 是 C 原事务的只读过滤视图，不是新写入存储；截稿后输入不进入本期 C 判断及公开原文。后续包保留上一有效包的历史 URL；真实历史仍取不可变修订及当时证据。缺少当日新闻不阻止 C；旧内容不冒充今日完成分析。no_input/no_changes/insufficient_evidence 与 failed/not_run/partial 区分；status.json schema 和 SUCCESS/FAILED 枚举不变，详细状态在独立健康及清单。

部署确认要求真实 Pages ID、API succeed、公开包所有文件哈希一致。上传、构建、部署分别记录；未知创建响应不猜 ID、不自动再次 POST。包不会为写“已部署”而改变再发。A publish/ledger 只更新同一个 artifact hash，旧包不会把同日新稿误标已部署。

本批修复的具体接口问题：

- C 原读取 checkpoint.records，而正式采集保存 items；现兼容正式字段与旧夹具，正式 checkpoint 的端到端扫描断言无错误。
- GitHub 旧来源版本持久标记 stale_source；同版本正文冲突保留快照。逐对象异常不妨碍同页其他对象落盘；游标不越过失败页。
- C 增加成功事务归档钩子；已有事务格式与 CAS 不变。
- 深度连续性阶段取真实 health，不能把内部捕获的失败误报 completed。
- 等待有超时限制的快速连续性请求结束再冻结，避免后台继续改 usage；代价是超时路径可能等待底层剩余超时，仍受工作流 45 分钟限制。
- 旧生产 CLI 在已产生协调发布记录的目录提示改走 release；旧数据工具和 fixture/dry-run 兼容保留。

## 预算及通知

config/release.yaml：每日 0.4 CNY，单次最少预留 0.04，物理请求最多 60；Daily/Shadow 前保护 Tendency 0.08 和后续连续性 0.04，进入相应阶段释放。A 的正文及快速连续性预留继续使用。

call_id 去重仅适用于同一物理记录；真实重试、重跑、恢复新增调用累计。开始前按输出 cap 与保守输入字节估算预留，意图先归档才发请求；本进程活跃预留可并存，旧 Runner 遗留 started 为未知，不因重启清零。缺 usage/价格/必要缓存信息 unavailable，已知部分费用不等于总额；实际费用超过预留照实记录并阻止后续。不承诺供应商计费上限或已经证明每日 0.4 元内有合格内容。

price=null 是刻意保留的未核实状态：transport 前拒绝新的收费请求。真实验证需核实实际生产 model 及官方价格，在隔离配置填匹配的 PriceQuote、来源与日期。本批未变模型、Secrets，不猜价格。

正常通知按业务日 + 接收方哈希幂等，不随 release_id 改变解锁重发。confirmed 才可发；sent/unknown 不自动重发，仅明确 failed 可重试。意图先以 unknown 归档，再发送单接收方请求。sendRecordId 表示建立发送任务，不证明设备收到或已读。超时/响应归档中断保持 unknown；未接通供应商未知请求查询/幂等键，不能保证跨崩溃 exactly-once。

人工 action=resend 必填 authorization_id，明确接受可能重复的风险；同授权编号内仍幂等，与日常键分开。定时及普通恢复不启用它。action=rollback 只重新部署指定旧冻结包，保留部署记录，不推理、不删新 C 历史、不通知。

模型中断后原 context 留存，新 generate 从正式历史继续，未知计费先对账；相同未完成 ID 拒绝重新推理。已冻结的同 ID 返回原包。部署/通知恢复不重跑晨报与 C。

## 操作及预览

当前浏览器 http://127.0.0.1:8770/；Tendency /tendency/index.html，导航含历史、回执和原文。

- 演示根 .tmp/b2/demo-delivery；页面 .tmp/preview/index.html。
- 清单 .tmp/b2/demo-delivery/data/releases/v1/packages/synthetic-release-1/manifest.json。
- 截图 .tmp/b2/preview-final.png。
- 全部材料 synthetic：sample_items 和人工港区试验材料；GitHub/模型是外部假响应。没有真实生产文章回放或真实模型能力验收。
- 本期保留一个补证缺口，overall partial、Daily partial、C completed，不能包装为质量全部通过。
- 演示包在最后预算保护补丁前生成；该补丁只影响真实收费准入，Fake 演示无收费。清单中的 SHA 是演示临时仓库种子提交，不冒充生产代码构建。
- 后台仅监听 127.0.0.1，启动文件 .tmp/b2/serve-delivery.py、PID .tmp/b2/server-delivery.pid。B1/C/D 预览未覆盖。

本 worktree 使用现有 Python 时先在脚本 sys.path.insert(0,"src")，避免原目录 editable install。然后调用：
    from morning_radar.release.demo import demo
    demo(Path.cwd(), Path(".tmp/b2/another-demo").resolve())
输出目录必须全新。普通已正确安装 B2 的环境可直接：
    python -m morning_radar release demo --output .tmp/b2/another-demo
已有包可用：
    python -m http.server 8770 --bind 127.0.0.1 --directory .tmp/b2/demo-delivery/.tmp/preview

未来授权后 GitHub 手动 Daily 的动作：
generate；deploy + release_id；reconcile + release_id（必要时真实 deployment_id）；notify + release_id；resend + release_id + authorization_id；rollback + release_id。
都是正式代码；本轮没有执行真实动作。

## 验证证据

- D 基线复用已通过 718 项的证据，不为形式重跑未改基线。
- 目标首轮 6 passed；B2+C/D+workflow 80 passed。
- 全量首轮 723 passed / 2 failed：旧诊断 Artifact 断言；补回 Daily Artifact、移除 Post-Daily 已不生产后的过时期待。
- 后续 724 passed / 1 failed：恢复入口改为动态 deploy/rollback 命令时旧 workflow 断言未同步；已修正。
- 完整 pytest 726 passed，156.64s；.tmp/b2/full-delivery.log/xml。边界包含部署投影、人工重发及回滚。
- 之后补跨模块预算保留/释放及一项回归；收尾 B2+workflow：21 passed，99.34s，.tmp/b2/closing-regression.log/xml。两组有重叠，不相加成虚假总数。
- 随后补齐 DeepSeek/OpenAI 缓存 token/命中价格缺失必须 unavailable 的计费语义；用量、缓存、预算、A 和 Provider 定向回归 93 passed，12.58s（cost-final-2.log/xml）。此前一次误写测试文件名导致 0 tests，修正选择后通过；不是实现失败。最终代码以全量证据加上述受影响回归覆盖。
- Ruff、git diff --check 通过；日志 .tmp/b2/ruff-final.log。
- 测试 socket 真实网络封锁，数据仅在 basetemp；正式采集/晨报/C/协调器/GitArchive/HTTP 适配运行，只替换外部来源、模型、GitHub、Pages、WxPusher。
- 独立 clone 只带 Git 正式历史，不复制 .tmp、浏览器或进程状态。覆盖重复、版本冲突、逐对象失败、Git 非快进冲突、部署失败/未知/结果归档失败、通知分接收方失败/未知/成功重跑、费用重复导入与真实新请求、缺 usage、跨午夜、截稿后输入、旧历史、授权重发、冻结包回滚。
- actionlint 1.7.12 原始仅报两处 queue 新语法不支持；按官方文档确认合法，精确排除该已知限制后其余检查退出 0。不是 GitHub 实际运行通过。见 actionlint.log / actionlint-compatible.log。
- 最终 16 个工作流 Bash 块通过 bash -n，shell-validation-final.json。
- 6/6 保护检查 true：原 HEAD/status/diff/修改字节、原 production data/site、worktree production data/site，protection-verification.json。
- 浏览器检查完整晨报、C 当前视图、回执；明示 synthetic，同一 release_id，1280px 无横向溢出。

## 配置、权限与真实条件

Daily：固定 master、原 03:37 Asia/Singapore 时间不变；contents:write、issues:read、pages:write、id-token:write。Inbox 仅 contents:write/ issues:read，无模型和 Pages/OIDC 权限。Post-Daily 仅 contents:read，手动诊断。

继续使用原 DEEPSEEK_API_KEY/MODEL/BASE_URL、PUBLIC_SITE_URL、WXPUSHER_APP_TOKEN/UIDS；不新建或更改。GITHUB_TOKEN 为自动 token，不新增 PAT，不申请 Issue 写权限。新配置仅 release.yaml 与冻结包换行规则。模型、来源边界、Editorial Shadow/AIHOT 禁用不变。

真实账号需满足 bot 可普通写入分支、Pages 构建来源 Actions、github-pages 环境与固定 master OIDC 授权。未读取或更改这些真实设置，本地成功不证明云权限正确。

## 有限真实验证及准确授权

以下尚未获批，不自动执行：

1. 来源只读：授权固定配置公开来源 HTTP、GitHub 投递只读 API；零模型费用、零远端写入。在新隔离 worktree 运行 MorningRadarPipeline(root).collect()，检查覆盖、新鲜度、解析/限额、原事件时间与缺口。不动原目录/生产历史。
2. 有限收费：授权既有 DeepSeek 配置下的一次隔离 generate，不 push/部署/通知。先只读核实实际模型官方价格（含缓存），在隔离副本 release.yaml 填 PriceQuote。建议本次整轮批准上限 0.4 CNY，而非每模块各 0.4；请求上限 60、沿用任务输出 cap，缺 usage/价格、未知计费或余额不足即停。供应商账单仍需对账。
   执行入口：
       ReleaseCoordinator(isolated_root, archive=GitArchive(isolated_root)).generate(token=read_only_token)
   只使用新隔离 worktree 与本地 GitArchive，不传 remote；此调用包含真实来源和模型，应先得到上述明确授权。凭据仅读取已有安全运行配置，不改 Secrets。
3. 真实输入/发布：另行授权选定的真实测试 Issue（或用户已有投递）、远端代码提交/PR/合并、Pages 公开部署、一次指定接收方通知。先手动 Inbox 验身份/编辑/关闭补扫，再核对冻结清单→公网哈希→通知。如果只批准部署而未批准通知，不能启动默认包含通知的 generate 自动链。
4. 内容验收：重要来源覆盖、去重、论文/技术长文条件/限制/原始引用、旧事新增信息、Early 新条件、目标性反证；保留未入选证据可供 C 使用的证明。不能以人工预制输出或少数例子代替真实验收。

## 首次上线与回滚

1. 获批后 fetch 最新 master 并记 SHA，在新集成分支以远端新增生产历史为权威应用本批代码/config/test/template，不拿旧本地 data/site 覆盖。核对 C 事务、投递、usage 与发布记录都保留；冲突停止对应项，不盲目 rebase JSON。
2. 核实价格、来源、PUBLIC_SITE_URL 基路径、权限/分支/环境；完成只读和有限收费验证，再批准正式发布。
3. 同次切换 Daily/Post-Daily，避免双发布；保留定时计划，首次人工按明确授权运行。
4. 部署失败/未知不通知，原有效站点保留。用同 release_id 原包恢复；需要时从真实 GitHub 记录取得 ID reconcile，不重生成。
5. 通知只恢复 notify；unknown 先对账。重复风险只能通过独立 resend 授权承担。
6. 页面 rollback 指定上一有效 release_id，只恢复原包，保留新输入、usage、修订，不发通知。
7. 代码回滚不能简单整体 revert 到旧双工作流：先停用会重复发布的旧入口，保留 B2 归档/包读取能力，再回退业务逻辑。C/D 仍兼容原 v2 事务；旧代码忽略 releases/v1，不能负责新状态恢复。不得删新历史迎合旧代码。

## 产品覆盖及 NOT TESTED

| 范围 | 本地 | 真实待验证 |
| --- | --- | --- |
| GitHub 入口/身份/补扫/回执 | D 复用 + B2 Git 归档、冲突测试 | API 当前字段、真实作者/editor、权限和事件 |
| 晨报/连续性 | A/B1 正式链与局部失败 | 来源覆盖、中文表达质量 |
| Tendency/编辑/历史 | C/D 正式事务、发现/校准接入 | 真实模型结构发现质量 |
| 冻结/部署/通知 | 正式模块、临时 Git、假 HTTP 跨 Runner | 真实 Pages/CDN、WxPusher 设备送达 |
| 费用/预留 | synthetic 调用计量与恢复 | 供应商账单、0.4 元整轮质量达标 |
| Editorial | 保持 Shadow，未开 Active | Active 未授权 |
| AIHOT | 保持禁用 | 许可/用途/生产接入未授权 |

A/B1/C/D/B2 的本地代码链已衔接，尚未上线，不宣称 Tendency 已领先共识。下一步直接有限真实验证与上线决策；只因具体缺陷做局部修复，不另加无理由功能批次。

## 官方依据（2026-10-10 读取）

- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
- https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow
- https://docs.github.com/en/rest/pages/pages#create-a-github-pages-deployment
- https://raw.githubusercontent.com/actions/upload-pages-artifact/v3/action.yml
- https://github.com/rhysd/actionlint/releases/tag/v1.7.12
