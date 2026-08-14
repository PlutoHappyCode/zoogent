# ROADMAP — zoogent 演进历程与规划

> 规则：每次功能更新都在「更新日志」追加一条（日期 + 内容 + 影响面）。
> 未来想做但在排的进「规划」，做完挪进日志。

## 项目历程

### 2026-03 ~ 2026-07 中旬：OpenClaw 时代

- 人格/记忆/Zootopia 工作区体系起源，最早记忆归档 2026-03-13
- 在 OpenClaw 上跑通了多人格 + journal + 任务看板的雏形
- 痛点显现：想改的行为改不动、飞书交互深度不够、故障无兜底

### 2026-07-29：自建运行时 zoogent 诞生 🎂

- 从零写出 agentRunner（~3k 行 Python），当晚部署到 NAS Docker
- 初始能力：
  - 9 人格 × 9 飞书机器人矩阵（固定人格账号 + 白名单）
  - 渠道/core/技能/数据四层分离，配置驱动（JSONC + ${VAR}）
  - 卡片回复 + 表情状态 + 建议按钮、图片/文件消息
  - 记忆三层（prompt 注入 / memory 工具 / 会话摘要）
  - **欠条机制**：模型故障记账 → 哨兵自动补发（原创设计）
  - cron.md 定时任务 + 人格文件指纹热加载
  - 技能：stock、feishu_docs（lark-cli）
- 部署架构：镜像 / 代码 / SSD 数据三层分离

### 2026-07-30：工程化加固 + 搜索与知识库

**上午·工程加固**

- 并发安全：会话锁（同一 chat 串行）+ 欠条/绑定/档案文件锁
- 日志系统：print → logging，logs/agent.log 轮转（5MB×3）
- 会话落盘：重启不丢对话；定时任务触发记录落盘防重推
- agent.json 挪到 SSD 数据目录统一管理（新增 `AGENT_CONFIG` 支持）

**下午·飞书交互补全**

- 富文本（post）消息展平支持；不支持的消息类型记日志
- 引用回复：按 parent_id 拉被引用消息内容拼进上下文
- 定时任务推送卡片化（原来是纯文本，markdown 裸奔）
- 止血：杀掉本地误开的 main.py 进程（和 NAS 抢消息导致会话割裂）

**晚上·两大能力**

- **联网搜索**：NAS 重建 SearXNG（:8181，baidu/sogou/quark 引擎），
  新技能 web_search（web_search / web_fetch）
- **RAG 知识库**：新技能 knowledge（kb_search / kb_reindex / kb_status），
  Kimi API 的 BGE-M3 embedding + sqlite 向量库，索引
  shared/ + Zootopia 产出 + 本人格记忆（人格隔离），惰性增量更新，
  首建 4195 块 / 234 文件
- 测试体系到 50 项；洞察鹰两条日报 cron 暂停（02:00 静默整理保留）

## 未来规划

| 优先级 | 事项 | 说明 |
| --- | --- | --- |
| P1 | delegate 工具 | 人格间互相调用，让狗管家的"调度中心"人设落地（防循环委派、独立子会话） |
| P1 | lean 模式观察期 | 跑两周对比数据：memory_list/kb_search 调用率、每轮 tokens（agent.log 有基线）；验证「检索换预装」不掉效果，不行就 prompt.lean=false 回滚 |
| P1 | 恢复洞察鹰日报 | 搜索通道已就位；恢复前需先修 lark-cli 飞书文档同步 |
| P2 | shell 沙箱持久化 | workspace 技能已上线（strict 档）；剩：容器里装的第三方工具重启不持久，需改挂载 |
| P2 | 自学习闭环 | 借鉴 Hermes：agent 完成任务后自动把经验沉淀成可复用技能 |
| P2 | 白名单收紧 | agent.json 一行开启 + 补齐各账号 owner_open_ids |
| P2 | 调度器升级 | 用持久化队列替代轮询+sleep：cron 表达式/间隔任务、独立消费线程、指数退避重试、任务状态可观测 |
| P2 | CI/CD 与自动化部署 | GitHub Actions 跑 lint/pytest/JS 语法检查，镜像/rsync 自动推送到 NAS，替代手动 scp |
| P3 | 规范技能导入路径 | 把 core 做成真正 package，skills 不再依赖运行时 sys.path，支持单独导入和静态检查 |
| P3 | RAG 索引异步化 | 启动时后台预索引或 scheduler 定时重建，避免首次 kb_search 全量阻塞 |
| P3 | 欠条多任务支持 | pending 由覆盖改为列表，支持多条未完成指令按序/选择核销，Dashboard 可查看 |
| P3 | 快模型路由 | 短消息/闲聊走高速模型，复杂任务留 k3（需观察期） |
| P3 | 流式卡片重做 | v1 已移除（不稳定、CT鸥 bug）；等飞书 edit API 更成熟后重做 |

> 备注：上表中 P2/P3 项为当前待办/backlog，近期优先处理 P1 与 P2 项。

## 更新日志

> 从 2026-07-30 起，每次更新追加在这里（最新的在最上面）。

