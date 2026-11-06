# Tendency 审查边界定点修复

## 结论和基线

仅修复独立复核提出的两处工程边界，无新功能批次。实现提交 `97e153d43b7f5d1833d9681e9a3e348c0d27471d`；基线 `fede7ee10e9dbb78ecb7d30c9a8fcdecf3e2356f`，包含 `da1808b1…` 及全部既有修复。分支仍为 `codex/tendency-grounded-review`。报告另存后续本地提交。

本轮真实模型调用、外部访问、费用均为 0；未修改生产数据、旧事务、三个冻结候选、提示词、公开准入合同或预算配置。未 push/发布/通知/操作真实 Issue。

## 修改位置与行为

1. `src/morning_radar/tendencies/grounded_review.py:249`：将单候选完整审查上下文与配置总字符上限比较。单项超过总上限记为 `input_too_large`，原请求、正文、证据、先前失败事务和具体大小原因原样保留，不再自动重试；不截断、不重新生成。`batch_deferred` 表示当前批次字符容量或候选数量容量不足，可后续恢复；正式 Provider 的临时调用/输入/费用预算不足仍为 `budget_skipped`。`pending_candidates` 兼容旧 budget_skipped 并保留 batch_deferred。
2. `src/morning_radar/tendencies/core.py:84`：恢复扫描如果只有已终止候选、没有发起请求且没有可重试待项，保留 `content_review_recovery` 诊断并继续正常输入流程；有实际恢复请求时仍遵循有界恢复路径。新候选审查同时传入本轮剩余容量和配置总上限，防止把“本轮剩余不足”误判为永久超限。
3. `src/morning_radar/tendencies/grounded_review.py:59`、`:272`：响应容器先保留任意 JSON 项，再逐项识别对象与明确 candidate_id。无身份/非对象/未知身份项保存索引、原值和错误；索引只用于诊断，不用于身份配对。合法候选继续经过原 Assessment/Pydantic、正文/证据哈希、契约版本、前版、字段覆盖、原句和引文校验。缺失或重复身份仍拒收，CAS 与公开准入规则不变。

对模型发布的 JSON schema 完全不变，仍要求对象数组且最多 12 项；仅接收解析器对坏项隔离。即使额外坏项使返回数组超过 12，也不能抹掉有效兄弟项；返回内容仍受既有最大输出/网络边界约束，未知身份不产生修订。

## 新增回归与最终结果

- `tests/unit/test_grounded_review.py:431`：连续两轮，覆盖新待项和旧 budget_skipped 待项。第一轮将始终超过 24k 的候选记为 input_too_large，不调用模型；下一轮读取新增隔离 B1 材料，通过正式选择、Provider、验证、审查和事务形成新公开修订。旧候选全部正文/证据及失败历史保持不变且不进入请求。
- `:489`：批次容量不足和当前剩余为 0 都为 batch_deferred，后续运行可恢复，未改总额度。
- `:519`：合法项 + null/字符串/缺 candidate_id/空身份/错误身份类型 + 合法项，覆盖合法兄弟双接收及独立拒收。未返回结果的第三候选仍 invalid_review，证明没有按数组位置绑定。每例仅一个 HTTP mock 请求，原坏值、原响应和错误持久化。
- `:543`：公开请求 schema 保持原样，解析器能隔离多余坏项。
- 定点回归最终 **79 passed in 4.18s**。初期一项测试复用了同一命题，正式去重正确触发冲突；改为不同 synthetic 命题后通过，未放宽去重规则。
- 最终全套 **810 passed in 140.89s**；Ruff `All checks passed!`；`git diff --check` 通过。测试显式导入当前 worktree src，输出到独立临时目录；外部 socket 连接被阻断，既有本地页面测试允许 loopback。
- 原019、inferred 支持增强/重复支持、否定句、已准入旧版保护等既有回归均在完整测试中通过。
- 18 项哈希/保护核查通过；原失败 ZIP 和三个冻结候选/请求未变化。当前正式 Provider 在 HTTP mock 边界生成的 review wire 与原冻结 wire 完全相同。

这些测试验证工程接线与边界，不证明真实审查模型的判断能力。

## 证据与后续关卡

本地证据在 `.tmp/gr-boundary/`：`full.log`、`full.xml`、`ruff-final.log`、`diff-check.log`、`protection.json`。补丁交付包保留这些证据与差异，不覆盖此前 ZIP。原完整冻结请求仍在 `.tmp/gr/package-final/frozen/`。

修复完成后可申请原定的至多一次真实 review：仅三个原冻结候选、同一正式消息、最大输出 6000、最多一次物理网络请求（含 SDK 重试），仍经正式预算/记录/校验/事务；先核实当日全项目已用量和官方价格，未知或不足即停止。只读价格核对及此次付费调用须另行授权。

不自行执行 discovery、calibration、新采集、整期重跑或远端验收。真实审查结果与实际费用仍未验证。永久超限候选保留可见终止原因；本轮没有新增人工缩写或自动拆分流程。
