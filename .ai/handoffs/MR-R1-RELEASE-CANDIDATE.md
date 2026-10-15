# MR-R1-RELEASE-CANDIDATE

状态：READY_FOR_REVIEW。主要真实验收已通过，单卡局部编辑已在独立审阅稿收尾；尚未上线。未来模型是否遵循本次提示修订尚未经新的真实调用验证，不据此重开已通过验收。

## 交付与最小修改

- 审阅稿：`.tmp/mr-r1-release-candidate/news/index.html`；技术卡：同目录 `technical/index.html`。
- 费城卡只改三处：正文止于“调查人员从未审查”；原因及标记未知明确放在 uncertainty；详情事实保留完整句。`editorial-diff.json` 保存准确前后内容及 JSON 路径。
- 原 live 响应、brief、失败标记和阅读页保持原样；其他新闻条目逐对象相同，技术文件逐字节相同，证据在 `preservation.json`。审阅稿明确为执行助手编辑，不是新模型结果。
- 批量和单条恢复提示各增加一句：正文保留完整事实，截断诊断不照搬，缺口放 uncertainty，不补全未知。没有模型、字段、判定器、删句器或业务代码变更。
- 新稿经正式 DailyBrief 校验、SiteBuilder 构建和正式本地链接检查。第一次构建脚本导入路径写错，未生成页面；纠正为现有 publishing.site_builder 后完成。未修改生产模块来适配脚本。
- 不为提示词写重复断言测试，不重跑54/64或全套。新增费用、外网、模型调用均0，配置、环境、凭据、生产数据不变。

## 代码与五批交付

执行树：`<LOCAL_HOME>/.codex/worktrees/radar-next-release-batch-a/Morning Radar v0.1`；分支 `codex/tendency-grounded-review`。阶段开工 HEAD c796b3117964ee5bfeedcda16c691c7ff15e9567，工作区干净。
本地缓存 origin/master 为8ddd314d5108d987f359f146484af61724f6a5b9，开工相对它 ahead28/behind0。这是旧引用，不是当前远端确认；本轮未fetch。没有同名远端分支引用。

| 批次 | 已核实祖先 | 已有能力及边界 |
|---|---|---|
| A | e507d104（含bb3ef351） | 候选/补采恢复、来源证据、预算计量、健康状态 |
| B1 | b7a48efc | 有界补证、技术阅读卡、正式晨报/隔离预览 |
| C | f67f5a2d | 独立Tendency发现/校准、不可变修订与事务 |
| D | d15220a6（含cdd2da02） | Tendency页面、GitHub输入归档/回执与历史 |
| B2 | bd812339（含a482cc57） | 协调发布、冻结包、Git归档、部署/通知/恢复 |

这些祖先均用本地git merge-base核实，inventory.json记录。后续97e153d4/c650fe29为review边界和810回归基线；077923be至86641267改善证据/语义传递；2f29d843约束写作依据；bfea2e6e修候选适配；6607be3b统一选材及标题输入；c796b311保留合法Story并省略缺项技术卡。本轮仅两份写作提示收尾，保留全部有效提交。
从缓存origin/master到阶段基线，data/与site/无差异；这不能证明远端没有新数据。主Documents checkout不是本任务执行树，不迁移其旧dirty改动。

## 验收证据盘点

- 810：`.tmp/gr-boundary/full.xml`，810 tests/0失败，附79项边界回归。早期失败日志保留，未将其算成成功。
- 223：`.tmp/mr-r1-evidence-flow-20261011/tests-1..3.xml`按同一case后次结果覆盖，223唯一case最终通过；verification.json记录。
- 103：`.tmp/mr-r1-writing-evidence-20261011/verification.json`及最终tests-3/4.xml。早期模块收集错误和改名测试记录保留，不将不同轮简单相加。
- 64：`.tmp/mr-r1-writing-selection-and-title/tests.xml`，零失败；正常/恢复请求一致和事实输入选择已关闭。
- 54：`.tmp/mr-r1-independent-delivery/checks.json`及当时命令回执，技术引文/B1材料/Story构建回归通过；没有独立JUnit文件，明确证据形式，不补跑制造文件。
- B2：`.ai/handoffs/MR-RELEASE-INTEGRATION-B2.md`，726全量后21收尾/93费用回归；正式模块+本地Git+假HTTP隔离端到端，不能称云端发布成功。
- MR-R1真模型：`.tmp/mr-r1-independent-delivery/live/acceptance.json`及五份raw，5次、恢复0、finish_reason均stop；新增0.02727768元，任务累计78次/0.36574504元。这是历史实测，不是后续授权余额。
- 本次手工编辑、正文/来源链接、正式页面构建是免费检查；未来模型文案效果不是保证。

