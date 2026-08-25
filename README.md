# zoogent — 自建多人格 Agent 系统

> 一个配置驱动的个人 Agent 运行时。9 个人格、9 个独立飞书机器人、
> 数据与代码完全分离——改人格改记忆不下线，改代码只重启不重建镜像。

---

## 核心能力 · 3×3 卡片

| | | |
|---|---|---|
| **人格矩阵**<br/>9 人 × 9 机器人，固定身份/原则/记忆/日程；改文件即生效 | **飞书原生交互**<br/>卡片 + 三阶段表情（举手→敲键盘→撒花）+ 建议按钮 + 图文/语音/文件消息 | **三层记忆**<br/>注入层（常驻 ~700t）+ 工具层（memory_write_batch）+ 语义层（RAG，含归档记忆） |
| **Prompt 瘦身**<br/>五刀精简 + 轮次优化三刀，典型写文件任务 7 轮 → 3 轮 | **技能即插即用**<br/>stock 财经 · feishu_docs 飞书文档 · web_search 联网 · knowledge RAG · workspace 沙箱文件操作 | **欠条自愈**<br/>模型故障记账 → 哨兵每 120s 自动补发，或说「继续」手动重放 |
| **声明式定时**<br/>cron.md 写 HH:MM + 指令，[quiet] 静默不打扰 | **并发安全**<br/>会话锁串行同 chat，档案/绑定/欠条读改写全加锁 | **可观测**<br/>每轮 API 日志（耗时/字符/tokens）+ footer 溯源 + 5MB×3 轮转 |

**关键数据**：9 人格 · 10 飞书渠道 · 自测 133 项 · 语义索引 6498 chunks / 523 文件 · 系统 prompt 常驻 ~2000 字符

---

## 快速开始 · 20 秒能读完

**本机跑（调试）**

```bash
cd agentRunner
python main.py                   # 启动，同一时刻只能一个实例
python tests/runAllTests.py    # 64 项离线自测 + feishu 53 + pending 13 + 并发 3
```

**飞书用**

- `/who` 看当前人格；每个机器人固定一个人格，找对机器人就是换人
- 模型故障说「继续」手动重放任务
- 文字结尾写 1. 2. 3. 会自动变成可点按钮

---

## 架构全貌

```
你（飞书 / 终端）
   ↓
channels/    渠道层：只收发、只渲染，不做决策
   ↓
core/        Agent 层：人格/记忆/会话/模型主循环（所有智能在这里）
   ↓
skills/*.py  技能层：每组工具一个 .py，模型自己决定调不调
   ↕
AgentsHome/  人格层：soul/rules/cron/memory — 纯数据，可整体备份迁移
Zootopia/    工作区：每个人格的产出落盘位置
```

**三条设计原则**

- 渠道无智能、智能渠道无关：channels 只调 `handle_command` + `run_agent_meta`
- 数据与代码分离：AgentsHome + agent.json + Zootopia 全在 SSD 外挂卷，容器删了数据一根毫毛不少
- 配置驱动：agent.json（JSONC 注释 + `${VAR}` 环境变量替换）统一管模型/人格/技能/渠道/调度

**关键目录**

```
zoogent/
├── agent.json                 # 中央配置（NAS 上 AGENT_CONFIG 指向 SSD）
├── agentRunner/               # 代码（core 引擎 + channels 渠道 + tests）
├── AgentsHome/                # 人格与能力的家（数据）
│   ├── skills/                #   技能文件（SCHEMAS + FUNCTIONS）
│   ├── shared/                #   共享：user.md、kb.sqlite、踩坑记录
│   └── <agent_id>/            #   每人：soul.md rules.md cron.md memory/
├── Zootopia/homework/<id>/    # 各人格工作产出
├── Dockerfile                 # 镜像（只装运行环境，代码不进镜像）
└── README.md                  # 你正在看的
```

---

## 一条消息怎么走完（5 站 · 2 支线）

### 主线

**① 飞书收货**（channels/feishu）
白名单 → owner.json 记住 chat_id → 按类型预处理（文字/富文本/图片/语音/PDF/引用）→ 斜杠命令直接答 → 贴举手表情

**② 引擎接棒**（core/engine）
后台异步换"敲键盘"表情 → 孤儿 tool_calls 消毒 → 确定人格 → 会话锁（同 chat 串行不打架）→ 「继续」= 核销欠条

