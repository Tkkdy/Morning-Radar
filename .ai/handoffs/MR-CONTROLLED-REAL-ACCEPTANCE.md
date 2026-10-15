# Morning Radar 本地受控真实验收
日期：2026-10-11（Asia/Singapore）。状态：真实试跑已执行，整期验收未通过；不进入远端发布。

## 结论
一次真实试跑完成了公开来源采集、真实模型处理和正式协调器冻结构建。晨报有 3 条正文、2 条 Radar 信号；技术卡 0。Tendency 发现调用真实执行，但 2 条候选因非法字段被逐项拒收，正式修订 0。不能将其解释为“已检查无变化”，也不能以剩余预算为由重复生成直到通过。
需要先解决真实模型输出契约兼容与材料充分性问题，再验收有效发现/校准；GitHub 正式摄取凭据也尚不可用。本次不申请远端验收。

## 代码与隔离
- 真实执行基线：bd812339d0aedff982130ed1b3e3566d127026d7，开工干净，保留全部 B2 收尾和祖先。
- 验收分支：codex/radar-controlled-real-acceptance。
- 后续局部修复：d6fd69e9ea51e9aa1e5eb4a12b1b69035b4330dc。本次收费产物没有在该补丁上重新生成。
- 解释器：<LOCAL_HOME>/Documents/Morning Radar v0.1/.venv/Scripts/python.exe。
- 实际模块来自：<LOCAL_HOME>/.codex/worktrees/radar-next-release-batch-a/Morning Radar v0.1/.tmp/controlled-acceptance-20261011/run/src/morning_radar/。
- 启动脚本显式把隔离副本 src 放到 sys.path 首位，并断言 pipeline/coordinator.__file__ 均在该目录；完整模块哈希见 runtime-identity.json。没有运行原目录 editable install 的旧代码。
- 新隔离根 run 是本地 clone 的 B2 精确提交；移除其 origin，GitArchive(remote=None)，归档只产生本地提交。仅隔离 Git 配置 core.longpaths=true；原环境不改。
- 使用正式 ReleaseCoordinator、Daily/B1、C、GitArchive、模型 Provider。复用真实冻结 CollectionResult 作为采集边界输入，仍执行正式 intake、选择、处理、历史与页面冻结；没有假协调器或 Fake Provider。
- 运行入口只 generate/export。HTTP 写方法仅允许 api.deepseek.com 的 chat/completions；GitHub GraphQL 摄取因本地无凭据被入口阻止并记 failed。没有远端 push、Pages 部署、通知、Issue 写入；无 Secrets、生产模型、生产数据修改。
- 为避免重复采集，原始 HTTP 获取仅执行一次。补证按现有允许域执行；本次实际补证 HTTP 为 0（重要缺口均不在白名单）。

## 冻结输入与时间边界
- 逻辑观察截止：2026-10-10T16:13:24.454289Z，即新加坡 2026-10-11 00:13:24。
- 新闻窗口：此前 24 小时；采集 checkpoint 的 cutoff_at 是下界（额外 6 小时缓冲，共 30 小时），不是发布包的上界。有界恢复继续按现有 7 日规则；不能把旧记录的已处理状态算成本次新处理。
- 真实采集 checkpoint 2,873 条，无 collector failure。明确 published_at 落在 24 小时窗口的 74 条；正式新闻窗口统计 83 条还包含市场等使用既有时间规则的记录。原始材料保留事件时间，未伪造为今天发生。
- 采集器以传入 clock 统一记录本轮逻辑 fetched_at/持久化时间；这是本轮观察标记，不代表每个 HTTP 响应均在那个微秒完成。入口及结束时间另有日志；原始网页全文不打包。
- 输入同时含此次新采集材料与从基线继承的真实历史。143 个 input_id 首次登记于本轮，正式 C 可见集合中有其中 36 个。例：item-10b5076209208aed1637 同时进入 Daily 正文与 C 的可见证据池。
- “可见”不等于“送模型”：实际 C 在 200 条有界可见记录中进一步裁剪，最终请求只有 3 条，2 条历史材料和 Context Diet 既有材料的新观察。没有声称全部本期关键材料都经过 C 深入分析。
- 发现请求没有用户五个例子、审计答案或预制结论；seeds/registry/targets 均为空。没有 synthetic 输入或个人假设混入真实结果。
- 投递版本按 submitted_at <= cutoff 冻结；本次真实 receipt/submission/revision 清单均为空。截稿后投递排除复用正式核心/协调器的隔离回归，证明不进入请求、版本表或原文页面；不是一次真实 GitHub 投递验收。
- 输入清单含来源性质、时间、内容版本、Daily 状态、是否在 C 可见/实际请求中及展示去向。历史判断不作为新现实证据。

