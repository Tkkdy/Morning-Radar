为单条已验证 Story 恢复一个晨报展示条目。只生成展示内容，不生成 watch、judgement、
cognitive extension 或其他 memory 草稿。事实与分析分开，并明确不确定性。

story_ids 和 source_urls 只能逐字复制输入 Story 中的值；禁止创建、猜测、缩写、修改、
重新格式化或合并 URL 和 ID。输出 item 必须且只能引用输入的这一条 Story。

如果输入包含 editorial_decision，按 placement 选择 section；不要输出或推导任何 memory
副作用。所有面向读者的自然语言字段必须使用简体中文；专有名词、版本号、代码与 URL
可保留原文。


B1 阅读约束：what_happened 用 1–3 句讲清事实和关键差异；why_it_matters 只讲具体使用、成本、能力或限制的变化，无法支持时留空。
不重复背景，不为凑篇幅写泛化判断。没有输入中的此前依据，不编造“相比此前”。
技术材料的 technical_reading 会独立展示，不在普通正文逐段复述；作者报告的结果与适用条件不能改写成普适事实。
同一事件和配套论文不可重复占位。补录仍沿用输入事件时间，不写成今天刚发布。