| 日期 | 更新 | 影响面 |
| --- | --- | --- |
| 2026-08-13 | **Token 爆炸修复 + 流式全移除**：① 截断含 tool_calls 的 assistant 消息 reasoning（>200 字符自动截断，节省 60-80% token）② 移除全部流式逻辑（占位卡、StreamCollector、chat_with_streaming 分支），改为同步直接回复 ③ 修复 CT鸥结果被吃 bug（流式 buffer 为空时占位卡永远不更新） | agentRunner/core/engine.py、agentRunner/channels/feishu/channel.py、agent.json |
| 2026-08-13 | **feishu.py 模块化拆分**：920 行单文件拆为 6 文件包（feishu/**init**.py、_loop_proxy.py、_owner.py、_parser.py、_card.py、channel.py），按事件循环/档案/解析/卡片/主类职责分层；新增 test_feishu_modules.py 53 项单元测试；外部 import 路径完全兼容 | agentRunner/channels/feishu/*、agentRunner/tests/test_feishu_modules.py、agentRunner/tests/runAllTests.py |
| 2026-08-13 | **L0 必做改进完成**：① API Key 环境变量化、② shell 工具加固（lark-cli 已加入白名单）、③ 凭证文件权限加固、④ SQLite WAL + busy_timeout、⑤ 流式卡片输出（可配置开关）、⑥ 三阶段表情系统（举手→敲键盘→撒花） | agentRunner/core/config.py、agentRunner/core/engine.py、agentRunner/core/models.py、agentRunner/channels/feishu.py→feishu/、agentRunner/skills/workspace.py、agentRunner/skills/knowledge.py、agent.json、agentRunner/.env、.gitignore |
| 2026-08-07 | agent.json 上移到项目根目录：相对路径改为相对配置文件所在目录解析（home/workspace/skills/asr 模型目录），AGENT_CONFIG 覆盖语义不变 | agent.json、core/config.py、README、DEPLOYMENT |
| 2026-08-07 | workspace 技能上线：file_list/file_read/file_write/shell 四工具，沙箱钉死在各人格工作区（路径逃逸拦截、元字符拦截、strict 档只读命令白名单）；cto/housekeeper/yingeraicopy/product/marketing 开通；测试到 64 项 | AgentsHome/skills/workspace.py、agent.json、tests/runAllTests.py |
| 2026-08-07 | 命名统一驼峰化：agent-runner→agentRunner，测试/skills/配置文件全部改驼峰（runAllTests、webSearch.py、feishuDocs.py、userBrief.md、chatAgents.json、errors.md/learnings.md/playbooks.md）；agent.json skills 值、12 个代码/文档文件引用、16 个人格 prompt 引用同步更新；Python 函数名保持 snake_case（PEP8）；59/59 测试通过 | 全仓库 |
| 2026-08-07 | evals 回归用例重建：身份用例去掉英文 id 关键词、纪律用例给具体材料防反问、housekeeper 危险用例（曾真实创建飞书文档）换无害题；evals.py 新增「a\|b」或语法抗措辞随机性，19 条全绿 | AgentsHome 9 个 evals.md、agentRunner/evals.py |
| 2026-08-07 | 极简注入版（瘦身第五刀）：每个人格新增 `lean.md`——soul+rules 手工压缩合并（~45%），lean 模式下替代全文注入；身份/职责/原则/纪律/open_id/能力边界全保留，soul.md/rules.md 原文不动（classic 回滚+检索底料）；实测常驻层 2875→1985 字符，较 classic 累计 -70% | AgentsHome 9 个 lean.md、core/personas.py |
| 2026-08-07 | 本地数据对齐 NAS：本地 AgentsHome 整体替换为 NAS 生产副本（旧版备份 AgentsHome.local.bak），合并回 userBrief.md 与 7 个 rules.md 能力边界；agent.json 改用 NAS 版为底（保留 qwen/deepseek 模型分配），套上 skills 收窄与 prompt.lean，并加入 .gitignore | AgentsHome/、agentRunner/agent.json、.gitignore |
| 2026-08-07 | 测试到 59 项：新增极简注入版回归（人人格有 lean.md、比原文短、内容真被注入） | tests/runAllTests.py |
| 2026-08-06 | Prompt 瘦身（渐进式披露）：常驻层四刀——画像摘要化（新增 shared/userBrief.md，全文转 kb_search 按需检索）、共享知识/踩坑记录改检索指针不预装、记忆索引限长（顶层文件+子目录计数）、格式规则压成 5 条禁止清单；实测常驻层 6460→2875 字符（-55%）；`prompt.lean` 双模式可一行配置回滚 classic 全量注入 | core/personas.py、shared/userBrief.md、agent.json |
| 2026-08-06 | 工具集收敛：agent.json 按人格分配 skills（stock 仅 housekeeper/finance，askme 再减 feishu_docs），7 个受限人格 rules.md 追加能力边界声明（没有的工具如实说没有，不硬编） | agent.json、AgentsHome 7 个 rules.md |
| 2026-08-06 | 测试到 58 项：新增 lean 双模式对比 / 索引限长 / 精简画像 3 项；e2e 识别模型故障的欠条回复（WARN 不计失败）并自行核销测试欠条 | tests/runAllTests.py |
| 2026-07-30 | 图文混排支持：post 消息嵌图下载 + 多图视觉输入（最多 3 张） | channels/feishu.py、core/engine.py |
| 2026-07-30 | 修复欠条死循环：_trim 裁切点可能落在工具调用组中间，孤儿 tool 消息导致会话永久 400；新增_safe_cut 边界消毒；全盘扫描修复存量毒会话（analyst/finance） | core/memory.py |
| 2026-07-30 | ASR 稳定性：启动后台预热（464MB 模型加载 ~2 分钟）+ 加载锁防双重加载 | core/asr.py、main.py |
| 2026-07-30 | 语音消息（faster-whisper 本地转写）+ PDF 入库（索引发给机器人的 PDF 自动存档） | core/asr.py、channels/feishu.py、knowledge.py、镜像重建 |
| 2026-07-30 | RAG 知识库 + 联网搜索上线；SearXNG 重建 | 技能×2、agent.json、system prompt 纪律 |
| 2026-07-30 | 定时任务推送卡片化；引用回复/富文本支持 | channels/feishu.py |
| 2026-07-30 | 并发锁 + 日志轮转 + 会话落盘 + AGENT_CONFIG | core 全部、agent.json 位置 |