## 费用准入与实际计量
既有安全环境有 DEEPSEEK_API_KEY、DEEPSEEK_MODEL、DEEPSEEK_BASE_URL；只检查存在性，未打印或寻找其他文件中的密钥。免费 /models 与 /user/balance 均 200，余额可用且至少覆盖 0.4 元；没有把账号余额当作项目当日额度。
本地所有登记 worktree 的当日 usage 为 0；GitHub 连接器查询当日 Actions 为 0，最近运行已在前一业务日结束，远端 master 无当日 usage。用户另明确确认没有其他机器/脚本调用，因此当日此前费用可确认为 0。
匿名 GitHub REST 曾返回 403、gh 无登录；后续使用现有只读连接器取得运行和树证据，不提取连接器 Token。

请求模型保留 deepseek-v4-flash，API 响应为 deepseek-flash。官方说明旧名称路由到 DeepSeek-V4.1-Flash；服务别名可变化，不能声称固定了供应商权重版本。
官方价格核查：每百万 tokens 空闲输入 1 元、缓存命中 0.02 元、输出 4 元；高峰分别 2/0.04/8 元。执行时为北京时间星期日，使用空闲价。只修改隔离 release.yaml，生产 price:null 保持不变。
来源：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/

| 项目 | 结果 |
| --- | --- |
| 项目当日上限 | 0.4 CNY，共享额度，未新增额度 |
| 真实物理调用 | 19，19 个独立 call_id；无收费重跑 |
| Daily | 18 次，0.10480484 CNY |
| Tendency | 1 次，0.020446 CNY |
| 输入 / 输出 | 45,227 / 22,797 tokens |
| 已知估算总费用 | 0.12525084 CNY |
| 当日剩余预算 | 0.27474916 CNY，未继续使用 |
| 计费关键 usage 缺失 | 0；所有调用都有输入/输出和缓存 hit/miss |
| reasoning 单列缺失 | 16 次未返回单独细分，不补零；输出总量已返回 |
| 供应商账单对账 | 未完成；上述是实际 API usage × 官方价格，不宣称账单已核销 |

每次请求按完整 messages 序列化字符保守折算、最大输出和当前活跃预留做准入；SDK 隐式重试关闭，新的物理重试仍须再次预留/计量。请求意图先本地 Git 归档，返回用量后释放预留；收费未知则阻止后续。
最低单次预留仍为 0.04，可被更大的输入/输出上界替代。实际如首个 classify 为 0.047332、研究为 0.0921、Tendency 为 0.086892 元。Daily 保护 0.12，Tendency 释放其自身 0.08 后仍保护连续性 0.04，进入深度阶段再释放。没有修改总上限或关闭保护。
费用层没有拒绝；Editorial Shadow 在调用 transport 前被 AI 输入字符上限拒绝，原因 AI daily input character limit exceeded，不是价格或余额不足。预算事件、usage 原始记录与拒绝原因均在包内。
当日任何后续项目调用必须合并包内 19 条正式 usage（仅相同 call_id 去重），不得因为原生产目录没有本地验收数据而重新认定剩余 0.4。未自动将记录写入原生产目录。

## 阶段结果与产物检查
| 阶段 | 实际结果 | 性质与影响 |
| --- | --- | --- |
| GitHub 补扫 | failed，入口禁止无凭据请求 | 配置/访问条件不足，不代表 GitHub 服务失败 |
| 来源采集 | completed，2,873 条，失败 0 | 真实来源；不等于 2,873 条都应在当天展示 |
| Daily | partial | 生成 3 正文、2 Radar；无正文兜底，13 项补证 not_allowed |
| 快速连续性 | completed | 未生成本期新的判断修订；不能视为复杂后续关联已验收 |
| Editorial Shadow | degraded | 输入字符预算拒绝，未开启 Active，也未改发布策略 |
| C discovery | validation_failed | 真实输出 2 候选均含 hypothesis.conditions_note / unresolved_note 非法字段，拒收 |
| C calibration | no_input | 没有旧 C 假设可校准；不算校准质量通过 |
| C submissions | no_input | 无已摄取用户投递；不算真实身份/回执验收 |
| 深度连续性 | no_ai_trigger | 正式检查后无触发，无额外收费 |
| 冻结构建 | partial 包成功 | release_id controlled-real-20261011，非部署成功 |
| 部署/通知 | not_started | 本次禁止，未调用 |

主文 Context Diet 基于作者自报，保留了小样本、未独立复现的限制。7/7、8 对 13 次调用、修复任务 6 对 8 次的描述可对应输入摘录；不能外推普适可靠性。其为社区工具实践，未生成独立技术报告卡，故论文/技术长文表达验收仍缺有效样本。
另两条 Anthropic 头条均显式承认原文信息不足；story facts 为空，分别 unknown/rumor。虽然没有隐瞒不确定性，但关键事实充分性不能判通过。相同 TechCrunch URL 的 RSS 摘录被去重丢弃是已确认的工程缺陷，见下节。
Tendency 两个未通过候选包含原约束、条件、机制、缺口和可证伪检查；10/10 短引文可精确匹配请求摘录。反向引文分别指向“朴素截断丢失失败尾部”和“子 agent CLI 跨进程方案只是粗糙而非不可能”。这些是候选文本中的边界论证，不是正式旧判断受到反证后的持久修订。没有有效 revision，不能验收发现质量、当前/旧版修订链或反证校准。
包内原始当前版、空 C 历史、空回执与 manifest 一致；无发布候选修订冒充正式历史。

