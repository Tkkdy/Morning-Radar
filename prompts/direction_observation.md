仅当输入中存在跨日、多来源或多公司证据时生成方向观察；证据不足时返回空 observation。
不要预测价格，不要给投资建议，不要为了填充栏目生成哲学内容。

无论输入来源使用何种语言，所有面向最终晨报读者的自然语言叙述字段必须使用简体中文。
这包括 observation 和 uncertainties。专有名词、公司名、产品名、模型名、版本号、代码和
URL 可以保留原文。

只选择一个证据能够共同支持的 coherent theme。不要把互不相关的 Signals 或 topic 拼成
“行业协同”。非空 observation 必须列出实际使用的 evidence_story_ids，且这些 Story 必须
来自同一个输入 Signal；证据不能形成单一清晰方向时，返回 observation=null、空证据列表。
