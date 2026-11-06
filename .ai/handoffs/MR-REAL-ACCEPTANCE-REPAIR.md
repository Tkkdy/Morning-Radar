# 首次真实验收：定点修复与免费回放

结论：本地修复与冻结响应回放完成；尚未完成修复后的真实模型验收，不能申请发布。两条原候选格式恢复成功，但仍未获接收。没有新增外部访问或付费调用。

## 基线与保护

- 基线 `13bb8fcf9cc23094c48e65c9132d570931114ee3`；修复代码提交 `5b3cfcca267b680ad46406bd27a6517b7e8ba387`。
- 分支 `codex/radar-real-acceptance-repair`；从原工作 worktree 最新提交继续，未重写 A/B1/C/D/B2。
- 执行目录 `C:/Users/PsyDuck/.codex/worktrees/radar-next-release-batch-a/Morning Radar v0.1`。
- 解释器为原目录 `.venv/Scripts/python.exe`，显式把当前 worktree 的 `src` 放在 sys.path 首位；正式核心实际来自当前 worktree。每次回放记录了解释器、模块路径和输入哈希。没有运行旧 editable install 的代码。
- 首次收费试跑仍归属于 `bd812339...`，不能把本次修复倒填为该次真实效果。首次验收 ZIP SHA256 仍为 `dd6e4b0ddfd16beb09c202ed5f20a273690c1725d4ac4f83eb960fbbad8d2b2a`。
- 原工作区 HEAD/status、两个工作区的生产 data/site 文件哈希、首次 ZIP 均未变。原失败、中间格式恢复回放、最终拒收均独立保存。
- 业务资料日为 2026-10-11（Asia/Singapore）；观察截止仍是 2026-10-10T16:13:24.454289Z，不新增现实观察时间。

## 1. 原始响应与严格契约

`ai/tendency_core.py:24` 仅规范化 `decisions/*/hypothesis/conditions_note=null`、`unresolved_note=null`。规则版本 `core-null-notes-v1`，四处转换逐项记录，保存原始输出。非空同键、其他未知键仍被严格模型拒绝。claim/scope/引用/修订身份不修改。

实际 wire 使用 `response_format={"type":"json_object"}`，没有 tools/strict tool call。嵌套 CoreDecision schema 在 system 消息中，而非服务端 strict schema 承诺。`C/formal-wire.json` 保存脱敏完整消息；原始包未留当时完整 HTTP 请求，修改前请求是本地当前基线重建，不能证明服务端当时逐字收到相同 system 消息。业务输入与原 019 响应有精确哈希验证。

提示词尾部的手写 schema 已改为运行时从 CoreDecision 生成；DeepSeek/OpenAI 共享同一函数，自动一致性测试覆盖。分析版本也纳入 schema 哈希。

| 路径 | 第一条 Context Diet | 第二条 Codemode | 事务修订数 |
|---|---|---|---:|
| before：原始 019 正式 Provider 回放 | extra_forbidden | extra_forbidden | 0 |
| format-only：仅格式兼容 | 未核验线索不能支持/反驳条件 | 旧语义校验可接收 | 1（仅隔离中间诊断） |
| final-exact-replay：全部修复 | 来源域名不是命题范围 | 来源域名不是命题范围 | 0 |

中间那条修订仅用于证明旧验证缺口，绝不导入正式历史。最终两条候选均保留拒绝记录；没有手动改写原候选后声称其通过。

## 2. Scope、引用作用、结论与修订

- `tendencies/inputs.py:102` 不再用 hostname 补 scope；缺失语义范围保留空列表。source_url/source_role 继续表达出处。
- `core_models.py` 为命题增加 observed/inferred 范围依据及 scope_reason；单条可靠来源可以支持 Early 外推，但必须说明依据、待解边界。不能把 inferred 当 observed 来 supported/strengthened。旧持久化记录无需迁移，默认值兼容读取；不改写旧文件。
- `core_validation.py:93` 拒绝用输入来源域名冒充语义范围。页面及导出会显示“待验证外推”，不是只藏在 JSON。
- 引用增加 source_claim、observation、boundary，保留旧 support/counter/context。observation 仅接受 original_fact；作者主张不能假装独立观察，未核验线索仍不能证明条件。
- `core_validation.py:237` 增加两类窄内容矛盾检查：正确性保证与未解决的正确性/归因问题并存；子 agent“不可能替代”与引文“理论可行”并存。这是可测的一致性护栏，不是完整事实或因果判断器。
- 原文 10/10 引文位置真实，但 Context Diet 的“正确性不因压缩受损”超出小样本自报；Codemode 把工程困难升级为不可能。其反向引文分别更适合作为朴素截断的边界、CLI 替代路径的限制，不自动等于方案被独立现实反证。
- 另发现并修复 no_change 提前返回绕过引文检查；现在维持判断也必须通过引用验证。相同 evidence_id 的摘录装包长度变化不再制造“新增现实证据”。旧版本/CAS、不变首录时间、单项拒绝不抹去合法同批项均测试。

## 3. C 扫描—可见—选择—装包

