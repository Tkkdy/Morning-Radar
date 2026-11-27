# Morning Radar A 限定复核与 B1 交付

## 交付状态

A 复核 PATCH_REQUIRED → 修复后 PASS。B1 本地实现、离线验收、隔离预览完成。没有 push、merge master、部署、生产 Actions、通知、Secrets 修改或付费模型调用。

- 原产品基线：8ddd314d5108d987f359f146484af61724f6a5b9。
- A 原交付：bb3ef351671bc5fcd54eed789e5c3c4306db5be0，保留原历史。
- A 复核修复：e507d1041cdabfbce1ea3191f9ae8883a7cf4424。
- B1 从上述修复提交创建分支 codex/radar-next-release-b1，未重新从 master 开始。
- Worktree：C:/Users/PsyDuck/.codex/worktrees/radar-next-release-batch-a/Morning Radar v0.1（复用隔离 checkout，目录名称保留 batch-a）。B1 提交为包含本报告的后续提交。
- 产品与范围依据：10-10 中 PRODUCT_BASELINE_2026-10-10.md、CODEX_FIRST_BATCH_2026-10-10.md，以及 MR-NEXT-RELEASE-BATCH-A.md。

## A 限定复核：确认的缺陷与修复

阅读 baseline..A 的实际差异及对应上下游，未重新全仓审计。

| 位置（当前文件行附近） | 触发条件与影响 | 处理 |
| --- | --- | --- |
| src/morning_radar/intake/review.py:80 | 短摘要预审负向被永久延期、正向短摘要分类直接复用，完整材料不能纠偏 | 预审只排序，剩余额度允许负向/未审进入；完整输入重新分类 |
| src/morning_radar/processing/grouping.py:174 | shared actor + 通用 generation/pricing + ing/tion 词尾，使 image 与 code pricing 进入同组 | 具体共同主题、标题依据、官方锚点及后续排除；保留历史 TextGrain 候选归并 |
| src/morning_radar/collectors/official_listing.py:208 | 同 URL 的空 anchor 摘录覆盖 article 摘录，证据丢失 | 保留非空摘录；未来日期不标 fresh |
| src/morning_radar/health.py:39 | 原始来源错误带查询 token/响应内容直接公开 | 来源诊断字段白名单；异常仅保存安全原因码/类型 |

59 项针对性回归通过，原 A 634 项完整测试证据复用。TextGrain 测试只确认候选组，未得到真实模型最终语义合并证据。物理调用 UUID 去重/重试累计、missing usage unavailable、正文预留与释放、旧 status 接口未发现新增阻塞问题。本轮复核结束，不追加泛化审计。

历史材料及 unresolved 状态沿用 A 的 manifest/history：09-30、10-06、10-07 raw/checkpoint/当时 ledger 等已取得；Actions 日志匿名 HTTP 403、原始模型响应缺失，未宣称完整生产事故回放。

## B1 实際变更

1. `research/materials.py` 复用 A 的 HttpClient / RequestStartBudget。候选有明确材料缺口，且预审 important 或 source priority=high 才进入补证；只读取原始输入中已有、允许域名的 HTTPS URL，不搜索、不猜 URL、不递归、不跟随重定向。不新增模型调用。
   - 最多 4 份、6 次真实 HTTP 尝试、30 秒、每响应 512 KiB、摘录 1600 字符；仅支持 HTML。逐次重试与流读取受截止时间和超时约束。
   - 保留 fetched/offline/not_allowed/budget_skipped/unsupported_format/parse_empty/failed/cancelled 状态及请求次数；先写 started，再写结果。仅存短摘录、原始时间、内容版本、角色、已观察关联 URL、哈希及缺口，不保存全文。
   - 不改原 raw/checkpoint；只用深拷贝的补证输入继续现有研究、Story 和 Brief 流程。缺口关键词检测是有界启发式，不等于已经证明材料充分。
2. `GroundedPoint` / `TechnicalReading` 增量模型、merge 提示词和校验：论文、技术报告、官方技术长文独立表达问题、方法、工程价值、作者报告证据、条件、限制。每点必须带输入 URL 和 12–280 字符短引文，校验引文存在于对应来源；问题/方法缺失则不接受技术结构，其余未报告字段为 null。拒收沿用现有局部恢复与 merge_failed 状态。
   - 引文存在不能证明中文解释必然正确，真实语义效果仍待验证；不会把作者报告当独立验证，也不强制宣称创新。
3. `grouping.py` 利用页面实际链接到另一个已采集输入的关系，允许配套发布和论文成为同事件候选；仍受版本/时间和模型 same_event 校验约束。引用旧论文不自动合并。预览中配套发布/报告合为一张卡的用例为 synthetic。
4. 普通稿件提示词强化事实、具体差异与意义、必要不确定性及简短表达。模板将已有 continuity.what_changed 提到事实前，避免完全重复的事实句；补录保留原始来源时间。技术卡逐点可展开短引文和原始链接；新字段均有兼容默认值，历史 JSON 可继续读取。
5. `data/evidence/v1/<业务日>/` 保存有界候选池（最多 200）的独立证据，包含延期/未最终入选材料。`data/evidence/events/` 保存所有已构建 Story 的事件 ID、输入及证据关联，发生在最终 Brief 筛选前。`tendencies/inputs.py` 提供读取适配器；未被深入处理的输入只保留输入身份，不伪造事件判断。
6. `preview-b1` CLI 只创建新的隔离目录，拒绝生产 data/site 及其子目录/祖先目录，也拒绝覆盖已存在目录。直接使用 Fake/Mock，不实例化生产流水线、不读取 Secrets。

