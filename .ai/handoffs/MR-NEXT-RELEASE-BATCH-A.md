# Morning Radar 首批实现交付（2026-10-10）

## 开发基线与工作区保护

- 产品目标：用户 `10-10/PRODUCT_BASELINE_2026-10-10.md`；本批范围：`CODEX_FIRST_BATCH_2026-10-10.md`，执行时以用户后续授权修正。
- origin 已联网核实为 `https://github.com/Tkkdy/Morning-Radar.git`；fetch 后 ls-remote 确认并采用 master **8ddd314d5108d987f359f146484af61724f6a5b9**。
- 分支：`codex/radar-next-release-batch-a`。
- worktree：`<LOCAL_HOME>\.codex\worktrees\radar-next-release-batch-a\Morning Radar v0.1`。
- 原目录：`<LOCAL_HOME>\Documents\Morning Radar v0.1`；原分支 `codex/review-brief-quality`，HEAD `6d3ace72f2e4b64df07cc594f3d32fb9756dcef3`。
- 原目录三份已跟踪差异：`.github/workflows/daily-brief.yml`、`tests/unit/test_workflow.py`、`HANDOFF.md`。未跟踪资料清单、完整 diff、data/site SHA256 已记录于新 worktree `.tmp/batch-a/original-state.json`。
- fetch 前相对旧 origin/master 的 11 个本地提交，fetch 后全部已在远端 master；`origin/master..HEAD` 和 `git cherry origin/master HEAD` 均为空。没有 cherry-pick，没有迁移原目录未提交修改。
- 已核验原 HEAD/status/diff/data/site 与初始记录一致，新 worktree 的生产 data/site 与基线哈希一致；未 stash/reset/clean，未复制旧 src/data/site/虚拟环境。

## 实际修复与复用

| 缺陷或限制 | 本批结果 | 验收依据 |
| --- | --- | --- |
| 非官方候选及时采集，工程延期后被当成迟到旧闻 | 依据 durable first observation 区分 backlog 与 late discovery；仍受原发布时间及 7 天恢复期限双重限制 | 真实 checkpoint 输入的资格回放、非官方 synthetic 反例、到期边界 |
| 官方 listing 丢失 priority/official metadata | 贯通到 RawItem；StorySourceRef 增加可选补录时间，展示保留原发布时间 | listing metadata、旧 source ref 兼容、原始材料不变断言 |
| 深处理上限先于候选审视，例行源挤占 | 超出原深处理队列时，最多审视 200 条简短 metadata，总输入上限 24k；来源交错送审，重要性决定深处理顺序，不增来源展示配额；小池保持原完整分类流程 | 用真实输入配合显式 synthetic 判断，确认原 deferred 样本进入深处理、其余 raw/身份保留 |
| 上游 merge/score/Shadow 用尽后段预算 | 昂贵阶段前保留 7 个逻辑任务、40k 正文与 8k 连续性规划空间；进入正文时替换为既有实际 payload 预留；删除连续性另一套 Story+5000 估算 | 早期预算竞争测试与既有正文预留/恢复测试 |
| 非法 URL 严拒后缺少局部恢复 | 仅 URL 类失败允许一次缩小材料后的重新生成；重新验证原始 URL 集合；仍失败回 FAILED_RETRY；预算耗尽单列 DEFERRED_BUDGET | URL 两次失败/第二次成功；历史 Mistral raw + synthetic 错误的 ledger 持久化 |
| 合法列表超 256KiB、现代页面非 article 卡结构 | 来源级 1MiB streaming 上限、heading/title-span 链接卡解析；保留超时、有限重试、User-Agent 和条目上限 | 当前页面只读观测、真实列表片段 fixture、流式超限测试 |
| RSS 单源异常被总采集掩盖；200/304 被误当新鲜成功 | 逐源失败进入 CollectionResult；记录 HTTP 状态、最新发布时间、空/陈旧/未知时间；304 检查缓存发布时间 | 403 与 304 陈旧案例、现有 RSS 超时/空数据测试 |
| 健康只靠 workflow 成功或 fallback | 独立 `site/health-v1.json`；daily/tendency/deep_continuity/model_ab 各次运行持久化；区分 no_candidates/no_changes/not_configured/budget_skipped/validation_failed/failed/cancelled/partial | 独立模块、取消/失败、同日重跑、旧批次恢复失败计数；原 status 契约全套测试 |
| 费用缺少真实调用身份 | DeepSeek、继承适配器及 OpenAI 在每次物理调用前记录 UUID，响应记录可用 usage；同记录重复导入去重，新重试/重跑累加；按业务日与模块汇总 | 重复导入、同日新调用、结构重试、usage/价格缺失、synthetic 价格算术 |
| TextGrain 官方标题与媒体标题不同导致拆组 | 最小扩展：同一短时间窗、不同来源、标题/摘要共享具体主体及动作线索才进入候选组；保留版本冲突/时间边界，最后仍由严格 AI 合并判断 | 两份历史原文的候选归并；synthetic 版本/后续时间分离 |

