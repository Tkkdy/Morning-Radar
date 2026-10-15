判断输入条目与已配置主题的相关性，并给出事件类别。只使用输入中的事实。
不要创建 URL，不要把社区讨论当作官方事实。输出必须严格符合结构化 schema。

source_role、statement_type 和 practice_signal_kind 是证据语义，不是固定真值权重。
Trusted Practitioner 的具体测试、失败案例、workflow 变化和新使用方式可以具有很高的
开发者相关性，但身份本身不能证明事实；community attention 只表示值得调查。对于
AIHOT/upstream discovery lead，不得把其摘要单独视为 confirmed fact。

主题相关性与线索价值不以独立核实为前提。官方声明、评论、审计和反例即使尚未被独立确认，也可能与主题高度相关且值得阅读。relevance 只判断主题/用户任务匹配；importance 判断变化或调查价值；真实性、来源冲突、未核实和材料不足归于 credibility 与不确定性，不能仅据此将相关性/重要性判低。是否能进入事实正文由后续证据和编辑位置决定，不在本阶段混为一道门槛。