## 原 partial 演示
保留原 synthetic-release-1 原包和标签：
- Daily partial 的直接原因：fixture-openai-release 的 example.com 不在补证域名白名单，material_incomplete=1。属于演示输入与配置边界，不是一次真实网络失败注入。
- cost unavailable、physical_calls=0：Fake 没有真实 usage，price:null 也未核价；不把它算作真实零费用证明。
- C completed、calibration/submissions no_input，深度 no_ai_trigger；这些本身不是故障。
- 新增正常条件下的 formal coordinator complete 断言：只补足 synthetic 来源摘录，不改协调器或健康状态，已通过。不能据此证明真实模型能力。

## 已确认问题及局部修复
1. src/morning_radar/tendencies/inputs.py：checkpoint 在取 maximum_evidence 前截断，去重后 len(result) 不超上限时误报 evidence_truncated=false。现合并 checkpoint 层截断标记；不改变选择内容。冻结当前 checkpoint 的免费回放仍为原 199 条，仅标记 false→true。实际整轮含历史时原 run 已另有最终截断 true，未回写它。
2. src/morning_radar/processing/story_builder.py：按 URL 去重先保留空摘要 HN 记录，遗漏同 URL 的 108 字 RSS 摘录；后者落为 story_build_failed。修复为共享候选位置、归并时保留同规范 URL 的材料和来源 ID，并由语义归并继续判断事件；官方分段锚点仍使用原比较规则。冻结真实两条输入的免费 Fake 边界回放确认一组包含两份材料，两条均 merged_into。未声称修复后真实模型新闻质量已重验。
3. 尚未解决的验收问题：真实 Tendency 输出违反已有明确 schema。严格拒绝是正确行为；没有手工删除字段入库，没有调 Prompt 后反复调用。需在后续受控验证中得到合法输出及真实修订证据。
4. 13 项重要材料因域名不在既有允许范围而未补证；本次没有擅自扩大来源范围。其余 71 项 captured 不等于所有信息缺口已补齐。选题有界处理留下候选延期，不将 deferred_budget 混同于 0.4 元已经花完。

## 验证证据与边界
- 复用 B2 726 全量及收尾 21、费用 93 的原证据，未机械重跑全仓。
- 截稿后投递/跨午夜隔离回归：1 passed。
- 新正常完成路径：1 passed，15 deselected。
- C 核心：37 passed。
- Story + A + integration：62 passed。
- 全部上述新增/重跑测试封锁 socket，数据进入临时目录。
- 初次正常路径测试因 Windows 临时路径过长失败；改短 basetemp 后通过。首次 Story 回归启动器缺项目根 sys.path，收集失败；修正启动器后 62 passed。另有只读沙箱 Ruff 缓存写失败，使用 --no-cache 后检查通过。未把这些错误隐去。
- Ruff 修改范围、git diff --check 通过。没有再执行第二次付费 generate。
- 当前 queue:max 实际位于 daily-brief.yml:36、tendency-ingest.yml:12，与 cancel-in-progress:false、共享 group 配套。GitHub 当前官方文档明确支持；最新 actionlint 发布仍为 1.7.12，本地原始退出 1，只报这两处 unexpected key queue。保留原始诊断，不改全局 lint 忽略配置，不宣称真实 Actions 运行通过。
  官方：https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency
  版本：https://github.com/rhysd/actionlint/releases/tag/v1.7.12
- 原工作区 HEAD/status/diff/未提交文件及双方生产 data/site 前后核对；最终保护结果见 protection-verification.json。

## 包的使用与未验证事项
解压后双击 index.html：进入报告、当期晨报、Tendency、材料去向和计量。无需 localhost。
reading/ 保留当期正式 HTML 原字节与必要样式；未带几十期无关旧晨报。archive/legacy 导航提供明确“历史未打包”说明，不冒充原冻结页；package-file-audit.json 区分原包文件和阅读辅助页。原 manifest 保留完整版本/哈希，完整原冻结包仍在隔离根。
data/brief.json、data/core-run.json、data/rejected-model-output.json 是此次真实生成/拒绝的结果，不是 synthetic。synthetic-reference/ 仅解释旧降级演示，和真实目录分离；replay/ 中 Fake 结果只证明修复的结构行为。
包排除 .git、凭据、.env、虚拟环境、原始异常堆栈、无关历史及文章全文。路径中的个人目录在上传版报告和运行身份中替换为 <USER_HOME>；本地原报告保留实际模块路径。
真实账户账单、远端权限、GitHub 正式投递、有效 Tendency 发现/旧假设校准、技术卡与完整后续关联仍未通过。本次 0.12525084 元仅证明这次缩小内容结果的已知模型用量，不能证明完整每日质量在 0.4 元内达标。
未来远端验收须另获授权，再记录实际归档、Artifact 上传、Pages 部署、公开逐文件哈希校验和通知开销；本轮未为这些指标新增系统或执行远端动作。