已复用原有 checkpoint/ledger、输入版本身份、恢复通道、完整分类、正文实际 payload 预留、有界 item recovery、Pydantic/来源验证及公开 status 契约。未重新实现 master 已有的 Tendency reasoning/retry 修复。候选审视不会保证某条内容一定上主卡；完整材料仍须经过事实、评分与展示规则。

## 历史证据与回放边界

机器清单：`tests/fixtures/batch_a/manifest.json`、`history.json`。12 份选中 raw/checkpoint/当时 ledger 记录保持原字段，新增的审计说明单独置于外层；对象完整 SHA256、运行 SHA、报告段落、采集/处理时间、预期和实际行为均可查。完整历史对象下载保存在忽略目录 `.tmp/batch-a/history/`，未覆盖生产历史。

| 日期 | Actions run / 执行 HEAD | intake / generated 提交 | 已取得 |
| --- | --- | --- | --- |
| 09-30 | 36643690053 / 3046d42cc0bcdfb21898d21ef7ec48c4860994a1 | d9e837b99012510a7fef228662a90571f79db12e / d7416ff501f9042f3150d834bf61f2d2d7fa1f56 | raw、checkpoint、两阶段 ledger、Story/Brief/Editorial/Continuity、配置 |
| 10-06 | 37396542508 / 3584a4a8f9e47fc0f8cc3b7a8f00fb7fe1b0a433 | 25e160caa623c38bebfd677e928f4cb32a9ad29d / 8657b9e3fdaff9fd3ef86579e695255929fe9277 | 同上 |
| 10-07 | 37545287658 / 8622fc4ec031b7096facc6aa430f3014eb8ef920 | 61ad8df2db00a8f510d1b995fc0fc04901c07f69 / 78852c06cba0a0f5bb417a56bc8c0094dbf5e0c1 | 同上，含两个相关 checkpoint |

- **已确认状态**：09-30 Dots、官方 Sol、DevDay deferred；10-06 Beam deferred、ChatGPT 广告低相关阈值未选；10-07 Mistral merge_failed、EmbeddingGemma 2 与 Beam deferred。
- Dots 后来于 10-05、官方 Sol 于 10-01 被处理。故障是当期延期/恢复资格风险，不能描述为永久丢失。
- 10-06 Brief 记录输入字符 119194/120000、11 个 item fallback、continuity budget skip；10-07 112050/120000、continuity budget skip。原始请求序列不足，完整预算事故未回放。
- TextGrain 两个历史 raw 的候选分组已回放；最终模型语义合并未回放。
- Mistral 原始 raw 中的 URL 保持原样，包括尾部特殊字符；没有猜测修正。原始模型非法输出不可得，测试明确为历史输入 + synthetic URL 错误。
- 10-06 no-run 已由 10-07 审计撤销，Actions 元数据也确认 schedule success；未构造“漏跑修复”。
- 三天 Actions 日志下载均返回 HTTP 403（匿名接口）；本机 gh 未登录。没有改变认证或 Secrets。模型原始响应未取得，语义重放保持 unresolved。
- 当期官方完整 HTTP 响应体未取得。10-10 当前页面观测：Anthropic News 429419 bytes/10 条，Research 282283 bytes/11 条；xAI 274956 bytes/85 条，最新 source_date 09-28，正确标陈旧。当前观测不替代历史快照。
- AIHOT 匹配历史截稿时间的原始快照、完整使用条款核查未完成，未开启生产采集，也未把新抓网页视为历史覆盖证明。

## 配置与 workflow 清单

- `config/app.yaml`：新增 `candidate_review_limit=200`、`candidate_review_characters=24000`、`planned_brief_characters=40000`、`planned_continuity_characters=8000`。没有提高 50 次逻辑调用/120k 字符/60 次网络请求上限。40k 是前段保护规划，正文仍用实际请求负载预留和消费，不能视为精确人民币预算。
- `config/sources.yaml`：Anthropic News/Research、xAI News 明确 `maximum_response_bytes=1048576`。SourceConfig 新字段兼容旧 YAML；RSS 从无限制读完整响应改为同样受控的来源级读取。
- `daily-brief.yml`：仅增加 always 诊断 artifact 保存，7 天保留、无文件忽略。
- `post-daily.yml`：增加 data/usage、data/health 的既有提交路径，以及相同诊断 artifact 保存。
- 所有现有 cron、模型、Secrets、AIHOT disabled、Editorial Shadow、部署/通知条件不变。代码中的既有 push/部署步骤没有执行。
- 原 `site/status.json` 无字段变更。新增 `health-v1.json` 及 data/health、data/usage 路径，都是附加接口。

## 测试与隔离

