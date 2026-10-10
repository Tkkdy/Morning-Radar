# Morning Radar C 批 Tendency 核心交付

状态：本地实现、隔离离线验证与演示完成；未集成生产，未证明真实模型发现能力。

## 基线与现场

- 实际开发基线：`b7a48efcd67d5afb0672f95612418a9661666207`，开工时工作区干净。
- 已核对祖先链：A `bb3ef351671bc5fcd54eed789e5c3c4306db5be0` → A 复核 `e507d1041cdabfbce1ea3191f9ae8883a7cf4424` → B1 `b7a48efcd67d5afb0672f95612418a9661666207`。
- B1 的 daily workflow `data/evidence/` 最后归档接线已包含在基线，不存在漏带的未提交接线。
- 新分支：`codex/radar-next-release-tendency-core`。复用隔离 worktree：`C:/Users/PsyDuck/.codex/worktrees/radar-next-release-batch-a/Morning Radar v0.1`，没有从 master 重开或覆盖旧分支。
- 最终 C 提交为包含本报告的本地提交，完整 SHA 随最终交付返回。
- 读取了本地 B1 完整报告、C 任务书、产品基线及项目规则；只检查 C 接入的来源、事件、预算、Provider 和存储接口，未发起全仓审计。

## 实际链路与复用

`CLI/核心 API → 保存投递及回执 → 独立证据读取 → 有界选择 → discover_hypotheses / calibrate_hypotheses → 逐项结构与引用校验 → 带 expected predecessor 的追加事务 → 当前视图/历史导出`

- `tendencies/inputs.py` 读取 B1 evidence 的各版本、已保存 intake checkpoint（包括未形成 Story 的材料）、B1 事件关联与本地用户材料。没有 Brief、新闻分数或最终入选门槛。来源时间、观察/抓取时间、入库时间分别保留；以可知时间截止，未知发布时间保持 null。
- 适配时保留 B1 未补齐缺口、unverified/opinion/marketing 等来源属性；失效/未验证材料不能作为支持或反证。B1 后一次失败不能遮蔽先前有效摘录；损坏事件文件记录局部诊断。没有另造下载器，也没有自动抓取实时网页；有界网络补证仍复用 B1 接口产物。
- 内容版本包含摘录摘要。相同摘录重复导入/镜像 URL 作为同一观察，不按 URL 数量计独立验证；同一事件不同后续内容可以进入校准。所有修订保留实际使用的证据快照，后来的抓取不替换旧依据。
- 检索按新增观察和约束/方法/失败等正文内容匹配，保留背景及反例上下文位置。它是可解释的有界词项检索，不是向量库，也不保证找到所有语义相关材料。请求记录选择作用、范围、缺口、截断和目标清单。
- 发现先执行，校准和投递解析有独立状态。没有旧假设也会执行发现；没有方向可正常返回不足或无变化。大历史库先压缩登记摘要和每次目标，避免挤光新增证据；已有方向按最近检查时间轮转，未处理目标显式列为 pending。
- 正式 DeepSeek/OpenAI 路径已接入，各自复用原 `_parse`、Pydantic、网络额度与 UsageRecorder；新增三个任务 discover/calibrate/split。当前每任务最多一次结构请求，不做无边界恢复；失败和预算耗尽保存为可再次处理状态，真实重跑仍产生新的调用记录。
- 默认 Fake 不内置发现答案，返回缺口/无变化。演示专用 Provider 使用明确标注的作者预制输出，只用于穿过正式编排、校验和持久化链路。

## 契约与校验

新增 v2 契约位于 `tendencies/core_models.py`，旧 v1 类型、daily 文件及 `run-tendency` 保留兼容。C 的新入口为 `tendency-core`，最终生产切换留给 B2。

- Evidence：稳定引用、输入与摘录版本、事件 ID、来源 URL/性质/角色、明确范围、三类时间、缺口与 synthetic 标识。用户个人观察附个人范围前缀，不自动推广为行业行为。旧系统判断和用户主张不能变成新外部事实。
- Hypothesis：命题、范围、原状态/可能新状态、旧限制（依据或假定）、条件观察与推断、联合作用、缺口、其他解释和可区分的后续观察。无精确概率字段，不设三个事件/两个日期通用门槛。
- Decision：支持、增强、削弱、收窄、分支、推翻、无变化、分析重评估、用户纠偏和归档。引用必须指向输入中真实存在的短引文及具体条件；范围不能超出支持材料声明范围；收窄/分支要说明放弃部分；推翻需反证。
- Revision：持久 hypothesis_id、新 revision_id、previous_revision_id、分支 parent 及其版本、首次记录/本次记录时间、起源、原投递 seed、原因、证据快照、run_id 和模型/提示词摘要。累计观察指纹避免某材料从上一版引用中消失后，被重导入伪装成新增增强。
- 无新增现实依据时不生成“外部增强”修订；有实质分析变化须标 analysis_reassessment。用户纠偏必须来自真实投递并匹配期望版本。归档不等于推翻。
- 字段/引用/范围校验不能证明语言模型对机制、证据独立性或隐含范围的理解正确。语义重述的完全去重仍依赖模型判断与上下文，不能宣称离线测试已经解决所有同义命题问题。