已关闭：正常写作与选材、成功新闻Story复用、Anthropic标题归因、技术摘要支持的问题/方法/作者依据及诚实缺口、输入链接、局部失败隔离、计费记录。可选认知延伸/观察项不合格被舍弃是正常降级，不开新任务。费城编辑已在审阅稿完成，原失败标记不改。

## Tendency：已有证明与最小剩余

`.tmp/grounded-real-review-20261011/REPORT.md`：1次真实审查（6000输出上限，finish stop），拒收真实不合格首版rev-5b9e…，接收两个人工/synthetic谨慎对照rev-7f183…、rev-38469…。它证明正式逐项审查和事务路径可用，能识别部分越界，且不过度拒绝有界推断；不证明两个人工对照是独立发现。报告还记录漏检和early_reason摘要不准确，不能称审查全面可靠。

剩余最小产品验收：在已保存的真实原始证据池（非人工答案、非合成材料）上，仅一次有界独立discovery及正常grounded review；逐条对照事实前提、来源是否独立、条件、可反证点、与旧命题的实质新增价值。0条可以是有效结果，不能强行生成；若0条或无有用新命题，只能关闭运行边界，独立发现价值仍未证明。已存在真实坏首版保留为反例，不重新购买审查对照。不要求长期误杀率或开放式准确率研究。

建议剩余一次隔离核心验收上限：沿用当前3逻辑/4网络及24k输入边界，最多4次物理尝试含恢复，共享生产策略日预算0.4元内，先冻结实际上下文/各阶段完整请求上限并核价；超过完整预留则不启动。仅保存真实输入，由正式run_core读取，独立CoreStore与GitArchive(remote=None)，禁止把审阅稿/人工对照混入。当前没有这项授权，也没有运行。

## 必要产品决定（集中选择，不擅自切换）

1. **整套候选一起试运行（建议）**：保留当前Daily→Tendency→deep_continuity协调流程。完成上述一次真实Tendency闭环及远端发布验证后上线；对发现价值依然不足的情况明确展示实验性边界，是否接受由用户决定。无需现在新增运行开关。
2. **晨报优先试运行，Tendency公开新增延后**：现有协调器无此发布模式，coordinator.py直接执行tendency且构建其页面；不能仅在文档说不跑就自动隔离。需另行批准一个明确的阶段选择/发布范围小修改（保留旧Tendency历史只读、跳过新生成及其费用，不删除数据），再准备对应发布包。当前未实施、不改配置、不承诺现有默认入口可做到。

## 生产默认路径与真实差异

本次真模型验收使用正式归并/评分/写作/校验/SiteBuilder，但采集、上游新闻归并评分、历史选择、全链预算与运行目录由冻结隔离入口控制；不是当前生产采集、GitHub输入、全日预算、Tendency、连续性、远端归档和公开投递全链通过。

`config/release.yaml`仍为日限0.4、最多60物理调用、Tendency预留0.08、连续性0.04、price:null；MR-R1累计5元只属于历史受控任务。Accounting对真实模型匹配价格缺失会阻止收费，不能把当前默认配置当成可直接上线状态。上线前需要明确授权只填已核实PriceQuote（model、currency、input/output/cache价格、source_url、verified_on），不顺带提高日预算；按发布时实际时段评估，不能把旧空闲价用于持续高峰时段运行。日预算是否足以支撑整套质量尚未被本次局部验收证明。