## 配置与 workflow 兼容影响

- `config/app.yaml` 新增 material_enrichment 配置，enabled=true，以上预算和显式域名列表（arxiv.org、openai.com、www.anthropic.com、research.google、blog.google、mistral.ai、reflection.ai）。旧配置省略该块时默认关闭。生产合入后会增加上述有界公共 HTTP 读取，不增加付费模型调用、原有 AI 上限或模型配置。未发起实时来源请求验证可达性。
- `.github/workflows/daily-brief.yml` 仅在既有 always 诊断 artifact 加 data/evidence/，保留 7 天；成功运行既有 git add data site 已包含新路径。未改变 cron、发布通知策略、Secrets、Actions 触发或权限；本次未执行 workflow。
- 新增证据文件不由 SiteBuilder 公开；健康报告只暴露计数。Editorial Shadow、AIHOT disabled 保持原状。费用缺失继续 unavailable，不将本批零新增 AI 调用当作整轮成本达标。

## 验证结果

- B1 旧路径第一轮：85 passed。新材料第一轮：11 passed / 1 failed，暴露非法端口在 URL identity 计算阶段先抛错；已改为保留输入哈希与 not_allowed 诊断。
- 补证、预览、站点回归：32 passed in 2.41s（包括修复后的非法端口、HTTP/PDF/空响应、取消诊断、虚构引文拒收、截止时间、输出隔离及来源标签）。
- 完整离线 pytest：655 passed in 42.51s，日志 `.tmp/b1/full-final.log` / JUnit `.tmp/b1/full-final.xml`。
- 随后仅增加 workflow evidence artifact 路径与对应断言：workflow/preview 19 passed in 1.96s；日志 `.tmp/b1/workflow-final.log`。未受影响的完整测试证据复用。
- Ruff `check --no-cache src tests`、git diff --check 通过。
- 所有 pytest 使用 socket.connect/create_connection 拒绝联网；Python -B，cacheprovider 关闭，basetemp/日志均在 .tmp/b1。使用原 venv 只读依赖、src 加载当前 worktree，未安装依赖。
- 浏览器已打开本机隔离页面，验证技术方法依据展开、真实链接、来源标签和后续增量先行显示；截图 `.tmp/b1/preview/technical-card.jpg`。
- `.tmp/b1/protection-verification.json`：原工作区 HEAD/status/diff、两边生产 data/site 全部哈希一致。原分支、已跟踪修改、未跟踪资料、本地独有提交均未迁移或覆盖。

## 打开预览

- 静态入口：C:/Users/PsyDuck/.codex/worktrees/radar-next-release-batch-a/Morning Radar v0.1/.tmp/b1/preview/site/index.html，可用浏览器直接打开。
- 本机已启动只读静态服务 http://127.0.0.1:8767/，仅监听 loopback、只服务上述 site 目录；PID 记录 `.tmp/b1/preview-server.pid`。这是本地预览，不是部署。服务退出后仍可直接打开 HTML。
- 每卡清楚区分：真实历史 raw + Fake 展示（补录日期仅演示）；已保存 10-07 生成产物（不是本次调用、不是原始模型响应）；完全 synthetic 的技术材料和后续报道。
- `preview-manifest.json` 记录来源路径、历史 case_id、已保存 Brief SHA256 和 paid_calls=0；`preview-brief.json` 为本次预览内容。
- 重建必须换一个不存在的 output。当前 checkout 未安装 editable 包，避免直接命中原工作区：在本 worktree 中使用现有 Python 执行 `-X utf8 -B -c "import sys; sys.path.insert(0,'src'); from morning_radar.cli import main; main(['preview-b1','--output','.tmp/b1/preview-next'])"`。

## 未验证与后续衔接

- 未验证真实模型选题/解释/归并质量，短引文校验不等于语义正确性证明。Fake 仅验结构、失败恢复和展示。普通新闻的中文质量仍需后续授权的真实模型样本评估。
- 未验证实时来源可访问性、网站改版、真实费用及整轮 0.4 元上限。PDF、JS 渲染、重定向、需登录内容、查询参数 URL 不在本补证器支持范围；unsupported/not_allowed 会保留状态。小候选池没有预审重要性标记时主要依赖既有 high priority；不保证所有重要材料都被识别和补齐。
- 未验证进程强杀后远端 artifact 必然上传。started 可供诊断，捕获式取消有测试；网络与文件系统本身完全失效时仍可能无法落盘。
- Tendency 后续 C 批可从独立证据适配器及事件关联读取材料，结合既有 raw/checkpoint 扩展独立发现和校准；本批不重写核心、不提前做编辑页面、不把 Tendency 移出整轮范围。读取器默认返回各 evidence_id 最新观察，全部历史快照保留；跨运行身份/关联校准由 C 批继续。
- 完整发布编排继续复用现有 publish/deploy/notify 回执链；B1 仅接入证据归档和可审阅预览，不代表整轮发布验收完成。
- 回滚：本地分支保持隔离即可；未来集成时 revert B1 提交（A 修复可单独 revert），不删除生产证据/历史、不回写旧判断。旧阅读器通过可选字段和默认值兼容。
