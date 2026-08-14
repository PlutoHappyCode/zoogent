# ROADMAP — zoogent 演进历程与规划

> 规则：每次功能更新都在「更新日志」追加一条（日期 + 内容 + 影响面）。
> 未来想做但在排的进「规划」，做完挪进日志。

## 项目历程

### 2026-03 ~ 2026-07 中旬：OpenClaw 时代

- 人格/记忆/Zootopia 工作区体系起源，最早记忆归档 2026-03-13
- 在 OpenClaw 上跑通了多人格 + journal + 任务看板的雏形
- 痛点显现：想改的行为改不动、飞书交互深度不够、故障无兜底

### 2026-07-29：自建运行时 zoogent 诞生 🎂

- 从零写出 agent-runner（~3k 行 Python），当晚部署到 NAS Docker
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
|---|---|---|
| P0 | 响应速度优化 | 流式卡片（首字 2-3s 上屏）+ 计时埋点；备用弹药：`kimi-for-coding-highspeed` 高速模型（方案已成形） |
| P1 | delegate 工具 | 人格间互相调用，让狗管家的"调度中心"人设落地（防循环委派、独立子会话） |
| P1 | 恢复洞察鹰日报 | 搜索通道已就位；恢复前需先修 lark-cli 飞书文档同步 |
| P2 | 自学习闭环 | 借鉴 Hermes：agent 完成任务后自动把经验沉淀成可复用技能 |
| P2 | 白名单收紧 | agent.json 一行开启 + 补齐各账号 owner_open_ids |
| P3 | prompt 瘦身 | 用埋点数据定位 system prompt 成本，精简注入 |
| P3 | 快模型路由 | 短消息/闲聊走高速模型，复杂任务留 k3（需观察期） |

## 更新日志

> 从 2026-07-30 起，每次更新追加在这里（最新的在最上面）。

| 日期 | 更新 | 影响面 |
|---|---|---|
| 2026-07-30 | 图文混排支持：post 消息嵌图下载 + 多图视觉输入（最多 3 张） | channels/feishu.py、core/engine.py |
| 2026-07-30 | 修复欠条死循环：_trim 裁切点可能落在工具调用组中间，孤儿 tool 消息导致会话永久 400；新增 _safe_cut 边界消毒；全盘扫描修复存量毒会话（analyst/finance） | core/memory.py |
| 2026-07-30 | ASR 稳定性：启动后台预热（464MB 模型加载 ~2 分钟）+ 加载锁防双重加载 | core/asr.py、main.py |
| 2026-07-30 | 语音消息（faster-whisper 本地转写）+ PDF 入库（索引发给机器人的 PDF 自动存档） | core/asr.py、channels/feishu.py、knowledge.py、镜像重建 |
| 2026-07-30 | RAG 知识库 + 联网搜索上线；SearXNG 重建 | 技能×2、agent.json、system prompt 纪律 |
| 2026-07-30 | 定时任务推送卡片化；引用回复/富文本支持 | channels/feishu.py |
| 2026-07-30 | 并发锁 + 日志轮转 + 会话落盘 + AGENT_CONFIG | core 全部、agent.json 位置 |