- CI：pull_request触发Ubuntu/Python3.12，pytest全套+Ruff；本地证据复用不能替代新集成SHA远端CI。不能将旧本地Actions引用当成现在绿灯。
- Daily：共享morning-radar-generated-data队列，production仅master；generate后自动upload/deploy/verify/notify，contents.write/issues.read/pages.write/id-token.write，github-pages环境。定时03:37 Asia/Singapore不更改。合并可能让下一次定时自动执行，不能先合并再考虑授权。
- preview：Daily fixtures/dry_run仅release demo（synthetic）并上传artifact；不是生产内容验收。Manual Preview也不是生产全链验收。
- Post-Daily已是只读status诊断，不再第二次生成；Inbox归档共享队列、固定master、读issues/写contents；API身份、编辑/关闭、补扫和回执尚需远端验证。
- Pages/CDN确切字节、OIDC/环境权限、PUBLIC_SITE_URL基路径、真实WxPusher接收端尚未验证。现有CLI live要求授权GitHub Actions、Tkkdy/Morning-Radar、master，不能伪造环境绕过。

## 集成、历史保护与回滚

获批后只fetch确认最新origin/master SHA，在其新集成分支应用本候选代码改动，绝不用当前旧树data/site快照覆盖远端新历史。先生成远端data家族清单及SHA备份，保留当前有效release_id/package_hash/deployment_id/通知回执。候选补丁限定src/prompts/templates/config/workflows/tests/相关文档，逐项审核配置与workflow差异；不直接合并带旧生成数据的整树。

GitArchive明确归档intake/raw/snapshots/state/briefs/stories/continuity/tendencies/radar_signals/evidence/usage/health/tendency-core/v2/releases/v1，拒绝synthetic和不相关已暂存变更；site本身不是归档权威，冻结包位于releases/v1。写入非快进冲突保留本地提交，在最新权威checkout重放允许的事务，不自动rebase/覆盖可变JSON。

部署恢复先复用同release_id冻结包export/deploy，失败或未知不通知；未知先取真实deployment_id后reconcile。页面回滚选择上一已验证release_id原包，保留新的输入、usage、修订和回执，不重新生成、不发通知。通知失败只恢复notify，unknown先对账；resend必须独立承担重复发送授权。代码回滚不能整体退回旧双workflow，须保留B2归档/读取/恢复兼容，再回退业务变更；不删除历史迎合旧代码。

## 一次远端阶段授权清单（现在均未执行）

- 仓库读权限：fetch/读取Tkkdy/Morning-Radar最新master、现有PR/Actions结论、Pages设置/环境限制、既有release和通知回执；仅读凭据是否配置，不输出秘密。允许读取指定测试Issue的原始身份、编辑与关闭事件，范围最多1个用户指定Issue及其必要回执。
- 集成写权限：最新master上的新codex/集成分支，推送本候选代码、建立/更新一个PR，允许CI及一个synthetic Daily preview。禁止force push，禁止旧data/site回写。需明确是否包括合并；合并同时承担下一次定时可能运行的影响。
- 配置授权：仅release.yaml.price从null更新为当时核实的匹配PriceQuote；若选择晨报优先，另行批准上述阶段范围修改。其余模型、域名、来源、预算、环境/凭据不动。
- 剩余真实验证：一次隔离Tendency discovery+review（上限4次/共享0.4元）以及一次生产默认全链试运行（上限60物理/共享日0.4元，含必要恢复，不是每模块各0.4）。先核价/任务和业务日账本/全链预留，有未知计费或额度不足即停，不自动加钱。来源访问严格沿既有配置允许域名、有限超时重试，不新增来源；具体活跃来源与待处理Issue清单在获准远端只读后锁定，无法现在声称已知远端当天候选。
- 发布与通知：授权一个已审阅release_id的Pages部署及公网哈希核验，并单独明确一次既有接收方通知是否允许。默认generate/deploy工作流会进入notify，不含通知授权时不得启动该默认链。失败恢复仅同包/同release_id，不新增生成；rollback只到指定上一有效包，不通知；resend不包含在本清单。

真实外部状态、上线范围选择和上述具体权限是剩余边界；本地可做部分已完成。历史审阅稿是内容决策材料，不作为今日生产数据、自动发布包或新模型成功证据。