**③ 组装上下文**（core/personas + memory）
会话落盘恢复（重启不丢）→ 人格文件 mtime 变了就热加载 system prompt → 组装 prompt（lean 精简 / classic 全量，一键切换）→ 旧对话摘要进 summary.md

**④ 模型主循环**（最多 20 轮）
`chat_with_retry` 指数退避 6 次 → 要调工具就执行（memory_write_batch 合并写、kb_search 自动增量 RAG、熔断死循环同参数 3 次、tool reasoning 超 200 字自动截断）→ 直接答复就出循环 → 失败满 6 次记欠条

**⑤ 卡片送达**（channels/feishu）
图片 base64 换占位符（防以后每轮白烧 token）→ 会话落盘 → 换"撒花"表情 → 飞书卡片 + footer（模型·tokens·用时）

### 两条支线

- **欠条自愈（120s 哨兵）**：扫 pending.json → 重放主线 ②~⑤ → 成功才核销、失败留账下次再试
- **定时任务（30s 调度器）**：扫所有人格 cron.md → 到点今天没触发过就 run_proactive 走主线 → 专属机器人推送（[quiet] 只执行不打扰）

---

## 三种扩展姿势

**加人格**
`AgentsHome/<agent_id>/` 建 `soul.md + rules.md`（可预置 cron.md / memory/），agent.json `agents.members` 加一行，飞书应用凭证进 .env。

**加技能**
`AgentsHome/skills/<名字>.py` 定义 `SCHEMAS + FUNCTIONS`，返回字符串不抛异常。只给部分人用：`agents.members.<id>.skills` 填技能名列表。

**加模型 / 切换模型**
agent.json `models.<名字>` 加 provider/base_url/api_key/model；某人格想单独用：`agents.members.<id>.model`。

---

## 部署与运维

### 三层分离架构

```
┌─ 镜像 zoogent:latest ──────────────────┐ 不变，只装依赖
│  node:18 + python3.11 venv + lark-cli  │ 重建 ≈ 5 min
├─ 代码 agentRunner/ ────────────────────┤ scp + restart ≈ 3s
│  本地改 → 上传 → docker restart        │
├─ 数据 SSD /data/zoogent ───────────────┤ 热加载，改完下条消息生效
│  agent.json · AgentsHome · Zootopia    │ 容器删了无损
└────────────────────────────────────────┘
```

### 三个挂载卷 + 五个环境变量

| 宿主机 | 容器内 | 作用 |
|---|---|---|
| `~/agent-system` | `/app` | 代码（上传你写的） |
| SSD `zoogent` | `/data/zoogent` | **活数据**（agent.json + AgentsHome + Zootopia） |
| `~/lark-cli-config` | `/root/.lark-cli` | lark-cli 授权 |

NAS `.env` 五个变量指到挂载点（本地 Mac 不写，自动用相对路径）：

```bash
AGENT_CONFIG=/data/zoogent/agent.json
AGENTS_HOME=/data/zoogent/AgentsHome
AGENTS_WORKSPACE=/data/zoogent/Zootopia/homework
AGENTS_SKILLS_DIR=/data/zoogent/AgentsHome/skills
ASR_MODEL_DIR=/data/zoogent/models
```

### 首次部署 · 五步

1. **打包上传**（COPYFILE_DISABLE=1 必须，防止 macOS 生成 `._*` 垃圾文件）：
   `tar czf` agentRunner/AgentsHome/Zootopia → scp NAS
2. **解压 + 同步数据**到 SSD `$ZOOGENT` → chown
3. **建镜像**：`cd ~/agent-system && sudo docker build -t zoogent:latest .`
4. **启动容器**：`docker run -d --name zoogent --restart always` 挂三个卷
5. **验证**：`docker logs -f zoogent` 看到 10 条「飞书渠道已启动」

### 日常更新 · 三张表

| 改了什么 | 操作 | 要重启？ |
|---|---|---|
| soul / rules / memory / cron.md | 直接改，**热加载** | **不用**，下一条消息生效 |
| agent.json / .env / 代码 .py | scp 上传 → `docker restart zoogent` | 重启容器（3s） |
| requirements / Dockerfile | `docker build` → restart | 重建镜像（5min+） |

