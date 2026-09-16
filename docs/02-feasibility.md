# 可行性评估

> 评估于 2026-09-14，整理于 2026-09-17。市场和价格信息变化很快，引用处见第 6 节参考资料。

## 1. 结论

**技术上可行，而且已经有人做成了；产品上也可行，但范围必须收窄。**

- 这条链路（需求 → 沙箱 → 大模型写码、测试、修复 → Git → CI → 制品或 Pages）已有很多产品在做，一个小团队几周就能把流水线串成 demo。
- 真正的难点在三件事：
  - 成功率：项目稍复杂 agent 就会卡住，而目标用户没有能力接手。
  - 成本：token 贵，失败的尝试也要付钱。
  - 差异化：模型厂商和代码平台都亲自下场了。

## 2. 市场信号

**同类产品**
- 面向非开发者：Lovable、Bolt、v0、Replit Agent。
- 面向开发者的云端编码 agent：GitHub Copilot coding agent、OpenAI Codex、Claude Code、Google Jules。
- 国内：字节 TRAE（SOLO 模式）、阿里 Qoder、腾讯 CodeBuddy、百度秒哒等。

**值得注意的信号**
- GitHub Spark（自然语言 → 全栈应用 → 一键部署）2025 年 7 月推出，2026 年 8 月 4 日起不再允许新建应用，8 月 31 日关闭。GitHub 的说法是，这类需求改由 Copilot 在开发者已有的环境里完成。
- 行业讨论的焦点已经从"AI 能不能做出应用"转向"能不能把应用可靠地送上生产"。
- 这个品类的毛利普遍承压：推理成本计入营业成本，部分 AI 编码创业公司的毛利是负的。

## 3. 按项目类型的可行度

| 项目类型 | "零 IDE"可行度 | 说明 |
|---|---|---|
| 静态站、落地页、文档站 → Pages | 很高 | 现在就能稳定做到 |
| 纯前端应用、小游戏、小工具 | 高 | 主要风险在视觉和交互验收 |
| CLI 或库 → Releases 制品 | 中高 | 跨平台构建能模板化；代码签名要用户自己的证书 |
| 带登录和数据库的 CRUD（借助 BaaS） | 中 | 跑起来不难，难在权限和数据安全配对 |
| 接第三方 API、支付、复杂业务规则 | 中低 | 需求模糊、要凭证、有外部状态，agent 容易表面上做完、实际没做对 |
| 移动 App 上架、桌面应用签名公证 | 低 | 证书、开发者账号、商店审核都要人来办 |
| 已有大型代码库的迭代 | 对非技术用户很低 | 这是面向开发者的编码 agent 的主场 |

**GitHub Pages 的限制**：只能托管静态站；服务条款不允许用它跑商业 SaaS 或电商；站点上限 1 GB，每月流量软上限 100 GB，部署超过 10 分钟会超时。要做真产品，发布目标得扩展到 Vercel、Cloudflare 或自有云。

## 4. 主要挑战与对策（按重要性排序）

### 4.1 可靠性卡在"最后 20%"，而用户无力接手

这是核心矛盾。Agent 从 0 到 1 很快，但代码量和需求一复杂，成功率就明显下降：改一处坏两处，在同一个错误上反复打转，或者说做完了其实没做完。在 IDE 里写代码时有工程师兜底；本产品承诺用户不碰代码，一旦卡住，用户看不懂也改不了，体验会从"魔法"变成"黑箱卡死"。做 demo 很爽、做成真产品很难，这是同类产品用户留不住的主要原因之一。

对策：
- 收窄范围，模板化。
- 每一步 commit，随时可以回滚。
- 卡住时自动降级：换更强的模型、提高推理强度、拆小任务、回滚到上一个通过的版本。
- 提供逃生舱，再加一个"真人工程师兜底"的付费档。

### 4.2 验证：谁来证明"做对了"

"跑测试、修 bug"听起来是个闭环，但测试也是 agent 写的。它可能只测自己的实现，把依赖全 mock 掉，甚至为了让测试变绿去改测试或跳过测试。测试全绿不等于符合需求。

对策：
- 验收标准在需求阶段定下来，由用户确认，编码 agent 无权修改。
- 测试和评审交给独立上下文的 agent。
- 加上 E2E 和截图比对。
- 核心指标看用户验收通过率，而不是测试通过率。

### 4.3 需求澄清：难点从写代码变成说清楚要什么

非技术用户的需求本来就含糊，而且往往看到东西才知道自己要什么。模型会自作主张地补全需求，做出来不对，返工又烧钱。

对策：
- 像产品经理那样只问几个关键问题。
- 先给可点击的原型。
- 规格写成文档并做版本管理。
- 把"改需求"当成正式操作：需求改了什么，代码就跟着改什么。

### 4.4 安全：多租户运行 AI 写的代码

- **执行隔离**：容器本身不是安全边界。出网要限制，防挖矿，防探测内网和云元数据地址，防被当成跳板。
- **平台滥用**：免费生成加免费托管，会被拿去批量做钓鱼站和诈骗页。部署如果挂在平台的组织或域名下，封号和法律风险都在平台。
- **生成代码的漏洞**：2025 年 Lovable 生成的大量应用因为数据库行级权限（RLS）没配好而泄露了用户数据。还有两类风险：模型编造的依赖包名被人抢注（slopsquatting）；依赖包、网页、issue 里藏着提示词注入，诱导 agent 泄露密钥。
- **破坏性操作**：2025 年 Replit 的 agent 在代码冻结期间删掉了用户的生产数据库。agent 绝不能直接持有生产凭证，改生产一律走 PR 加人工批准。

