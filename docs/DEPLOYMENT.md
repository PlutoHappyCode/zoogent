# 部署与运维

> 功能与架构见 [../README.md](../README.md)；演进历史见 [../ROADMAP.md](../ROADMAP.md)。

## 部署架构：三层分离

```
┌─ 镜像 zoogent:latest ──────────────────────────────┐
│  运行环境：node:18 + python3.11 venv + lark-cli     │
│  封死不动，只有依赖变了才重建                        │
├─ 代码（agentRunner/）──────────────────────────────┤
│  本地改 → 上传 → docker restart 生效（不用重建镜像）  │
├─ 数据（zoogent/，SSD 外挂）──────────────────────────┤
│  agent.json 配置 + 人格/记忆/技能 + 工作区            │
│  热加载，改了下一条消息就生效                         │
│  容器删了重建，数据一根毫毛不少                       │
└────────────────────────────────────────────────────┘
```

| 挂载（docker run -v） | 容器内路径 | 作用 | 谁会写它 |
|---|---|---|---|
| `~/agent-system` | `/app` | 代码（agentRunner 在 `/app/agentRunner`） | 你（上传代码） |
| SSD `zoogent` | `/data/zoogent` | **活数据**：agent.json + AgentsHome + Zootopia | 你 + agent |
| `~/lark-cli-config` | `/root/.lark-cli` | lark-cli 授权配置 | lark-cli 自己 |

数据路径通过 NAS 的 `.env` 指到挂载点（本地 Mac 不配这四个变量，自动用相对路径默认值）：

```bash
AGENT_CONFIG=/data/zoogent/agent.json   # 配置文件也在 SSD，与人格/记忆统一管理
AGENTS_HOME=/data/zoogent/AgentsHome
AGENTS_WORKSPACE=/data/zoogent/Zootopia/homework
AGENTS_SKILLS_DIR=/data/zoogent/AgentsHome/skills
```

## NAS 首次部署（从零到跑起来）

前置：NAS 有 docker、本地有 sshpass；宿主机路径以实际为准
（本项目：ssh `15112331127@192.168.31.52 -p 12345`，
zoogent 在 `/data_n003/data/udata/real/15112331127/zoogent`）。

```bash
# 1. 打包上传（COPYFILE_DISABLE=1 必须加！否则 macOS 扩展属性会变成
#    ._* 垃圾文件，agent 读到报 'utf-8' codec can't decode 0xa3）
cd /Users/pluto/Desktop/zoogent
COPYFILE_DISABLE=1 tar czf /tmp/agent-deploy.tar.gz \
  --exclude='.venv' --exclude='__pycache__' --exclude='.DS_Store' \
  agentRunner AgentsHome Zootopia
scp -P 12345 /tmp/agent-deploy.tar.gz 15112331127@192.168.31.52:~/

# 2. NAS 上解压、建数据目录、同步数据到 zoogent
ssh -p 12345 15112331127@192.168.31.52
mkdir -p ~/agent-system && tar xzf ~/agent-deploy.tar.gz -C ~/agent-system
ZOOGENT=/data_n003/data/udata/real/15112331127/zoogent
sudo cp -a ~/agent-system/AgentsHome/. $ZOOGENT/AgentsHome/
sudo cp -a ~/agent-system/Zootopia/.  $ZOOGENT/Zootopia/
sudo chown -R 15112331127:15112331127 $ZOOGENT

# 3. 构建镜像（代码不进镜像，只装运行环境和依赖）
cd ~/agent-system && sudo docker build -t zoogent:latest -f Dockerfile .

# 4. 启动容器（三个挂载 + 开机自启）
mkdir -p ~/lark-cli-config
sudo docker run -d --name zoogent --restart always \
  -v /home/15112331127/agent-system:/app \
  -v /data_n003/data/udata/real/15112331127/zoogent:/data/zoogent \
  -v /home/15112331127/lark-cli-config:/root/.lark-cli \
  zoogent:latest

# 5. 验证：日志里每个机器人一条 connected to wss
sudo docker logs -f zoogent
```

## 日常更新（三种场景，对号入座）

| 你改了什么 | 怎么做 | 重启？ |
|---|---|---|
| 人设/规则/记忆（zoogent 里的 .md） | 直接改，**热加载** | 不用，下一条消息生效 |
| agent.json（在 $ZOOGENT 下）/ .env / *.py 代码 | 直接改或上传 → `sudo docker restart zoogent` | 重启容器（3 秒） |
| requirements.txt / Dockerfile | `sudo docker build -t zoogent:latest .` → restart | **重建镜像** |

```bash
# 上传代码（在本地项目根目录执行）
scp -P 12345 -r agentRunner 15112331127@192.168.31.52:~/agent-system/
# 新增/变更人格文件时同步到 zoogent（注意是数据目录，不是代码目录）
scp -P 12345 -r AgentsHome/* 15112331127@192.168.31.52:$ZOOGENT/AgentsHome/

# 常用运维（docker 需要 sudo）
sudo docker logs -f zoogent     # 看日志（实时）
tail -100 ~/agent-system/agentRunner/logs/agent.log   # 看日志（落盘）
sudo docker restart zoogent     # 改完代码重启生效
sudo docker ps                  # 看状态
```