常用运维三行：

```bash
scp -r agentRunner 15112331127@192.168.31.52:~/agent-system/
sudo docker restart zoogent
sudo docker logs -f zoogent
```

### ⚠️ 运维红线 · 踩坑备忘（读一遍，能省你几天）

1. **只能一个实例在跑**。本地 Mac 和 NAS 二选一，两边抢同一批机器人 = 消息随机落实例、按钮点了找不到上下文（2026-07-30）
2. **同类容器不能订阅同一批机器人**。openclaw 和 zoogent 同时绑管家狗 = 抢答消息，一个正常一个 401，表现为间歇性报错（2026-08-24）
3. `._*` macOS 垃圾文件进容器会炸 UTF-8 decode：`find ~/agent-system $ZOOGENT -name '._*' -delete`
4. `.env` 四行（AGENT_API_KEY 等）不能丢，丢了所有 kimi 模型 key 被替换为空串 = 全 401
5. 容器有两套代码目录：`/app/agentRunner`（生产，挂载的）和 `/app/agent-runner`（历史残留，别碰）；部署只动 `~/agent-system → /app`

### 各组件运维入口

**SearXNG（web_search 后端）**
容器 searxng，端口 8181（8080 被占），引擎只开 baidu/sogou/quark；改配置 `sudo docker restart searxng`。

**RAG 知识库**
- 向量库：`shared/kb.sqlite`（随 AgentsHome 备份）
- 日常不管：kb_search 前自动增量扫 mtime，只重嵌变了的文件
- 变 embedding 模型才说一句 `kb_reindex`（已改为增量，几秒完）
- 范围：shared/ + Zootopia 产出 + 本人格 memory 全部目录（**含 archive 归档**，2026-08-25 纳入）
- PDF 索引入库：直接发飞书给机器人 / 丢进 `shared/pdfs/`

**ASR 语音转写**
faster-whisper small int8 CPU，模型在 `$ZOOGENT/models/`；换更准的 medium 改 agent.json `tools.asr` + 下载模型 → 重启。

**飞书云文档（lark-cli）**
bot 身份自助授权（agent.json 里的 app 凭证），状态存在 `/root/.lark-cli` 挂载卷；三个权限要开：`docx:document` / `bitable:app` / `docs:permission.member:create`（建完自动分享主人）。

**安全加固**
- `.env` / `agent.json` 进 `.gitignore`，权限 600
- API key 统一 `${VAR}` 写 .env，不能明文
- `workspace` shell 默认 strict 只读白名单，要放宽改 agent.json `tools.workspace.posture`

---

## 演进与规划

### 里程碑

**2026-03 ~ 07 中旬 · OpenClaw 时代**
人格/记忆/Zootopia 体系起源（最早归档 2026-03-13）；痛点：想改的行为改不动、飞书交互浅、故障无兜底。

**2026-07-29 · zoogent 诞生 🎂**
~3k 行 Python agentRunner 零写出，当晚部署 NAS。9 人格 9 机器人、四层分离、卡片+表情+按钮、三层记忆、欠条机制、cron 定时、人格热加载；镜像/代码/SSD 三层分离部署。

**2026-07-30 · 工程化 + 两大能力**
上午：并发锁 + 日志轮转 + 会话落盘 + AGENT_CONFIG SSD；下午：富文本展平 + 引用回复 + 定时任务卡片化；晚上：SearXNG 联网搜索 + RAG 知识库（bge-m3+sqlite，首建 234 文件 / 4195 块）。

### 未来规划