原路径先截 checkpoint 的前 200 行，再去重；用所有摘录构造查询后匹配自身，偏向长文本；最后从选定列表尾部删材料，历史背景和短新材料没有合理竞争机会。

修复：checkpoint 扫描行数与可见数量分离，默认每文件最多 5000 行，最多 200 文件，超过页限的偏移记录在 run 中，后续运行续扫。候选按来源轮转获得可见机会，不预留展示席位。发现不再以全体摘录自匹配，当前观察与相关背景交替装入；请求给出简短线索概览及有限原文，删除无判断作用的存储元数据。校验器只能使用实际送入请求的截短原文。

| 指标 | 旧真实试跑 | 新冻结材料选材回放 |
|---|---:|---:|
| 当前 checkpoint 记录 | 2873 | 同一份 |
| 24 小时明确日期记录 | 74 | 同一份 |
| 首次登记 input_id | 143 | 同一份 |
| C 可见 | 200 | 200 |
| 可见中的首次登记 | 36 | 29 |
| 请求中的原文 | 3 | 5 |
| 原文中的首次登记 | 0 | 2 |
| 简短线索概览 | 0 | 9 |
| 业务请求字符数 | 7721 | 7380 / 8000 |

本轮扫描 120 个文件、77186 条 checkpoint 行、去重后 5686 条证据；上限仍有截断，不能据此声称召回率通过。新可见数中的首次登记反而少 7 条，说明改动是竞争顺序调整，不是所有覆盖指标均提升。`selection/disposition.json` 列明版本、观察时间、空摘录、选取/字符上限及装包开销。新选材请求在正式核心的零调用预算处截停，没有把未调用模型记成“检查无变化”。

新原文包括本轮观察、历史 Codemode 背景和未核验社区线索；它们不被强制组合成同一个方向。概览目前仍是可追溯标题/摘录短片段，不是模型已完成语义审视的证明。对新排序的真实发现质量尚未验证。

## 4. 十三项补证缺口与新闻版面

13 项均是 not_allowed，补证 HTTP=0，非人民币耗尽；涉及 10 个 host，没有 Hugging Face。逐 URL、来源链、同页等价材料和处置见 `materials/thirteen-gaps.json`。

| input_id 后缀 | host | 最长同页保存摘录 | 处置 |
| bb9b825b495a45f8beae | techcrunch.com | 0 字符 | 精确请求提案，未启用 |
| 820e8a9c9ba3cdb40eff | www.theverge.com | 350 字符 | 保留缺口，未请求访问 |
| 1d14c3f3f0e31d9f9e9f | www.nbcphiladelphia.com | 0 字符 | 精确请求提案，未启用 |
| 10b5076209208aed1637 | techcrunch.com | 108 字符 | 精确请求提案，未启用 |
| 651783d6d93abdf25af1 | decrypt.co | 0 字符 | 保留缺口，未请求访问 |
| 7747bf95ce607792cbaf | www.newscientist.com | 0 字符 | 保留缺口，未请求访问 |
| 2280e550c66d8ffeed1c | www.wsj.com | 0 字符 | 保留缺口，未请求访问 |
| a0f15fdbfcfb2f12bd06 | www.nytimes.com | 24 字符 | 保留缺口，未请求访问 |
| 8914037fa1250d1becb9 | github.com | 194 字符 | 精确请求提案，未启用 |
| 4c4dce735e8ae75f7447 | techcrunch.com | 108 字符 | 精确请求提案，未启用 |
| 6f4a690b059b7da03e00 | www.engadget.com | 0 字符 | 保留缺口，未请求访问 |
| 9cedea6720cf4e4fb105 | platform.claude.com | 0 字符 | 精确请求提案，未启用 |
| a1663d810c6d6c988dce | github.com | 0 字符 | 精确请求提案，未启用 |

TechCrunch 的同页 RSS 与 HN 空摘要共存；已复用祖先提交的 Story 同 URL 摘录保留，无第二套归并器。108 字符摘要可证明“来源这样报道”，不足以填补全部原文细节。其他媒体相似标题不自动当作同事件事实或互证；不访问 archive.ph，不绕过付费墙。

`intake/review.py:86` 按既有规范化 URL 分组后分配深处理机会，同页互补来源仍交给既有 Story 合并；保留总 item 传输限制，不按相似公司名强制并事件。跨 URL 同事件的补证不足仍保留。

`briefing/generator.py:156/264`：无非空 facts 的 Story 不进入正文 AI 调用或头条，保留在其他阅读，中文提示“尚缺足够正文”，不展示运维枚举作为主要正文，不替它写影响分析。旧实际三条 Story 和 017 输出经正式 Provider 解析后的布局回放，结果为一条有事实正文、两条待补证线索。不是重新生成事实、不是事实质量整体通过。

原六次 classify 分为五次宽元数据审视和一次深分类，不能全部称为重复浪费。`materials/funnel.json` 保留每次输出 ID/结果以及五次 merge 的事实情况；17 次前序处理与第18次连续性等以原 usage 记录为准，不用末态 ledger 把旧成功全部算进本轮。原 Editorial 仍因字符预算拒绝；未启用 Active，也未提高人民币额度。

