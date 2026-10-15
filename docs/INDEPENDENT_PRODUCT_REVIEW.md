# Morning Radar 独立产品审查导航

本分支是 Codex 执行的工程快照整理，不表示产品审查通过，也不是用户手写实现声明。

## 版本

- 来源基线：`5b298d43119d3f1633dc90979cded9390598cbc8`（E2/E3）。
- 唯一父提交：`41f95d706f86e5f7066a3e6b2dca17381e4ece49`（已公开 PR #23 HEAD）。
- 分支：`review/r1-product-reset`。17 个本地提交被汇总为单一审查提交；不沿用其本地历史。
- 相对来源仅将 9 份历史 handoff 和 1 份 fixture 中的本机用户目录前缀替换为 `<LOCAL_HOME>`，并新增本导航。
- 业务代码、模型/生产配置、测试逻辑、prompts、templates、data/site 均与来源基线逐 blob 相同。
- 父提交的公开历史继续可达；本次脱敏不删除或重写已公开历史。

## 审查任务

独立判断当前产品目标、真实阅读价值、复杂度与证据充分性；同时寻找支持和反对既有诊断的证据。不要预设重构、继续调提示词或现有验收结论正确。

本次仅审查，不授权修改、收费调用、采集新闻、推送、合并 PR #23、修改 master、部署或通知。

## 阅读顺序与文档边界

1. `docs/PRODUCT.md`：历史产品目标，存在早期 Provider 等过时表述。
2. `docs/ARCHITECTURE.md`、`docs/DATA_MODEL.md`、`docs/AI_PIPELINE_V2.md`，随后核对实际代码与 config。
3. `src/morning_radar/intake/`、`processing/`、`editorial/`、`briefing/`、`research/`、`release/` 及对应测试、prompts、templates。
4. `tests/fixtures/editorial_alignment/`、`tests/editorial_preview.py` 与离线入口 `tests/run_offline.py`。
5. README、PROJECT_STATUS、HANDOFF 与 `.ai/handoffs/` 是跨阶段历史材料，其 PASS/READY 不能自动继承到当前产品。

## 已有证据与尚未通过的验收

- E2/E3 来源记录：22 个相关测试文件，223 passed / 0 failed / 0 skipped，Ruff 通过。此为既有工程证据，不是本次重新运行的全套测试。
- E2/E3 状态是 `OFFLINE_PATCH_READY`：固定人工 Editorial decisions + Mock 写作。页面证明接线/序列化/模板与部分保护逻辑，不证明自动新闻评价、文案质量或事实准确性。
- 真实模型选材、默认预算下召回、日常用户价值与负责人阅读验收仍未由 E2/E3 证明；生产 editorial 保持 shadow。
- A2 参照由 Codex 标注，owner_confirmed=false。召回审计针对历史输出和候选机会，不能直接当成当前实现失败或行业重要性真值。
- 历史 minimal-real-news-01 为真实模型 PARTIAL_FAIL：归并/写作错误否认输入中的 Muse Wonder Full，并把内部诊断当作事实。随后补丁不等于真实复测通过。
- 真实 news-2 是历史特别配置运行；人工 reader-draft、A2 编辑候选及 E2/E3 固定样稿均须分开阅读。不得把人工编辑稿或 mock 正例称作自动生成成功。
- 既有收费/预算记录只说明过去，不是新调用授权或可用余额。

## 需在新对话另行上传

GitHub 包含代码、产品文档、测试及 fixtures；忽略目录内的以下材料不在分支中：

- `01-e23-offline.zip`：脱敏验证报告、原测试日志/JUnit、固定 Editorial 样稿、HTML 和原截图（明确 manual/mock）。
- `02-real-news-and-failure.zip`：历史真实 news-2 阅读样本及代表性 minimal-real-news-01 的输入、原响应、校验输出和 PARTIAL_FAIL；图片如有注明其渲染来源。
- `03-a2-recall.zip`：重要召回参照、lineage、historical coverage、候选、验收与必要原始窗口记录。
- `review-materials-manifest.json`：逐文件来源标签、脱敏方式、原件/分享副本 SHA256；原件本地保存，脱敏副本不声称逐字节原响应。

这些压缩包仅供用户手动上传，未发送到 GitHub 或其他服务。不要整包上传 .tmp、浏览器状态、环境、密钥或历史日志。Pages 发布来源必须单独核实；仅有无 push 触发器不足以确认仓库设置。