| 优先级 | 事项 | 说明 |
|---|---|---|
| P1 | delegate 工具 | 人格间互调，狗管家"调度中心"人设落地（防循环委派 + 独立子会话） |
| P1 | lean 模式观察期 | 对比 tokens / 工具调用率；效果不佳就 `prompt.lean=false` 回滚 classic |
| P1 | 恢复洞察鹰日报 | 搜索通道已就绪；需先修 lark-cli 飞书文档同步 |
| P2 | shell 沙箱持久化 | 容器里第三方工具重启丢失 → 改挂载 |
| P2 | 白名单收紧 | agent.json 一行开启 + 补齐各账号 owner_open_ids |
| P2 | 调度器升级 | 持久化队列替代轮询 sleep：cron 表达式、独立线程、退避重试、任务状态可见 |
| P2 | CI/CD 自动化部署 | GitHub Actions lint+pytest → 镜像/rsync 自动推 NAS，替代手搓 scp |
| P3 | 技能导入规范 | core 改成真 package，skills 不再依赖运行时 sys.path，支持静态检查 |
| P3 | RAG 异步索引 | 启动后台预索引 / scheduler 定时重建，免首次搜索卡 |
| P3 | 欠条多任务 | pending 改列表，支持按序 / 选择核销，Dashboard 可看 |
| P3 | 快模型路由 | 闲聊走高速模型，复杂任务留 k3（观察期后） |
| P3 | 流式卡片重做 | 飞书 edit API 更成熟后回归（v1 不稳已移除） |

### 更新日志（最新在顶）

