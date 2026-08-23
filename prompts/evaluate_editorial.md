应用输入中的 Editorial Profile 和 Golden Cases，对完整 Story 批次逐条做编辑判断。
必须为每个输入 Story 返回且只返回一个 decision，不得添加、遗漏或修改 story_id。

只判断输入证据：Story 通过结构与 URL 校验不等于其中所有主张都已验证。官方一手来源可以
验证“官方确实发布产品、修改价格、修改许可证、公布政策”这类可由官方行为本身确认的客观
事件；可靠 market source 可以验证股价、成交量等客观数字。这些事件允许 verified_fact。
但是厂商不能单独验证自己声称的模型性能、Coding 能力、可靠性、benchmark 优势或实际效果；
这些能力主张在没有独立测试、复现或 Practitioner Evidence 时必须保持 claim。事实与推断混合
时使用 mixed。不得使用模型自身知识补造验证状态。context_snapshot.independently_verified 只有
在输入中确有独立证据时才能为 true。

先隔离今天真正发生的 news delta，再独立判断 reader_value 与 evidence_value。不得生成一个
加权总分，不得因品牌光环把小更新提升为重大事件。重大且已知的开发者事实可以 TOP +
short_news；未验证 benchmark 只能作为低读者注意力的 claim 保留。

reader placement 与 evidence retention 必须独立判断。retain_for_trends 不控制 Story 是否保存；
所有 Story 都会进入原有存储和趋势历史。它只表示这条 Story 是否应成为当前或未来 Trend、
Tendency 或 Prediction Evaluation 的显式证据。以下信息通常应保留：支持、削弱或反转已有
趋势；可能在后续 adoption、复现或失败后变得重要；记录技术能力、开源、本地部署、许可证、
价格、可靠性、基础设施、市场结构、安全或政策实际影响的演化；能验证过去判断、预测或因果
解释；以及可信但前台只值 ONE-LINER 或 DROP 的 weak signal。装饰性更新、普通维护、无结构
意义的宣传、已完全解决且没有持续后果的短暂故障，以及无法命名潜在趋势或未来验证用途的
低价值噪声，通常不保留。

跨字段必须一致：retain_for_trends=true 时 trend_links 非空；false 时 trend_links 必须为空。
evidence_value 为 3 或 4 时必须保留，为 0 或 1 时不得保留，为 2 时按具体情境判断。
decision_reasons 包含 trend_confirmation 时必须保留。保留时 trend_links 必须清晰、具体、可
复用，能命名所跟踪的机制或变化，不得使用“AI 发展”“行业趋势”等空标签。不得在 why_now、
news_delta 或 uncertainty 中声称“保留为趋势线索”，却输出 retain_for_trends=false。

SUPPORT 必须指向本批中的一个非 SUPPORT、非 DROP Story，不能指向自身。SUPPORT 只补充目标
Story，不能成为独立新闻。TOP 不必然保留，DROP 也不必然丢弃后台证据。不要创建或输出
URL。所有叙述字段使用简体中文。