精确配置提案只追加 techcrunch.com、platform.claude.com、github.com、www.nbcphiladelphia.com，位于 `materials/proposed-hosts.yaml`，生产 app.yaml 未改。`proposed-requests.json` 列六个现有输入 URL；实际访问仍待独立授权。协议、域名精确匹配、查询参数拒绝、重定向禁跟随、字节/时限/调用上限均保留，并有正反例假网络测试。

## 5. 技术材料与校准的验收边界

- 技术样本来自冻结 checkpoint 的 OpenAI gpt-oss-safeguard technical report，源发布日期 2025-10-29，摘要 539 字符。不是本期新新闻。人工构造的问题/方法投影通过正式引用校验，伪造引文被拒；评测条件与限制仍 null。没有把 Context Diet 标成论文，也没有凭摘要补造评测结论。真实技术卡生成仍未验证。
- `calibration/` 使用真实 Codemode 摘录的前1600/完整2000字符分阶段暴露；外部模型响应经过明确 synthetic 修改，再走正式 Provider、C 核心与事务。结果 Early→weakened，重开 CoreStore 后可回看首版；首次时间和旧机制保持不变。
- 此轨迹是历史材料的行为检验，synthetic 标记贯穿，不能当作新现实反证、真实模型发现能力或领先性证据。原真实试跑 calibration/submissions=no_input 的记录未动。

## 6. 验证与费用

- 最终完整 pytest：753 passed，145.27 秒，外网封锁，输出在短隔离根；相关失败项/Provider/C/D 复核153 passed。
- 首次完整结果747 passed、3 failed、2 errors亦保留。3项旧正文测试缺少事实前提，补入明确测试事实后保持原断言；2项 Windows os.replace 被拒，短隔离根复跑成功，具体系统原因未确认，不能断言是路径长度问题。
- 原始空字段、非空同键、未知键、错证据 ID、编造引文、旧修订冲突、单项失败与合法同批提交、分页与预算截断、补证 host 正反例、无事实头条、旧技术引文等覆盖。
- Ruff 与 git diff --check 通过。初始 lint 和相关失败日志保留。
- 本轮新增真实调用=0，新增费用=0；回放未写项目 usage，不把旧 usage 当作新费用或再次记账。原19笔估算0.12525084元不变。原余额0.27474916元未重新确认、不是授权。
- 记录了完整模型消息、json_object 模式及6000最大输出；按旧离峰 input=1/output=4元每百万 token、UTF-8字节保守上界演算，当前固定请求一次预留示例0.041999元。该数字不是新价格确认或真实账单，不能用于直接开跑。

## 7. 下一次最小真实复测（待单独授权）

1. 先确认业务日所有机器/工作目录/生产归档的最新已用额度，再核对当时官方模型/时段价格。冻结新请求完整 messages、最大输出、网络尝试上限；没有可确认剩余额度即停止。
2. 先只做 C discovery 一次（最多一次网络请求、6000输出，示例预留约0.042元，以执行时完整输入和价格重算）。使用修复选材请求，不注入本报告两候选的修正答案。若无合格新方向，明确无接受项或证据不足；不要反复请求直到生成。
3. 若产生合法首版，再以有实际相关差异的已冻结材料做一次 calibration；同样最多一次请求、6000输出，预留按该目标版本重新计算。无合适材料就标未验证，不能复制 synthetic 轨迹充数。
4. 内容补证另行申请：优先两条原头条对应的 NBC/TechCrunch 原 URL，以及一份技术报告原 URL。最多3个公开页面，各不超过524288字节、不跟随重定向，冻结新取得时间。六URL提案不是自动全抓许可。基于取得正文再决定一个新闻样本或技术样本的最小 merge/score/write 调用，分别按正式任务策略最大输出及全部可能重试预留；未取得材料前不承诺金额或调用成功。
5. 上述均为缩小范围复测，不重跑整期。真实技术表达、当前来源可读性、语义反证对结论强度的实际影响、云端摄取/发布费用仍未通过。本任务不申请 push/Actions/部署/通知/Issue写入。

## 8. 本地使用与回滚

解压小包后双击 `index.html`，链接到新闻布局、拒收对照、材料去向、技术样本与 synthetic 历史；无需 localhost 或后端。JSON保留机器可查证据。

在修复分支工作树运行 `python -X utf8 -B <解压目录>/scripts/test_runner.py tests/unit/test_real_acceptance_repair.py -p no:cacheprovider --basetemp=<新的短临时目录>`；使用现有虚拟环境解释器。runner 显式导入当前src并封锁非本机 socket，不读取密钥。不要运行旧一次性付费 run-real.py。

回滚用本地 revert 本次修复代码提交，保留报告和隔离包；不 reset、不清理历史。没有数据库迁移、workflow或Secrets改动。新增模型字段有读取默认值，但旧代码不认识新 revision 字段/角色；若未来已产生新修订，回退前保留整套事务档案，不把新版本历史交给旧代码强读。当前修复没有写生产新修订。