## 用户输入与持久化

`submit()` 首先保存原文、身份、真实提交时间、submission ID、材料引用及可选纠偏目标，再拆分；失败不能丢原输入。

- 明确结构输入使用原文字符 start/end span；自由文本由正式 split Provider 拆分。无效项独立标记，未覆盖原文保持 partial；成功条目的稳定 ID 按 submission + span 产生，重排/重试不改写已接受条目。
- 同 ID 重放幂等，有冲突则拒绝；相同文本的另一次有意投递获得不同 submission ID。
- receipt 分开显示原文/拆分状态及逐条 analysis（unassessed/recorded）；“拆分成功”不等于已形成有依据的方向。
- 纠偏作为新投递关联目标与期望前一版本，经核心运行生成 user_correction 修订；迟到更新不能覆盖后来版本。
- `data/tendency-core/v2/transactions/` 为原始投递、回执及修订的追加事务。复用 atomic_json；OS 单写入锁，提交时检查前一版本及新命题重复，派生当前视图，不覆盖旧命题。临时写入失败不损坏已提交历史。
- `data/tendency-core/v2/runs/` 保存运行进度、实际输入清单、合法输出、逐项校验结果与物理调用 ID。非法响应仅保存摘要/长度和失败状态，不保存任意全文；运行进度可更新，判断历史不可变。
- 旧 `data/tendencies/*.json` 只读导出为 legacy_v1_unmigrated，不静默重写或升级成熟度。原 `status.json` 无接口修改。

## 配置、预算与健康

修改前已说明配置影响。`config/app.yaml` 新增 tendency_core：每类最多扫描 200 文件、最多 200 证据、12 个请求候选、4 个校准目标、每阶段 8000 输入字符。旧配置省略该块时使用同样默认值。

仍受既有 Tendency 总 3 次逻辑调用、24000 输入字符、4 次网络请求限制；没有提高生产预算或换模型。提示词/schema 也会产生实际 token，不能用输入字符上限估算实际费用。

沿用 A 的 module_run/usage_summary，模块名 tendency、同业务日汇总；物理调用 ID 关联到阶段，重复计量导入去重、真实重跑累计。usage/价格缺失保持 unavailable/null。本批没有价格或真实账单验证，不能宣称每日 0.4 元达标。

发现、校准、投递分别报告 no_input、insufficient_evidence、no_changes、completed、partial、budget_skipped、provider_or_validation_failed 等；整体不能用某一成功掩盖另一项缺口。捕获取消留存运行记录。无变化可以有检查记录，不制造命题修订。

C 没有修改 workflow、定时、模型、Secrets、Editorial/AIHOT 开关、部署或通知策略。未 push、merge、触发 Actions、部署、通知或调用付费模型。

## 实际验证与边界

所有 pytest 拒绝 socket.connect/create_connection；Fake/Mock 传输，Python -B、禁用 pytest cache，basetemp/日志/JUnit 在 `.tmp/c-core/`，未安装依赖。

| 检查 | 实际结果 |
| --- | --- |
| C 修改前相关基线 | 49 passed in 4.17s，baseline.log |
| 首轮核心 | 暴露新循环导入，收集失败；移除 Provider 的运行时类型导入后 25 passed |
| 扩展核心及旧接口 | 69 passed in 6.35s，targeted-3.log |
| 完整 pytest | 688 passed in 44.59s，full-final.log / full-final.xml |
| 完整测试后的 CLI/纠偏收尾 | 新测试曾因缺少 CoreOutput 导入出现 1 failed / 33 passed；修正测试导入后 34 passed |
| 后续直接 Demo API 隔离保护 | 2 passed / 33 deselected；同进程先加载 CLI 引起一个 anyio assert-rewrite warning，无失败 |
| B1 缺口字段接入 | 核心+补证接口 47 passed in 6.71s，delivery-final.log / XML |
| 最后写入锁内重复命题保护 | 全部当前核心 36 passed in 4.54s，store-final.log |
| 最终 Ruff / git diff --check | passed |

没有把“688 后局部改动的复测”描述为“最终代码再次全量 688”。完整测试后只改了纠偏回执关联、提示词换行、直接 demo 输出隔离、B1 缺口字段适配和写入锁内重复检查；对应核心/接口覆盖已重跑，其余证据复用。

测试检查了引用和内容性质、范围、修订关系、首次时间、跨进程回执读取、写入失败恢复、部分解析、重复输入、真实后续、反证收窄/推翻、分支、分析重评估、迟到纠偏、Provider 正式请求/解析、missing usage 和重复账单导入。生产模块配假传输不证明真实模型推理能力。

