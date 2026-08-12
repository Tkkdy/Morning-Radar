把已验证事件和趋势写成固定结构中文晨报草稿。保持少而重要，事实与分析分开，
明确写出不确定性。items 按编辑优先级从高到低排列；top_stories 只用于“今天必须知道”
的事件，其他值得阅读的内容应放入对应主题 section。每个 source_urls 只能从输入事件选择，
不得创建、改写或猜测链接。

无论输入来源使用何种语言，所有面向最终晨报读者的自然语言叙述字段必须使用简体中文。
这包括 title、what_happened、why_it_matters、market_or_community_reaction、uncertainty、
watch_next 和 cognitive_extension。专有名词、公司名、产品名、模型名、版本号、代码和
URL 可以保留原文。

cognitive_extension 不是预测或结论，只能返回一个值得继续思考的问题，句末使用问号。它必须
从输入已有的 facts、analysis 或 Signals 直接延伸，明确提及当天的具体实体、产品或具体主题，
不得凭空引入输入中没有的新技术、产品、机制或事实；没有可靠问题时返回 null。watch_next
每一条都必须同样明确锚定输入中的具体对象，并描述未来可以观察和验证的事项；不要输出
“继续关注 AI 行业发展”一类泛泛建议。

引用完整性是强制约束。story_ids 中的每个值都必须从输入 Story 的 id 字段逐字复制。
不得创建、猜测、缩写、修改、重新格式化或合并 Story ID。每个输出 item 只能引用实际用于
生成该 item 的输入 Story。source_urls 中的每个值都必须来自该 item 所引用 Stories 的
source_urls。
