# AstrBot for SPZX

> 基于 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 深度改造、面向办公与研发协作场景的发行版。
> 上游项目与全部原始版权归 AstrBotDevs 及贡献者所有，本分支遵循 AGPL-3.0-or-later。

<p>
<img alt="version" src="https://img.shields.io/badge/version-4.31.4-76bad9">
<img alt="python" src="https://img.shields.io/badge/python-3.12%2B-blue">
<img alt="license" src="https://img.shields.io/badge/license-AGPL--3.0--or--later-green">
<img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey">
</p>

把 AstrBot 从“IM 聊天机器人平台”扩展为以 ChatUI 为主入口的 **Agent 工作台**：
会话、项目、宿主文件、知识库、多智能体工作流、多人账户与运行审计在同一个界面里闭环。
上游的 IM 接入、模型服务、插件市场、知识库等能力全部保留，可直接沿用上游生态。

典型用法：资料整理与文档产出、表格与 PDF 处理、代码仓库维护、网页资料检索、长任务推进与定时汇报、多人共享同一实例。

---

## 1. 项目定位

- 本仓库是 AstrBot 的定制分支，集成分支为 `all`，**不是**官方发行版；上游通用文档仍然适用。
- 改造集中在六块：ChatUI 工作台、Agent 团队与工作流编排、上下文与记忆、工具执行与权限、多人账户与远端接入、运行可观测性。
- 设计取舍优先“可审计、可回滚、单一真源”：配置能推断就不手填，界面如实反映后端状态。

## 2. 核心能力

### 2.1 ChatUI：办公工作台

- **项目化会话**：项目侧栏“+”与输入框项目 chip 共用同一加载页；会话自动归组，也可选择“不在项目中工作”。每个会话独立选择模型与思考强度，重开继续沿用。
- **工作区侧栏**：文件树、Git 变更/分支/工作树、网页预览三合一；支持文件预览、编辑、重命名、移入系统回收站、在宿主机打开。
- **文件引用与审阅**：聊天中引用宿主文件并预览；整文件差异、逐行评论，评论可直接回传为对话输入。
- **补丁式编辑**：批量修改、预览与回滚，默认走补丁模式。
- **侧边聊天**：独立配置、可保存重开、面板宽度可调，不打断主会话阅读位置。
- **Agent 浏览器**：实时观测 Agent 操作页面，需要时手动接管、拖动元素并交还；支持全屏、自适应宽度、断线重连。
- **任务可视化**：goal / todo 固定在顶栏，上下文用量、思考强度、消息搜索、滚动标记、归档会话一览可达。
- **开发者模式**：每次模型请求保存最终 payload 快照，覆盖每一步请求与重试/降级，便于定位“模型到底收到了什么”。
- **传输层**：同一标签页内所有运行中的会话共用一条 SSE 流并按会话分发；发送、停止、切换会话走普通 HTTP，避免多会话并发时浏览器假死。

### 2.2 Agent 团队与工作流

- **三类资产**：团队（协调者按目标派发）、工作流（画布编排）、角色模板（职责 + 本机资源）。
- **画布**：节点库/连线/属性面板，条件分支、全部汇合、有界循环、嵌套子工作流；缩放、小地图、撤销重做、自动布局。
- **版本与草稿**：编辑自动保存草稿，发布生成固定版本；引用固定版本、主动升级，运行冻结启动时的资产、输入与配置。
- **试运行**：完整流程 / 仅选中节点 / 从选中节点开始，可指定精确历史产物或手填测试数据。
- **人工在环**：人工问题、动态选择、版本批准、期限与推荐依据；待处理收件箱集中展示“需我处理”。
- **运行工作台**：任务、过程、待办、产物分栏；查看实际上下文及其装配来源、子作用域、循环轮次、用量与限制。
- **资产包**：导入导出含嵌套资产，支持版本升级；删除为逻辑删除。

### 2.3 上下文与记忆

- 上下文预算统一按 token 计算；主代理以**窗口切换**管理长会话，窗口原文归档在 `data/agent_history/<umo_key>/<window_id>`，可检索、可回看。
- 压缩（子代理与 `/compact`）按工具调用与结果的完整配对切分，不拆散一次调用；较早内容整理为结构化检查点。
- 思考内容死循环检测：稳定重复块只保留第一遍并追加标记，避免退化推理被反复回灌。
- 内置 `/compact` 手动压缩；`notes` 工具跨窗口保存任务状态，落盘于 `data/agent_notes`。
- 子代理使用独立会话页面展示每次调用详情与配置，可为动态子代理设置 MCP 工具策略。