`.tmp/c-core/protection-verification.json` 记录原工作区 HEAD/status/diff、已修改文件字节和两边生产 data/site 哈希均未变；原分支、未跟踪资料及本地独有提交保留。未运行真实流水线。

## 连续演示与本地操作

最终演示：`.tmp/c-core/demo-final/trajectory.md`，同目录 `trajectory.json`、`operations.json`。

全程 synthetic 港区传感器案例，不使用用户五个产品正例：

1. 唤醒能力与缓存补传的分散变化 → 首次 Early，范围甲/乙两站；保留长期可靠性缺口。
2. 同一试验的新运行记录 → strengthened，保留同一 hypothesis_id，引用最初 revision。
3. 高峰拥塞反例 → narrowed，只保留低负载甲站，明确放弃乙站范围。
4. 当前视图与完整历史同时导出，可回看第一版的原范围、假定、理由及当时依据。

演示 Provider 是预制文本，输入与修订都带 synthetic；paid_calls=0 只描述这份演示，不是生产成本评估。

同目录 `submission-example.json` 是明确标注 synthetic 的两条结构投递。已实际通过 CLI 保存到 `.tmp/c-core/local-input` 并重读回执，见 `submission-receipt.json`、`reloaded-receipt.json`。它们已拆分、尚未被当成真实判断。

在本 worktree 的 PowerShell 中，可使用原 venv 的只读解释器（editable 安装指向原工作区，因此显式加当前 src）：

```powershell
$radarPython = 'C:/Users/PsyDuck/Documents/Morning Radar v0.1/.venv/Scripts/python.exe'
function Invoke-RadarLocal {
    & $radarPython -X utf8 -B -c 'import sys; sys.path.insert(0,"src"); from morning_radar.cli import main; raise SystemExit(main(sys.argv[1:]))' @args
}
Invoke-RadarLocal tendency-core submit --workspace .tmp/c-core/my-input --file .tmp/c-core/demo-final/submission-example.json
Invoke-RadarLocal tendency-core receipt --workspace .tmp/c-core/my-input --submission-id synthetic-local-batch
Invoke-RadarLocal tendency-core run --workspace .tmp/c-core/my-input --evidence-root .tmp/c-core/my-input --provider fake
Invoke-RadarLocal tendency-core show --workspace .tmp/c-core/demo-final --evidence-root .tmp/c-core/demo-final --format markdown
Invoke-RadarLocal tendency-core show --workspace .tmp/c-core/demo-final --evidence-root .tmp/c-core/demo-final --format json
```

`show --hypothesis-id <operations.json 中的 ID>` 查看同一方向及全部旧版。重新生成轨迹使用 `tendency-core demo --workspace <一个全新隔离目录>`；不覆盖已有演示。自由文本投递省略 entries；材料可指定 source_claim/personal_observation/user_hypothesis、text、scope、原始 url 及已知时间。纠偏附 correction_of、expected_revision；回执保留 pending/partial，后续 run 消费。

`--provider fake` 是默认值，不产生预制发现。正式 `--provider deepseek` 与核心 API 已接通，但本批没有执行；真实模型和限定费用验证等待另行授权。只做本地受控文件写入，不向静态页面放任何凭据。

## 未验证、风险与后续

- 未验证真实模型独立发现、机制推理、语义去重、范围理解、反证归因的质量，也未验证“早于共识”。禁止以 synthetic 结果替代。
- 未验证实时来源可达性、模型费用、整个产品日预算。读取保存材料不等于完成实时有界补证；新缺口保持诊断，B1 后续补证结果可被下一轮读取。
- 检索有文件/候选/字符上限，词项可能漏掉语义关联；范围默认使用来源域名或明确声明的观察范围，较保守。超限目标/投递保留待处理，不保证一次运行穷尽历史。
- 相同引用存在只保证可追溯，不保证模型解释正确；同义命题与非逐字转载的独立性仍需真实样本评估。断电、远程文件系统故障、真实模型错误分布未验证。文件级锁只服务本地单体，不是分布式协调器。
- **D 批仍须完成**：独立列表/详情/历史和批量编辑 UI、受保护的写入入口；复用 submit/receipt/store/expected revision 契约，实现刷新/关闭后真实回执、追加材料和纠偏的前台端到端验收。
- **B2 最终整合仍须完成**：生产编排切换至 v2 核心，统一晨报+Tendency 健康/费用/发布回执，提交及失败 artifact 包含 data/tendency-core，统一构建与一次通知策略；按另行授权进行限定费用的真实来源/模型与完整交付验证。C 不宣称该编排已经上线。
- 回滚：本地分支未集成即可隔离；未来可 revert C 提交，原 v1 数据/命令保留。保留新追加历史和原始投递，不删除或倒填判断；无需数据库迁移。
