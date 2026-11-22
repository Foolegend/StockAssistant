# 📈 StockAssistant AI

StockAssistant 是一款集成多种金融分析技能（Skills）和 MCP（Model Context Protocol）工具的智能股票分析助手。

当前架构：**后端是 Bun 编写的 PTY + WebSocket 服务，把 StockAssistant CLI 的交互式命令行窗口直接暴露给浏览器；前端用 Angular + xterm.js 内嵌该终端。**

> 原来的 HTTP/SSE 适配层（`stock-server/server/`）已移除。现在一个 WebSocket 连接对应一个 CLI 进程，终端输出原样送到前端；CLI 的 Ink 界面、工具调用、MCP、技能都在真实 PTY 中运行。

## 🛠️ 项目架构

### 1. 后端（stock-server）

- **PTY + WebSocket 会话服务**：[terminal/server.ts](stock-server/terminal/server.ts)
  - `Bun.serve` 监听 `127.0.0.1:8015`
  - 每个**会话**拥有一个 `Bun.Terminal`（PTY）和一个 CLI 进程；WebSocket 只是附着/分离，断开连接不会终止 CLI
  - 会话带输出缓冲区，重新附着时会先回放，再继续实时输出
  - `GET /health` 返回 `{"ok":true}`
- **CLI 引擎**：`src/entrypoints/cli.tsx`，以**交互模式**运行（不再使用 `-p` headless）
- **启动参数注入**：[terminal/wiring.ts](stock-server/terminal/wiring.ts)
  - `.stock/agents`、`.stock/skills`、`.stock/commands` → `--append-system-prompt`
  - `.stock/mcp-configs/mcp-servers.json` → `--mcp-config`（绝对路径）
  - `.stock/hooks/hooks.json` → `--settings`（替换 `${STOCK_PLUGIN_ROOT}`）
  - `.env` 中的 `INVEST_*` 由 `compat/preload.ts` 映射为 `ANTHROPIC_*` / `STOCK_*`
  - `--dangerously-skip-permissions`（仅限本机可信环境）
- **扩展扫描**：[lib/catalog.ts](stock-server/lib/catalog.ts)，每次新建终端连接时重新扫描 `.stock`

### 2. 前端（stock-ui）

- **终端页面**：[terminal.component.ts](stock-ui/src/app/terminal/terminal.component.ts)，基于 **xterm.js**
- 默认路由 `/` 重定向到 `/terminal`
- WebSocket 连接：`ws://127.0.0.1:8015/terminal/ws?session=<id>&cols=...&rows=...`
- 左侧会话栏来自服务端 `GET /sessions`：新建、切换、删除、导出都对应真实 PTY 会话
- 切换会话会重连并回放该会话的输出；未选中的会话继续在服务端运行
- 支持窗口自适应（FitAddon + ResizeObserver）、断线自动重连
- 原聊天页保留在 `/chat`，但其 HTTP/SSE 后端已随 `server/` 一起移除，仅作参考

## 🔌 终端协议

WebSocket 地址：

```text
ws://127.0.0.1:8015/terminal/ws?session=<会话ID>&cols=<列数>&rows=<行数>
```

客户端 → 服务端（JSON 文本帧）：

```json
{ "type": "input", "data": "..." }
{ "type": "resize", "cols": 120, "rows": 40 }
```

服务端 → 客户端：会话缓冲区回放 + 原始 ANSI 终端输出。非 JSON 文本帧会按原始输入处理。

HTTP 接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/sessions` | 会话列表（按最后活动时间倒序） |
| POST | `/sessions` | 新建会话并启动 CLI |
| DELETE | `/sessions/{id}` | 删除会话并终止 CLI |
| DELETE | `/sessions` | 删除全部会话 |
| GET | `/sessions/{id}/output` | 导出该会话终端输出（纯文本） |
| GET | `/sessions/export` | 导出全部会话（Markdown） |

WebSocket 必须带 `session=<id>`；未知或已删除的会话会以关闭码 `4404` / `4001` 结束，缺少会话 ID 返回 `4400`。

## 🧩 扩展（.stock）

仅扫描 `stock-server/.stock`，不读取全局配置或仓库外的扩展目录。

- `stock-server/.stock/agents/**/*.md`：Agent 定义；正文注入系统提示。
- `stock-server/.stock/skills/**/SKILL.md`：技能定义；技能清单写入系统提示，模型按需用 `Read` 打开对应 `SKILL.md`。
- `stock-server/.stock/commands/**/*.md`：斜杠命令定义；命令清单写入系统提示。
- `stock-server/.stock/mcp-configs/mcp-servers.json`：MCP server 注册；通过 `--strict-mcp-config` 注入。
- `stock-server/.stock/hooks/hooks.json`：Hook 定义；通过 `--settings` 注入。

技能与命令文件使用 YAML frontmatter：

```markdown
---
name: /stock-report
description: 生成单只股票的快速研究报告
argument-hint: <股票代码或公司名称>
arguments: [symbol]
when_to_use: 当用户希望一次获得行情、技术面和结论摘要时
agent: investment-analyst
skills: [stock-analysis]
mcp_server_ids: [quant-tools]
user-invocable: true
enabled: true
---