### 2.4 工具与执行环境

- 内置文件工具组：读取、写入、编辑、补丁、删除（进系统回收站）、上传、下载、grep。
- 文件访问模式：`/mode full|readonly|workspace`，会话级生效，与“文件访问权限”按钮等价。
- Windows ACL 沙箱：只读/工作区模式下即使调用 shell 或 Python 也无法越权写入。
- Shell、Python、浏览器、桌面 CUA、定时任务、知识库、跨会话消息工具。
- 办公文档技能：`documents`（DOCX）、`spreadsheets`（XLSX/CSV）、`pdf`，可直接生成与审阅办公文件。

### 2.5 多人协作与远端

- **多用户 ChatUI**：独立账户（admin / user）、会话、项目、附件、配置档案；账户可启用、禁用、删除；支持整机备份与恢复。
- **Agent 跨会话通信**：Agent 可向其他会话发消息、创建会话、读取会话；同会话内 Agent 消息与用户消息分离展示。
- **跨实例协作**：在“设置 → 安全”启用并生成密钥，ChatUI 侧“远端连接”单向绑定远端实例；远端可发现/创建会话、发送消息、共享附件与人工选择。

### 2.6 可观测性

- 运行级 token 用量按 Agent 运行上报；Trace / 观测页、运行过程与产物留档；开发者模式提供请求 payload 快照。
- 控制台、日志与更新提醒保留上游行为，启动时发现新版本或插件更新会弹窗提示。

## 3. 与上游的主要差异

| 维度 | 上游 AstrBot | 本分支 |
| --- | --- | --- |
| 入口重心 | IM 平台对话 | ChatUI 工作台为主，IM 接入完整保留 |
| 多智能体 | SubAgent 调用 | Agent 团队 / 工作流资产、可视化画布、人工节点、运行工作台 |
| 上下文 | 以压缩为主 | token 预算 + 窗口切换 + 原文归档 + 笔记工具 |
| 文件能力 | 沙箱 / 工作区 | 宿主文件浏览、引用审阅、补丁编辑、ACL 写入控制 |
| 账户模型 | 单管理员 | 多账户、独立资源与配置档案、备份恢复 |
| 实例间 | 无 | 跨会话通信、跨实例远端会话与密钥绑定 |
| 观测 | 日志与控制台 | 运行用量、Trace、请求快照、运行产物留档 |

## 4. 快速开始

### 环境要求

- Python 3.12+，包管理使用 [uv](https://docs.astral.sh/uv/)
- Node.js 18+ 与 pnpm（仅前端开发需要）
- Windows / Linux / macOS；Windows 额外提供托盘启动入口

### 源码运行

```bash
uv sync
uv run main.py
```

默认 WebUI：`http://localhost:6185`。首次启动会在 `data/` 下生成配置、数据库与运行目录。

### 前端开发

```bash
cd dashboard
pnpm install
pnpm dev          # http://localhost:3000，/api 代理到 6185
```

生产构建：

```bash
cd dashboard && pnpm build     # 输出 dashboard/dist
```

内核优先加载 `data/dist`，要求其 `assets/version` 与 `astrbot/__init__.py` 中的版本一致，否则回退到内置 `dashboard/dist`。
改前端只需把构建产物同步到 `data/dist` 即可生效；改后端需要重启进程。

### Windows 托盘运行

```powershell
uv pip install pystray
pythonw.exe main_bg.py            # 无控制台窗口，右下角托盘图标
python main_bg.py --open-browser  # 服务就绪后自动打开 WebUI
```

托盘菜单提供“打开 WebUI / 打开日志 / 退出”。`main_bg.py` 与 `main.py` 功能一致，仅多出托盘与后台运行能力。

### Docker

仓库内 `Dockerfile` 从源码构建，`compose.yml` 默认拉取上游镜像，使用本分支时请自行构建镜像：

```bash
docker build -t astrbot-spzx .
docker run -d --name astrbot-spzx -p 6185:6185 -v "$PWD/data:/AstrBot/data" astrbot-spzx
```

需要沙箱隔离时参考 `compose-with-shipyard.yml`（Shipyard 服务与本实例共享 `data`）。
