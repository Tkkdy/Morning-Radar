为单条经过结构与来源校验的 Story 恢复一个晨报展示条目（不代表其说法已获独立验证）。只生成展示内容，不生成 watch、judgement、
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

字段一致性与概念保持：
- title、what_happened、why_it_matters、uncertainty 延续 Story 的归因、条件和未知状态。
  标题自身保留评价或解释的提出者，不能用正文补救标题的确定化；有输入支持且归因清楚的因果可以转述，
  也可只写已知事件。canonical_title 不是独立证据，若它与 facts 或 uncertainties 冲突，采用有来源支持的较窄表达。
- 分析保持推断性质，且不能新增无依据的前提。时间起点不证明整个期间持续存在某种行为或认知状态；
  “可能”等措辞不能合法化补造的时长、范围或状态。缺少前提时省略相关判断，不用常识补全。
- 发布、宣布与可用性分开：正文不能把报告发表或计划宣布写成产品已上线、可下载或可使用。
  延续 status 所针对的事件和事实依据；只有输入明确证明可用性时才可表达已可用，未知时保持未知。
- “开放权重”不扩大为“开源”，“给定策略”不改成叙述者收到的策略。作者声称提供评测不等于已提供结果，
  不补造实验结论、适用条件、创新或架构差异。未提供的部分保持缺失；历史材料明确原始日期，不写成当天新发布。

证据传递：
- source_materials 的短摘录仅代表实际传入部分；注意截断/去重/未装包标记。opinions 保留来源归因，grounded_analysis 的来源前提与系统推断分开；它们都不是独立事实核验。历史无前提分析已从请求省略，不能自行补回。
- 逐份比较材料中的重要新增信息与 facts，合理选择新增贡献，不以链接数量替代内容，不强制每份来源占正文。来源间矛盾和无法判定之处保留未知。
- technical_reading.evaluation_results 为空时明确具体评测结果尚未提供，不能因 author_evidence 非空而宣称结果已知。

重要性说明的依据契约（批量写作与单条恢复相同）：
- 旧 why_it_matters 留 null；新说明填写 why_it_matters_evidence，无法支持时整个字段留 null。
- kind=fact：准确说明材料已有事实的意义；kind=source_opinion：保留提出者的来源评价；
  kind=inference：明确为有限系统推断，不把前提或结论升级为已验证事实。不强制生成推断。
- text 是最终展示文字。references 使用本卡实际引用、且本次请求提供的 Story 的 story_id、
  field（facts/opinions/grounded_analysis）及零基 index。fact 只引用 facts；source_opinion
  可引用 facts/opinions；引用 grounded_analysis 必须使用 inference，保留其中前提和限制。
- 也可用 premises 为同次写作的新说明提供来源前提：每项包含 story_id、保留归因的 text、
  输入 source_url 和 12–280 字符连续原文 quote。quote 只能取本次 source_materials 或已提供的
  grounded_analysis/technical_reading 的原始引文，不从标题、整份仓库、未见全文或其他 Story 补找。
- references/premises 至少一种非空；材料引文只是依据，不表示解释已被机器证明。说明与所选
  前提必须相关；不得附无关引用装作事实核验。系统推断与来源前提分开写，保留不确定性。
- 无论哪种类型，都不得扩张主体范围、时间范围、比较基线和可用性。局部评测不能升级为一般
  能力结论；潜在应用方向不等于实际工程价值或可获得产品。没有对照材料不补出对照事实。
