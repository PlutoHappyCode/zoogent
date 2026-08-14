# zoogent — 自建多人格 Agent 系统

一个配置驱动的个人 Agent 运行时：飞书多机器人接入、9 个人格、
技能即插即用、长期记忆 + RAG 知识库、联网搜索、定时任务、故障自动补发。

> 演进历史与未来规划见 [ROADMAP.md](ROADMAP.md)；
> 部署与运维见 [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。

## 功能

**人格矩阵**
- 9 个人格 × 9 个独立飞书机器人，各有身份/原则/记忆/日程：
  🐶 狗管家(总调度)、🐱 招财猫(财经)、🕊️ CT鸥(技术)、🦅 洞察鹰(分析)、
  🦊 产品狐、🦁 设计狮、🐦 营销鹊、👥 颖分身、🐭 芝士鼠
- 人格文件热加载：改 soul/rules/cron/记忆不用重启，下一条消息生效

**飞书原生交互**
- 卡片回复（标题=人格名，footer=模型/tokens/耗时）、**三阶段表情**（举手→敲键盘→撒花）
- 尾部编号选项自动渲染成可点按钮
- 消息类型：文字 / 富文本(post 展平) / **图文混排**(嵌图下载+多图视觉) /
  语音(faster-whisper 本地转写) / 图片(视觉模型) /
  文件(txt、pdf≤5MB；PDF 自动存档进知识库)
- 引用回复：自动拉取被引用消息内容拼进上下文
- 定时任务推送也走卡片（失败降级纯文本）

**记忆体系（三层）**
- 注入层（常驻层）：system prompt = 精简画像 + soul + rules + 限长记忆索引，
  lean 模式常驻 ~700 tokens；`prompt.lean: false` 可回滚全量注入的 classic 版
- 工具层：`memory_list/read/write/search`，沙箱在本人格 memory/，防穿越
- 语义层：`kb_search` RAG 检索（shared + 全部工作产出 + 本人格记忆，
  BGE-M3 embedding，sqlite 向量库，惰性增量更新，人格隔离）
- 会话：超长自动摘要（前情提要），每轮落盘重启不丢

**Prompt 瘦身（渐进式披露，2026-08-06 起默认开启）**
- 原则：元数据>全文，索引>详情，检索>预装，摘要>原文
- 五刀：① 用户画像摘要化（shared/userBrief.md 常驻，全文靠
  kb_search 按需检索）② 共享知识/踩坑记录不再预装，改为检索指针
  ③ 记忆索引限长（顶层文件 + 子目录计数，详情用 memory_list）
  ④ 输出格式规则压成 5 条禁止清单
  ⑤ 极简注入版：每个人格一份 `<agent>/lean.md`（soul+rules 手工
  压缩合并，~45%），lean 模式下替代全文注入；soul.md/rules.md
  原文保留（classic 回滚 / 检索底料）
- 效果：常驻层 ~6500 → ~2000 字符（-70%），测试实测
- 工具集收敛：agent.json 按人格分配 skills（stock 仅
  housekeeper/finance），受限人格在 rules.md 声明能力边界，
  没有的工具如实说没有、不硬编

**技能（即插即用）**
- `stock` 股票行情/分析
- `feishu_docs` 飞书云文档/多维表格（lark-cli 后端）
- `web_search` 联网搜索（自建 SearXNG）+ `web_fetch` 抓网页正文
- `knowledge` RAG 知识库：`kb_search / kb_reindex / kb_status`
  （md/txt/pdf 均可索引，发给人格机器人 PDF 自动存档入库）

**主动性与可靠性**
- cron.md 声明式定时任务（支持 [quiet] 静默），专属机器人推送
- 欠条机制：模型持续故障 → 记账 → 恢复后哨兵自动补发 / 说「继续」重放
- 并发安全：会话锁串行同一 chat，欠条/绑定/档案文件读改写均加锁
- 可观测：控制台 + logs/agent.log 轮转（5MB×3，带时间戳/级别）

## 架构

```
你（飞书 / 终端）
   ↓ 消息
channels/     渠道层：收发 + 渲染（飞书卡片、表情、终端打印）
   ↓
core/         Agent 层：主循环 + 模型调用 + 会话 + 人格 + 记忆
   ↓
AgentsHome/skills/*.py   技能层：工具，模型自己决定调不调
   ↕
AgentsHome/   人格层：soul.md / rules.md / memory/ ……（数据，与代码分离）
Zootopia/     工作区：每个 Agent 的产出落盘位置
```

**设计原则**
- 渠道无智能、智能渠道无关：channels 只调 `handle_command` + `run_agent_meta`
- 数据与代码分离：AgentsHome 可整体备份迁移；agent.json 也可用
  `AGENT_CONFIG` 指到数据目录统一管理
- 配置驱动：agent.json（JSONC 注释 + `${VAR}` 环境变量替换）管
  模型/人格/技能/渠道/调度/会话

### 目录结构

```
zoogent/                # 项目根目录
├── agent.json          # 中央配置（本地默认位置，NAS 上指向 SSD 数据目录）
├── agentRunner/       # 代码：Agent 运行时
│   ├── .env            #   密钥（AGENT_API_KEY、FEISHU_*_APP_ID/SECRET……）
│   ├── main.py         #   装配入口：校验配置 → 起渠道 → 起调度器/哨兵
│   ├── core/           #   Agent 引擎（渠道无关，所有智能在这里）
│   │   ├── config.py   #     配置加载（AGENT_CONFIG 可变位置）
│   │   ├── models.py   #     模型注册表 + embedding 客户端
│   │   ├── personas.py #     人格加载：soul/rules/共享注入 → system prompt
│   │   ├── memory.py   #     记忆工具 + 会话管理(落盘) + 欠条队列 + 锁
│   │   ├── engine.py   #     主循环：技能加载、chat_with_retry、斜杠命令
│   │   ├── asr.py      #     语音转写（faster-whisper 本地模型，启动预热+锁）
│   │   ├── log.py      #     统一日志：控制台 + 轮转文件
│   │   └── scheduler.py#     cron 定时 + 欠条哨兵 + 触发记录落盘
│   ├── channels/       #   渠道层（feishu 多账号 / terminal 调试）
│   │   └── feishu/     #     按职责拆分：_loop_proxy / _owner / _parser / _card / channel
│   └── tests/          #   离线自测（runAllTests 64 项 + test_feishu_modules 53 项）
│
├── AgentsHome/         # 人格与能力的家（数据+技能，可整体备份迁移）
│   ├── skills/         #   技能（每个 .py = 一组工具，SCHEMAS + FUNCTIONS）
│   ├── shared/         #   全员共享：user.md、learnings、tools.md(自动)、kb.sqlite
│   └── <agent_id>/     #   每个人格：soul.md rules.md cron.md memory/ inbox/
│
├── Zootopia/homework/<agent_id>/   # 各人格的工作产出目录
├── docs/DEPLOYMENT.md  # 部署与运维（NAS / Docker / SearXNG / lark-cli）
└── ROADMAP.md          # 演进历程与未来规划
```

### 业务流程：一条消息的生命之旅

```
【第 1 站】飞书渠道收货（channels/feishu/channel.py · on_message）
  你在飞书发消息 → ws 长连接推给对应机器人
  ├─ 白名单校验（agent.json 的 whitelist 总开关 + 各账号 owner_open_ids）
  ├─ owner.json 记下你的 chat_id（以后定时任务往这里推，加锁读写）
  ├─ 按消息类型预处理：
  │    文字 → 直接取文本
  │    富文本 post → 文字展平 + 嵌图下载（图文混排，最多 3 张）
  │    图片 → 下载转 base64（给视觉模型，只服务这一轮）
  │    语音 → 下载 → core/asr.py 本地转写成文字（faster-whisper）
  │    文件 → 下载提取文本（txt 直读 / pdf 用 pypdf，≤5MB、8000 字）
  │    其他类型 → 婉拒 + 记日志留痕
  ├─ 是引用回复？→ 按 parent_id 把被引用消息内容拉下来，
  │    拼成【你引用了：…】+ 你的新话
  ├─ 是斜杠命令？（/agents /agent /who）→ 直接处理，旅程结束
  └─ 贴上「举手」表情（表示已收到），转交后台线程
       │
       ▼
【第 2 站】后台线程切换表情（channels/feishu/channel.py · _process_with_reaction）
  ├─ 撕掉「举手」表情
  └─ 贴上「敲键盘」表情（表示正在处理）
       │
       ▼
【第 2 站】引擎接棒（core/engine.py · run_agent_meta）
  ├─ 确定人格：固定人格机器人直接用本人格；
  │    多人格账号查 chatAgents.json 里的绑定（加锁）
  ├─ 取会话锁：同一（人格+chat）串行处理，你连发三条也不会乱
  └─ 你说的是「继续」？→ 核销 pending.json 欠条，重放原任务
       │
       ▼
【第 3 站】组装上下文（core/memory.py + personas.py）
  ├─ 取会话：内存没有 → 从磁盘 sessions/*.json 恢复（重启不丢）
  ├─ 人格文件指纹（soul/rules/shared/记忆的 mtime）变了？
  │    → 原地热加载 system prompt，对话历史保留（改人格不用重启）
  ├─ 组装 system prompt（prompt.lean 双模式）：
  │    lean（默认）：精简画像 + soul + rules + 工作区路径
  │    + 检索指针（共享知识/踩坑记录用 kb_search 按需取，不预装）
  │    + 限长记忆索引（顶层文件 + 子目录计数）+ 5 条禁止清单
  │    classic：全量注入（画像全文 + 共享知识 + 踩坑记录），回滚用
  └─ 追加你的消息 → _trim：超 30 条先把旧对话摘要进 summary.md
       （_safe_cut 保证裁切点不落在工具调用组中间，防止会话被切坏）
       （下轮以「前情提要」带出，不暴力砍头）
       │
       ▼
【第 4 站】模型主循环（core/engine.py → Kimi k3，最多 20 轮）
  chat_with_retry 把对话发给模型（失败指数退避重试 6 次）
  ├─ 模型说"我要调工具" → engine 按工具名分发执行：
  │    memory_*  → 本人格 memory/ 沙箱（防路径穿越）
  │    kb_search → 语义检索 RAG：
  │       先惰性增量更新 shared/kb.sqlite（扫 mtime，只重建有变化的文件）
  │       → query 调 embedding（bge_m3_embed）→ 余弦 top-k 片段
  │       范围 = shared/ + Zootopia 产出 + 本人格记忆（人格隔离）
  │    web_search → NAS 自建 SearXNG(:8181) 联网取证，web_fetch 细读
  │    stock / feishu_docs → 行情接口 / lark-cli
  │    结果塞回对话 → 再问模型（工具报错也返回说明，不炸循环）
  │    ⚡ Token 优化：含 tool_calls 的 assistant 消息，reasoning 超过 200
  │       字符自动截断，节省 60-80% token 消耗
  └─ 模型直接回答 → 出循环，进下一站
       │
       ├─【岔路·故障】6 次重试全败 → 记欠条 pending.json
       │     撤回未应答消息 → 回复「已记欠条」→ 见支线 A
       ▼
【第 5 站】收尾（engine + memory）
  ├─ 历史里的图片 base64 换成占位符（防止以后每轮白烧几千 token）
  └─ 会话落盘 sessions/*.json（每轮一次，重启接着聊）
       │
       ▼
【第 6 站】卡片送达到你（channels/feishu/channel.py · reply_card）
  ├─ 撕掉「敲键盘」，贴上「撒花」（完成）
  └─ 飞书卡片：标题=人格名、正文 lark_md 渲染、
       尾部 1. 2. 3. 编号自动生成可点按钮、footer=模型·tokens·用时
```

**支线 A：欠条自愈（哨兵线程，每 120s）**

```
扫每个人格的 pending.json → 重放任务（走主线第 2~5 站）
  → 模型还没恢复：留账下轮再试
  → 成功了：原机器人推送「📮 补发」卡片 → 推送成功才核销欠条
  （你说「继续」= 手动版同路径；两条路都有锁，不会双核销）
```

**支线 B：定时任务（scheduler 线程，每 30s）**

```
扫 AgentsHome/*/cron.md（HH:MM 指令，改文件即生效，[quiet]=静默）
  → 到点且今天没触发过（fired 落盘，重启不重复推）
  → run_proactive 以该人格走主线第 2~5 站（可调用全部技能）
  → 人格专属机器人卡片推送给你（[quiet] 则只执行不打扰）
```

## 扩展（最常见三种）

**加一个技能**：`AgentsHome/skills/<名字>.py` 定义 `SCHEMAS` + `FUNCTIONS`，
返回字符串不抛异常，重启生效，`tools.md` 自动重新生成。
只给部分人格用：`agents.members.<id>.skills` 填技能词干列表。

**加一个人格**：`AgentsHome/<agent_id>/` 建 `soul.md` + `rules.md`
（可选 cron.md / memory/ 预置），`agent.json → agents.members` 加一行；
专属飞书机器人再配 `channels.feishu.accounts.<id>` 和 .env 凭证。

**加一个模型**：`agent.json → models.<名字>` 加 provider/base_url/api_key/model；
人格级切换：`agents.members.<id>.model`。

## 快速上手

```bash
cd agentRunner
python main.py                      # 启动（⚠️ 同一时刻只能有一个实例在跑）
python tests/runAllTests.py       # 全功能离线自测（64 项）
python tests/test_feishu_modules.py # feishu 模块拆分验证（53 项）
python tests/testPending.py        # 欠条机制自测（13 项）
python tests/testConcurrency.py    # 并发锁压力自测（3 项）
```

飞书里：`/who` 看当前人格；每个机器人固定一个人格，换人找对应机器人；
模型故障时说 `继续` 重放欠条任务。

生产部署（NAS / Docker / SearXNG / lark-cli）见
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)。