针对 $ARGUMENTS 生成结构化研究简报。
```

扩展在**每次新建终端连接时**重新扫描；修改文件后，刷新页面或点“重连”即可生效。

> 注意：终端模式下，`/` 开头的内容由 CLI 自己的命令系统处理；`.stock/commands` 不会自动注册成 CLI 的斜杠命令。需要这些命令时，直接在对话中描述任务，模型会按系统提示读取对应技能/命令说明。

## 🚀 快速启动

### 前置条件

- **Bun 1.4+**（后端运行时）：<https://bun.sh>
- **Node.js LTS + npm**（前端构建）
- **Python 3.12+**（可选：行情抓取与文档处理技能脚本）
- 首次安装依赖：

  ```bash
  cd stock-server && bun install
  cd ../stock-ui && npm install
  ```

### 一键启动前后端

Windows：

```powershell
.\start_all.ps1
```

Linux / macOS：

```bash
bash ./start_all.sh
```

脚本先启动后端并等待 `127.0.0.1:8015` 就绪，再启动前端。

- 前端：`http://localhost:4200`（默认进入终端页）
- 终端页：`http://localhost:4200/terminal`
- 后端健康检查：`http://127.0.0.1:8015/health`

首次进入终端时，CLI 会显示主题选择和安全提示；按提示操作即可。

### 分别启动（可选）

后端：

```powershell
cd stock-server
.\start_server.ps1
# Linux / macOS: ./start_server.sh
```

前端：

```powershell
cd stock-ui
.\start_ui.ps1
# Linux / macOS: ./start_ui.sh
```

不经过浏览器、直接使用 CLI：

```bash
cd stock-server
bun run start
```

## ⚙️ 配置说明

首次配置时，将模板复制为 `stock-server/.env`：

```powershell
Copy-Item stock-server\.env.example stock-server\.env
```

`.env` 不纳入 Git，只提交 `.env.example`。主要配置项：

| 变量 | 说明 |
| --- | --- |
| `INVEST_ANTHROPIC_BASE_URL` | Anthropic Messages API 兼容端点 |
| `INVEST_ANTHROPIC_AUTH_TOKEN` / `INVEST_ANTHROPIC_API_KEY` | 访问凭据 |
| `INVEST_ANTHROPIC_MODEL` | 默认模型 |
| `INVEST_ANTHROPIC_DEFAULT_SONNET_MODEL` 等 | 模型映射 |
| `INVEST_STOCK_PROVIDER_MANAGED_BY_HOST=1` | 忽略全局 CLI 的模型路由配置 |
| `INVEST_TERMINAL_HOST` / `INVEST_TERMINAL_PORT` | 终端服务监听地址与端口（默认 `127.0.0.1:8015`） |

技能脚本的 Python 依赖：

```bash
.venv/Scripts/python -m pip install -r stock-server/requirements-skills.txt   # Windows
.venv/bin/python     -m pip install -r stock-server/requirements-skills.txt   # Linux / macOS
```

## 📁 目录结构

- `stock-server/`
  - `terminal/`：PTY + WebSocket 服务与 CLI 参数注入
  - `lib/`：`.stock` 扩展扫描（catalog / frontmatter）
  - `src/`：StockAssistant CLI 与分析引擎
  - `compat/`：运行时兼容层（环境变量映射、构建宏、原生包 stub）
  - `.stock/`：Agent、Skill、Command、Hook、MCP 扩展定义
  - `.env` / `.env.example`：模型配置
  - `start_server.*`：后端启动脚本
- `stock-ui/`
  - `src/app/terminal/`：xterm.js 终端页
  - `src/app/stock-chat/`：旧聊天页（后端已移除，仅作参考）
- `start_all.*`：一键启动前后端
- `.tmp/`：本地临时文件（已忽略）

## 📝 说明与限制

- **终端等于命令执行权限**：服务只监听 `127.0.0.1`，不要暴露到公网；`--dangerously-skip-permissions` 会跳过权限确认。
- 一个服务端会话对应一个 CLI 进程；关闭页面只断开连接，会话保留，可从左侧栏重新进入。
- 会话保存在后端内存中；重启后端会丢失所有会话（CLI 进程也会结束）。
- `.stock` 的斜杠命令不会注册为 CLI 原生命令；技能通过系统提示 + 按需读取 `SKILL.md` 生效。
- `.stock` 中的技能脚本依赖仓库根目录的 `.venv`；MCP server（`quant_tools_server.py`）为纯标准库实现。
- 分析引擎需要一个兼容 Anthropic Messages API 的端点；模型是否输出 `thinking` 取决于模型本身。
- 原聊天页 `/chat` 的 `/agent/*` 接口已随 `server/` 删除，页面仅作历史参考。