| 日期 | 更新 | 影响面 |
|---|---|---|
| 2026-08-25 | **轮次优化三刀**（7 轮→3 轮）：① 明示目标直接写 shared/memory/指定路径，去掉 4 轮工作区中转冗余 ② 通用并行工具调用——独立工具（kb_search+write_batch）同一轮出 ③ 写入 shared 后免手动 reindex + kb_reindex 改增量 | personas.py（模板 4 处）、8 个 lean.md（规则 4）、knowledge.py |
| 2026-08-25 | **RAG 含 archive**：语义索引扩到本人格 memory/archive/，9 人 225 归档文件一次预热，5032→6498 chunks，查询不慢 | knowledge.py |
| 2026-08-24 | **401 双修复**：① 容器 .env 缺 4 行（AGENT_API_KEY 等）→ key 全空，用主机备份恢复 ② openclaw 抢答同一批 3 个机器人 + 它自己 key 失效 → 间歇性 401 → 停 openclaw + 禁自启；根因见 openclaw 日志 401 invalid_auth | NAS .env、openclaw 容器 |
| 2026-08-18 | **模型可见文字英文化**：prompt 模板 / 5 个 schema / 工具返回串 / 5 技能 52 处 description + ~84 返回串 → 全精简英文；用户侧（卡片/命令/报错）仍中文；Reply in Chinese 保留 | personas.py、memory.py、engine.py、skills ×5 |
| 2026-08-18 | **响应提速四件套**（诊断报告驱动）：① memory_write_batch 多文件 1 轮 ② 旧 tool 结果压缩 200 字（管家狗 -41% token）③ 表情后台异步 ④ API 计时埋点 footer 溯源 | memory/personas/engine/feishu 4 模块 + agent.json（housekeeper 去 stock） |
| 2026-08-17 | **飞书文档链路全通**：① lark-cli v2 接口适配 ② bot 身份自助授权闭环（自动初始化 agent.json app 凭证，挂载卷重启不丢）③ 建文档自动分享主人 ④ Dockerfile 钉死 lark-cli 1.0.88 | skills/feishuDocs.py、Dockerfile |
| 2026-08-17 | **模型与稳定性修复**：① 模型兜底去 hardcode default ② 孤儿 tool_calls 消毒（会话恢复自动补响应，管家狗换 kimi 全 400 根因）③ 死循环熔断（同工具同参 3 次 → 空转烧 40 万 token 的教训）④ NAS 统一只读 /data/zoogent/agent.json | models/memory/engine/config 4 模块 + NAS .env |
| 2026-08-16 | **全员格文档精简**：8 agent（除 askme）soul/rules/lean/memory 瘦身 57-92%，删重复 archive/daily-logs 目录，同步 NAS | AgentsHome/* 全目录 |
| 2026-08-13 | **Token 爆炸 + 流式移除**：① tool_calls reasoning >200 字截断（-60~80% token）② 全部流式逻辑删掉，改同步直答 ③ 修复 CT鸥空 buffer 不更占位卡 | engine.py + feishu/channel.py + agent.json |
| 2026-08-13 | **feishu 模块化**：920 行单文件 → 6 文件包（loop_proxy/owner/parser/card/channel），test_feishu_modules 新增 53 项，外部引用完全兼容 | channels/feishu/* + tests 两个文件 |
| 2026-08-13 | **L0 必做 6 项**：API key 变量化 / shell 加固 + lark-cli 白名单 / 凭证权限 600 / SQLite WAL+busy_timeout / 流式卡片可关 / 三阶段表情 | config/engine/models/feishu/workspace/knowledge 6 模块 + agent.json/.env |
| 2026-08-07 | agent.json 上移根目录 + 路径解析相对配置文件目录；workspace 技能四工具上线（沙箱 strict 档）；全仓库命名驼峰化 agent-runner → agentRunner；evals 回归用例重写 19 条全绿；极简注入 lean.md 9 份（soul+rules 手工压缩，-70% 常驻字符）；本地 AgentsHome 对齐 NAS 生产副本；测试到 59 项 | agent.json、config.py、workspace.py、全仓库 12+ 文件引用、9 个 evals/lean.md |
| 2026-08-06 | Prompt 瘦身四刀（画像摘要 + 共享知识改检索 + 记忆限长索引 + 规则压 5 条清单，常驻 -55%），prompt.lean 双模式回滚；工具集收敛按人格分配；测试到 58 项 | personas.py、userBrief.md、agent.json、7 个 rules.md |
| 2026-07-30 | 图文混排（post 嵌图 + 多图视觉，≤3 张）；欠条死循环裁切点消毒 + 存量毒会话修复；ASR 预热锁；语音 PDF 自动入库；RAG+搜索上线；卡片化定时任务；并发/日志/AGENT_CONFIG 全加固 | 几乎所有 core 模块 + 飞书渠道 + skills×2 |

---

## 附录 · Code by Contract（改代码前读一遍）

这些是系统里的硬约束和经验教训，写在工程里不一定看得到，集中在这里。

**运行时参数**
- housekeeper max_steps=25（其他 agent 按 agent.json 各自配置）；因为狗管家调度最重，步骤多
- `streaming.enabled` 必须是 false；流式逻辑已完整移除，打开会炸
- 三阶段 emoji 有效类型：WAVE（举手）/ Typing（敲键盘）/ PARTY（撒花），RaisingHand 等会被飞书拒 231001
- 会话超 30 条走摘要进 summary.md，裁切点 _safe_cut 不会切在工具调用组中间
- 模型主循环最多 20 轮 tool_call，再多会结束；同工具同参连调 3 次熔断

**NAS 部署铁律**
- Docker 容器 zoogent 入口是 `/opt/venv/bin/python main.py`（不是系统 python3），`python3 main.py` 会缺 openai 模块
- 4 条 .env 变量（AGENT_API_KEY / AGENT_BASE_URL / AGENT_MODEL / ALIYUN_API_KEY）+ 4 条路径（AGENT_CONFIG / AGENTS_HOME / AGENTS_WORKSPACE / AGENTS_SKILLS_DIR）+ ASR_MODEL_DIR，少了容器内全部读空
- agent.json 里所有 `base_url` 要和 key 对应：kimi key → kimi coding 端点，qwen key → aliyun maas 端点，乱配必 401
- lark-cli 要用 v2 命令格式（`+create --doc-format markdown` / `+update --command append` / `+record-list --json` / `members create --yes`），老格式会炸
- scheduler job 去重键必须用 hashlib.md5，不能用 Python `hash()`——PYTHONHASHSEED 每次重启变，会重复触发

**Prompt & Token 经验**
- 给模型看的文字（模板/工具 description/返回串）一律写英文 + 短句：token 省 30-50%，遵循度还更稳；Reply in Chinese 规则留着，用户侧看到的永远中文
- 存长推理的会话文件：`sessions/<agent>--<chat_id>.json`，敏感内容或 401 不要先清 session，先查日志/container 抢答
- 会话历史里含 tool_calls 的 assistant 消息，reasoning 一律截到 200 字：模型写的那一大段中文推理对下一轮工具调用没价值，但能把一轮 15k tokens 撑爆

**飞书交互经验**
- 换 emoji 是 5 次 HTTP 请求串行（加举手/去举手/加敲键盘/去敲键盘/加撒花），NAS 慢环境累计几秒钟——所以后台异步换，不阻塞回复主线
- 占位卡片只有一个就永远不会更新空 buffer（CT鸥吃结果的教训）——所以流式已彻底移除
- 尾部 1.2.3. 自动渲染按钮；不要逼 agent 每次都给选项，"没有下一步就直接结束"——硬凑选项让模型+用户都别扭