- Python 3.12 使用原 `.venv/Scripts/python.exe` 只读依赖；pytest 的 `pythonpath=src` 加载新 worktree。没有安装依赖或修改开发环境。独立脚本显式将新 worktree 的 src 放在 sys.path 首位。
- 修改前：完整离线 pytest **613 passed**；Ruff passed。没有基线失败。
- 最终：完整离线 pytest **634 passed in 37.62s**；Ruff **All checks passed**；`git diff --check` **passed**。所有 pytest 运行全局拒绝 socket.connect/create_connection，AI 使用 Fake/Mock，无付费调用。`-B -X utf8`，pytest 禁用 cacheprovider，basetemp/JUnit/日志全部位于 `.tmp/batch-a/`。
- 命令：`pytest -o addopts= -q -p no:cacheprovider --basetemp=.tmp/batch-a/delivery-tests --junitxml=.tmp/batch-a/delivery-pytest.xml`（经上述离线 Python 包装）。Ruff：`python -B -m ruff check --no-cache src tests`；`git diff --check`。
- 中间发现并修正：小候选池不应改用预审、Fake Provider 无 budget 兼容、新字段精确断言、新测试构造字段、健康漏算旧批次恢复失败。没有把这些失败归为基线问题。
- NOT TESTED：生产 Actions/部署/通知、真实模型效果与账单、整轮每天 0.4 元达标、历史原始模型输出完整回放、真实进程强杀/runner 丢失后的最终归档。捕获式取消已有测试，强杀只可能留下 running/started，不能伪报完成。

## 风险、回滚与后续衔接

- 广覆盖审视会消耗一部分原上游额度；缩短 metadata 可能误判，因此审视不相关仅留作待深入判断，不能终结原始候选。若未处理完，健康状态明确为 partial。
- 7 天恢复期限未扩大。当前恢复队列仍有界，并不承诺全网零遗漏；过期 unresolved 不被伪装成新发生。
- 同事件新匹配只扩候选组，最终仍可能受模型语义错误影响；不同版本/明确后续不强合并。
- 新 source parsing 基于已测量页面结构；网站改版、持续 403、陈旧更新仍会失败/降级，不能绕过访问控制。
- usage 与价格缺失时 total_cost_cny 为 null/unavailable；known_cost_cny 只是已知部分。未配置生产价格，synthetic quote 不可用于成本承诺。真实运行的模型名称、官方价格生效日期/缓存口径与供应商账单仍需后续授权验证。
- health 的 complete 只表示本接口定义的当日模块执行状态，不表示 A–D 整轮产品已完成，也不证明不存在漏报。当前 health 在 prepare_process 之后开始记录；进程尚未进入该阶段就失败时，模块仍显示未运行/不可用，workflow artifact 也可能没有记录。
- 回滚：保持本分支不合并即可完全隔离；若未来集成，revert 本批提交并重新构建。仅回退代码和配置，不删除生产 raw/ledger，也不回写历史。旧消费者继续读取原 status.json。
- Tendency 仍在整轮范围。批次 B 完成更完整的晨报处理/交付；C 实现独立发现与校准；D 实现独立页面、批量输入、真实持久化回执及修订历史。本批只交付共享输入、恢复与健康/费用底座，没有提前做页面或移除后续职责。


## A 批限定复核（后续提交，不改写 bb3ef351）

结论：PATCH_REQUIRED → 修复后 PASS，可进入 B1。检查了 8ddd314..bb3ef351 的候选、来源、分组、预算、计量、健康及 workflow 差异和必要上下游，没有重新全仓审计。

已证实并修复：

1. `intake/review.py:78` 附近：仅挑预审 relevant=true，且把 120 字摘要分类直接用作深处理分类。摘要误判时同一记录会反复 deferred，不能用完整材料纠正。现按预审排序后取有界队列，剩余额度允许未审/负向候选进入；完整原始材料再次分类，负向预审不终结候选。
2. `processing/grouping.py:164` 附近：共享 actor、两个词和 ing/tion 词尾即匹配。实际最小反例“OpenAI image generation pricing”与“OpenAI code generation pricing”返回同组。现要求官方锚点、具体共同主题与标题依据，排除通用能力词及明显后续信号；历史 TextGrain 仍进入候选组。原来只是误分候选组，不等于已证明生产最终误合并。
3. `collectors/official_listing.py:210` 附近：同 URL 的 anchor 空 excerpt 覆盖 article 摘录。现优先保留非空 article evidence；未来日期不再标新鲜。
4. `health.py:30` 附近：公开健康输出直接带 discovery_audit 的原始异常文本，可能含带查询参数的请求 URL。现公开来源诊断使用字段白名单，collector 不保存任意异常文本为 reason；合成 token/响应内容不会进入公开 health。

候选 identity/content_version、窗口与延期 ledger 衔接已检查；物理调用 UUID 的重试累加/重复导入、缺失 usage 为 unavailable、正文 reservation 释放与旧 status 契约未发现新增阻塞问题。保留风险：规划额度不能证明人民币合规；价格未配置；异常强杀前尚未进入模块的情况只能报告未运行；同事件最终语义仍需要真实模型验证。不得把这些模拟回归当作真实模型质量验证。

验证：59 项针对性测试通过，Ruff passed。初次测试的 13 个 setup errors 来自新 basetemp 父目录未创建，修正目录后均通过；不是业务回归。A 原始 634 项完整测试证据继续复用，B1 完成后对最终代码运行全套。未改变模型、Secrets、定时、发布、通知与源开关。