⚠️ **同一时刻只能有一个实例在跑**（本地 Mac 或 NAS 二选一），
否则两边抢同一批机器人的消息，会话会割裂（消息随机落到不同实例，
按钮点了找不到上下文——2026-07-30 踩过）。
⚠️ 万一 NAS 上又出现 `._*` 文件：`find ~/agent-system $ZOOGENT -name '._*' -delete`
⚠️ 旧数据备份 `~/agent-system/AgentsHome.bak.20260729` / `Zootopia.bak.20260729`
和 `$ZOOGENT/AgentsHome.bak.20260730`，稳定后可删

## SearXNG 搜索服务（web_search 技能的后端）

容器 `searxng`：`~/searxng/settings.yml`（已开 JSON 格式；引擎只留
baidu/sogou/quark——NAS 网络访问不了 google/bing/ddg）。
端口 8181（8080 被别的服务占用），配置改动 `sudo docker restart searxng`。

```bash
# 重建（容器/镜像丢失时）
sudo docker run -d --name searxng --restart always \
  -p 8181:8080 -v /home/15112331127/searxng:/etc/searxng \
  searxng/searxng:latest
# 验证
curl "http://127.0.0.1:8181/search?q=test&format=json"
```

## RAG 知识库运维

- 向量库：`$ZOOGENT/AgentsHome/shared/kb.sqlite`（随 AgentsHome 备份）
- 日常**不用管**：kb_search 前自动增量重建有变化的文件
- 只有换 embedding 模型 / 库损坏 / 保留 mtime 方式同步过文件时，
  才需要对任一人格说一句 `kb_reindex` 全量重建
- 首次全量建库约 5 分钟（4000+ 块），之后每次搜索 <2s
- 索引 md/txt/pdf：PDF 丢进 `AgentsHome/shared/pdfs/` 即可入库
  （在飞书里直接发 PDF 给机器人也会自动存档到这里）

## ASR 语音转写运维

- 模型：faster-whisper small（int8 CPU），存 `$ZOOGENT/models/`（SSD 挂载内）
- 配置：`agent.json → tools.asr`（model / model_dir）
- 换更大模型（如 medium，中文更准但更慢）：改配置 + 下载对应模型后重启
- 模型下载（容器内，HF 走镜像源）：
  ```bash
  sudo docker run --rm -e HF_ENDPOINT=https://hf-mirror.com \
    -v $ZOOGENT:/data/zoogent zoogent:latest \
    /opt/venv/bin/python -c "from faster_whisper import WhisperModel; \
    WhisperModel('small', download_root='/data/zoogent/models')"
  ```

## 飞书云文档 / 多维表格技能（lark-cli）

后端是官方 **lark-cli**。CLI 用它自己配置的应用身份操作
（`lark-cli auth status` 查看），不随 Agent 切换；
分享目标取自当前 Agent 账号的 owner_open_ids。

```bash
npx @larksuite/cli@latest install   # 安装
lark-cli config init --new          # 配置应用
lark-cli auth login --recommend     # 登录授权（扫码）
lark-cli auth status                # 确认身份
```

必需的应用权限（开放平台 → 对应应用 → 权限管理 → 开通并发布）：

| 权限 scope | 用途 |
|---|---|
| `docx:document` | 建/读/写云文档 |
| `bitable:app` | 读写多维表格 |
| `docs:permission.member:create` | 建文档后自动分享给主人 |

一键开通链接（分享权限，2026-07-29 开通）：
`https://open.feishu.cn/page/scope-apply?clientID=cli_a974a39057b89cbc&scopes=docs%3Apermission.member%3Acreate`
（clientID 是 lark-cli 自己的应用 ID，见 `lark-cli auth status` 输出的 appId）

**应用权限**：每个要用的机器人应用，去开放平台开 `docx:document`、
`drive:drive`、`bitable:app` 并发布；文档/表格还需把机器人加为协作者
（机器人建的文档会自动分享给主人）。

## 改配置注意

- `agent.json` 支持 `//` 注释和 `${VAR}` / `${VAR:-默认值}`（从 .env / 环境变量替换）
- 配置文件位置：默认项目根目录 `agent.json`；.env 里设 `AGENT_CONFIG`
  可指向别处（NAS 上指向 `/data/zoogent/agent.json`）
- 改 `cron.md` / soul.md / rules.md / shared / memory：**不用重启**
- 改 `agent.json` / .env / 技能代码：**要重启**

## 安全加固

- `.env` 和 `agent.json` 已在 `.gitignore` 中，不会被提交到 Git
- `.env` 文件权限应为 `600`（仅所有者可读）：`chmod 600 agentRunner/.env`
- `agent.json` 中 API Key 应使用 `${VAR}` 引用环境变量，不要明文写死
- `config.py` 启动时会自动检测：明文 key 会告警，`.env`/`agent.json` 权限过宽也会告警
- `workspace` 技能的 shell 命令默认 strict 档，仅允许只读白名单命令
- 如需放宽 shell 限制，改 `agent.json → tools.workspace.posture` 为 `auto` 或调整 `strict_allowlist`
