# 真实验收校验补丁收尾（2026-10-11）

本轮为免费离线修复与冻结材料回放，不是新的真实模型验收。

## 代码边界

- 基线：faaa6992720b375f09a5392b6674474127b42ede，包含既有修复与报告；未回退旧 master。
- 代码提交：d4e0d7a63cb31d2b0091b2dcd9e20c81c359981b；分支 codex/radar-validation-followup。
- 工作树：现有 radar-next-release-batch-a/Morning Radar v0.1；原工作目录未施工。
- 使用原目录 .venv/Scripts/python.exe，但 runner 显式优先加载当前工作树 src；日志记录实际导入路径。未安装或修改环境。
- 改动仅涉及 tendencies/core_validation.py、core_models.py、inputs.py、两份 C 提示词及相关测试。没有 workflow、生产模型、预算配置或来源域名配置改动。

## 修复对照

| 问题 | 修改前 | 修改后与边界 |
|---|---|---|
| inferred 支持修订 | supported/strengthened 一律拒绝 | 真正新增的支持证据可追加修订；保持 claim、scope、scope_basis 与先前 unresolved，不自动变 observed/active；重复 ID/相同摘录不增加修订，只有反证或背景不能增强 |
| 否定句 | 关键词被当作肯定断言 | 在分句内识别“不能断言”“不应被一概判定为”等否定；两条指定句通过，019 原两条过强断言与“但是”后重新肯定仍拒绝 |
| Codemode 截断 | 只取前 700 字符，漏掉关键限制 | 在原 700 字符上限内选择真实条件/限制句，理论替代句保留相邻主语上下文；记录版本、冻结摘录 SHA256 和字符区间；不允许跨省略处引用 |

正式模型、Provider 解析、引用/来源/版本校验和 CoreStore 追加事务均继续使用。synthetic 正反例验证落盘与重开，首版不变。原始 019 通过既有格式兼容后仍被语义规则拒收，没有修改它来求通过。

保守取舍：支持修订必须保留旧 unresolved 条目；不能借 supported/strengthened 悄悄删掉缺口。否定检查仍是有限语言规则，片段选择仍是启发式；二者均不代表全面语义正确性保证。

## 实际验证

- 最终相关回归：76 passed，5.92 秒；含 17 个新增用例，正式 Provider 使用 synthetic HTTP 响应，不使用 Fake Provider 绕过失败点。
- 最终完整 pytest：770 passed，0 failures、0 errors、0 skipped，152.619 秒，退出码 0。JUnit 是完整计数依据；项目 -q 与命令 -q 叠加使日志不打印统计尾行。
- Ruff 全仓通过；git diff --check 通过。
- 初轮相关 74 passed/2 failed 保留在临时证据中；分别为缺少理论替代句、旧整段子串断言不适用于分段摘录。修正后以逐段原文/位置一致性断言检查。末次相邻上下文修正后重新完成上述完整测试。
- 测试封锁非本机网络；请求冻结脚本封锁所有 socket，正式 Provider 在 MockTransport 边界捕获请求后终止，没有模型响应。选材回放的零调用预算只是离线截获护栏，不是项目余额判断。
- 原工作区 HEAD/status、两工作区生产 data/site、首次失败 ZIP、前轮回放 ZIP、response-019.json 哈希不变；protection.json 全部为 true。
- 本轮新增真实调用 0、费用 0，未产生项目 usage。没有外部访问、push、部署、通知、Issue 或 Secrets 操作。

## 冻结的最小复测请求

包内 frozen/request.json 是正式协调器基于原冻结材料产生的业务上下文；discovery-wire.json 是正式 DeepSeekProvider 生成的完整消息正文，未发送。freeze.json 保存版本与文件哈希。

- 观察截止：2026-10-10T16:13:24.454289Z；未采集新材料。
- 扫描 120 文件、77186 checkpoint 行；5686 去重记录中可见 200 条，含 29 条本期首次采集。可见上限仍然生效，不宣称所有材料均深入处理。
- 实际请求 7713/8000 字符，5 条引用材料（其中 3 条本期首次采集），9 条概览。概览明确仅提供发现线索，不可引用；其中一条 unverified 材料仍不能验证或反驳条件。
- Codemode：696/700 字符；content_version=3873ead23c379431c65454b2；源冻结摘录 SHA256=d571a750924fe42bd3339787f34a8dbc8ffee3d485a8e1ab57a42f45d51c2824。
- 片段区间 [0,160)、[1148,1244)、[1407,1570)、[1615,1877)，为源冻结 2000 字符摘录内 Unicode 字符位置，不是网页全文或字节位置。依序保留介绍、可运行程序限制、图像协议条件、子 agent 编排与 CLI/socket 理论替代方案，未人工编造结论。
- 请求 wire SHA256：6ba9f8cf92459054ef81d759333a8fcc6f2ed650e14ce46b1d3f38239607f838。
- 提案不变：至多一次 discovery；deepseek-v4-flash、thinking disabled、6000 最大输出、一次网络尝试。价格、当日余额和全输入预留需执行时重新核实并另获付费授权，本包不是调用授权。
- 仅合法首版产生且存在实际相关新证据时，最多一次 calibration；必须绑定届时真实 hypothesis/revision 再冻结请求与预留。当前没有伪造一个已获接受的目标或 calibration payload。无合适材料就标未验证，不重复相同证据求增强。

## 包与剩余边界

ZIP 内 report.md、tests/、frozen/、protection.json、版本清单及 runner 可独立查阅；均为文本/JSON，无虚拟环境、凭据、生产历史副本或旧包的重复拷贝。冻结请求材料来自真实历史，测试模型响应是 synthetic，本轮模型未执行。

真实发现/校准质量、新闻和技术卡的真实生成效果及上轮其余未完成验收仍未验证；本轮没有追加付费试跑，没有扩大验收批次。无需新外部访问即可准备以上一次 discovery；任何内容补证或付费复测须另行具体授权。

回归复用命令：在当前工作树用已存在解释器运行 scripts/test_runner.py tests/unit/test_validation_followup.py tests/unit/test_real_acceptance_repair.py --basetemp=<新的隔离目录>。请勿把 discovery-wire.json 直接交给无预算保护的网络脚本。

如需回滚，仅 revert 本轮代码提交并保留档案；未迁移历史。新增摘录字段有读取默认值，回退旧代码前应保留新版本独立修订档案，不能要求旧代码识别新字段。