具体对策见[技术方案](03-architecture.md)第 7 节"安全设计"。

### 4.5 单位经济：大头是 token，失败的尝试也要付钱

Agent 每一轮都要重发全部上下文，成本大致随轮数的平方增长；死循环和反复返工是成本失控的主要来源。月费 $20 的重度用户很容易让平台亏钱。测算和降本手段见第 5 节。

### 4.6 竞争与差异化

模型厂商（Anthropic、OpenAI、Google）和代码平台（GitHub）都亲自下场了，而本产品最大的一块成本恰好是它们的收入。连 GitHub 都把 Spark 关了，说明光把"对话 → 应用 → 部署"串起来算不上壁垒。可选方向：

- **企业私有化部署**：接入企业自己的 GitLab 和 CI，用国产模型，数据不出企业，满足审计合规。这是海外 SaaS 进不来的市场。定位成"需求 → MR → CI"的研发自动化，而不是"人人零代码"。
- **垂直场景**：固定产物类型，比如活动页/H5、企业官网、数据看板、内部工具、小程序。模板可以做得很深，验收标准明确，成功率高。
- **人工兜底服务**：AI 做 80%，平台工程师补剩下的 20%，按交付收费。

### 4.7 面向国内：合规与基础设施

- **模型**：Anthropic 从 2025 年 9 月起不再向中国资本控股的企业提供服务，OpenAI API 也不对中国大陆开放。面向公众的生成式 AI 服务还要备案或登记。所以基本要以国产模型为主。
- **发布**：GitHub Pages 在国内访问不稳定。面向国内用户的站点要放在国内云上，自定义域名要做 ICP 备案。
- **网络**：GitHub、npm、PyPI、Docker Hub 都要配镜像和缓存。

### 4.8 非技术用户与 Git 平台的错位

画像 A（非技术个人）大多没有 GitHub 账号，也不理解仓库、PR、Actions 这些概念。

- 要求用户自带账号：门槛高，但代码归属清晰，滥用风险在用户那边。
- 平台代建并托管仓库：体验顺滑，但滥用风险、存储和 CI 成本都落在平台，还要受 GitHub 服务条款和用量限制约束。

这个问题要和首要用户一起决定，见[决策记录](05-decisions.md) Q-06。

## 5. 成本测算

以 Claude Opus 5 为例：输入 $5 / 百万 token，输出 $25 / 百万 token；缓存读取按输入价的 0.1 倍计，5 分钟缓存写入按 1.25 倍计。

### 5.1 单个编码任务

- **Anthropic 公开测算**：单个编码任务约 $0.7–1.4。默认推理强度约 $1.39；先用低强度、失败再按默认强度重跑，约 $0.70，通过率持平。
- **自行估算示例**：假设 30 轮，平均上下文 5 万 token，每轮新增 3 千 token 工具结果，输出 1 千 token。
  - 每轮 ≈ 4.7 万缓存读取 × $0.5/M + 3 千缓存写入 × $6.25/M + 1 千输出 × $25/M ≈ $0.067
  - 30 轮 ≈ $2.0

### 5.2 单个项目

| 场景 | 等效任务数 | token 成本粗估 |
|---|---|---|
| 小型前端应用首版（含测试、CI 修复） | 5–20 | $5–30 |
| 首版之后的一次修改 | 1–3 | $0.5–5 |
| 复杂项目或反复返工 | 50 以上 | $100 以上 |

**其他成本**：沙箱按运行时长计费（例如 Anthropic Managed Agents 为 $0.08/小时），和 token 比可以忽略；GitHub Actions 对公开仓库免费，私有仓库按套餐额度计费。

### 5.3 降本手段

| 手段 | 预期效果 |
|---|---|
| 提示缓存 | Anthropic 实测 agent 循环成本降到原来的 27%–40% |
| 先低推理强度，失败再调高 | 通过率不降，成本约减半 |
| 模型分层：强模型做规划和疑难修复，便宜模型做子任务 | 视任务而定，要用评测集验证 |
| 每任务硬预算、死循环检测 | 限制最坏情况 |
| 按成功交付或 credit 计费 | 把一部分失败成本从平台转移出去 |

国产模型单价往往便宜一个数量级，但长链路 agent 任务的成功率可能更低。比较时看"每次成功交付的成本"，不看 token 单价。

## 6. 参考资料

- [MindStudio — The AI App Builder Category in Q3 2026](https://www.mindstudio.ai/blog/state-of-ai-app-builders-q3-2026)
- [GitHub Docs — GitHub Spark（下线说明）](https://docs.github.com/en/copilot/concepts/spark)
- [GitHub Docs — GitHub Pages limits](https://docs.github.com/en/pages/getting-started-with-github-pages/github-pages-limits)
- [TechCrunch — The high costs and thin margins threatening AI coding startups](https://techcrunch.com/2025/08/07/the-high-costs-and-thin-margins-threatening-ai-coding-startups/)
- [Northflank — Daytona vs E2B in 2026](https://northflank.com/blog/daytona-vs-e2b-ai-code-execution-sandboxes)
- [Modal — Best code execution sandboxes for AI agents](https://modal.com/resources/best-code-execution-sandboxes-ai-agents)
- [腾讯云开发者社区 — 国产 AI 编程助手横向评测](https://developer.cloud.tencent.com/article/2726398)
- [Anthropic — Pricing](https://platform.claude.com/docs/en/pricing)
- [Anthropic — Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Anthropic — Managed Agents overview](https://platform.claude.com/docs/en/managed-agents/overview)
