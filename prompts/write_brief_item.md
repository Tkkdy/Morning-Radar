为单条已验证 Story 恢复一个晨报展示条目。只生成展示内容，不生成 watch、judgement、
cognitive extension 或其他 memory 草稿。事实与分析分开，并明确不确定性。

story_ids 和 source_urls 只能逐字复制输入 Story 中的值；禁止创建、猜测、缩写、修改、
重新格式化或合并 URL 和 ID。输出 item 必须且只能引用输入的这一条 Story。

如果输入包含 editorial_decision，按 placement 选择 section；不要输出或推导任何 memory
副作用。所有面向读者的自然语言字段必须使用简体中文；专有名词、版本号、代码与 URL
可保留原文。
