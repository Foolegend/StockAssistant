# 📈 StockAssistant AI

StockAssistant 是一款集成多种金融分析技能（Skills）和 MCP (Model Context Protocol) 工具的智能股票分析助手。它采用前后端分离架构，支持自定义大模型调用，旨在提供专业、结构化的股票数据分析与投资价值评估。

## 🛠️ 项目架构

### 1. 后端 (stock-server)
基于 **FastAPI** 构建，核心逻辑由 `UnifiedAgent` 驱动。
- **Agent 核心**：从服务端扩展目录发现 Agent、Command、Skills 和 MCP Servers；前端通过人类可读名称选择，API 使用注册 ID 定位扩展。
- **Brain 层**：提供多种 LLM 适配器（Ollama, OpenAI 等）。
- **Skill 系统**：
  - `stock-analysis`: 股票数据抓取与技术指标分析。
  - `docx-cn`: Word 文档处理与红线比对。
  - `weather`: 实时天气查询。
  - `doc-handler`: 文档解析处理。
- **MCP 集成**：通过 MCP 适配器扩展标准工具集。

### 2. 前端 (stock-ui)
基于 **Angular** 构建的现代化聊天界面。
- **实时交互**：通过 REST API 与后端 Agent 通信。
- **响应式设计**：适配不同设备，提供专业且简洁的 AI 聊天体验。

---

## 🧩 扩展智能体

- 自动扫描工程根目录下 `.github`、`.claude`、`.cursor` 中的扩展，不读取外部目录，也不使用 `CLAUDE_CONFIG_DIR`。
- `.github/agents/**/*.md`、`.claude/agents/**/*.md`、`.cursor/agents/**/*.md`：自动发现 Agent，并注册为可选 Agent 和可委派子 Agent。
- 三个目录下的 `commands/**/*.md`：自动发现 Command；默认名称为 `/命令路径`（可由 frontmatter 的 `name` 覆盖），正文作为工作流指令执行，`$ARGUMENTS` 会替换为用户输入。
- 三个目录下的 `skills/**/SKILL.md`：递归发现 Skill，按 DeepAgents Skills middleware 加载；支持 Skill 子目录。
- 三个目录下 `hooks/**/*.json` 的 `hooks` 数组和 `mcp/**/*.json` 的 `servers` 数组：自动发现项目 Hook 与 MCP server，格式与下方配置示例一致；不再读取 `stock-server/config` 中的 Hook/MCP 注册文件。

前端加载 `GET /agent/catalog` 展示 Agent、Command、Skill、MCP 和 Hook 的已注册信息，再将选中的 Agent/Command ID 发送到 `POST /agent/chat`。用户不需要知道或手动输入 ID；服务端不接受客户端传入本地 Skill 路径或 MCP 启动命令。MCP 的 `env` 配置支持 `${ENV_VAR}` 引用进程环境变量，避免把密钥写入配置文件。

Command 无需额外 JSON 配置；默认使用 ID 为 `chat` 的命令，若未发现则使用扫描到的第一个命令。若没有发现命令，普通对话仍可不选择 Command 进行。

Agent 无需额外 JSON 配置；通过 Agent Markdown frontmatter 中的 `skills` 和 `mcp_server_ids` 关联能力。可用 `default: true` 指定默认 Agent；未指定时优先使用 `stock-assistant`，否则选择扫描到的第一个 Agent。

Agent、Command 和 Skill Markdown 是指令，不是 Claude Code 或 Cursor 运行时本身：Agent 委派映射为 DeepAgents `task` 子 Agent，Skill 通过技能目录加载，Command 正文作为本轮任务指令。定义中提及的工具只有在本平台实际配置后才能使用；Claude Code 原生 `settings.json` hooks、Cursor 配置、memory 和权限配置不会导入。只读取工程根目录扩展分类目录中的项目文件。

Hook 无需服务端 JSON 配置文件；在 `.claude/hooks/`、`.cursor/hooks/` 或 `.github/hooks/` 下放置项目 JSON 文件，使用 `hooks` 数组登记。支持 `before_agent`、`after_agent` 和 `on_error`。每项必须包含唯一 `id`、`name`、`event` 和 argv 数组形式的 `command`，可用 `agent_ids` / `command_ids` 限定范围，以及 `timeout_seconds` 设置大于 0、最多 120 秒的运行时限。例如：

```json
{
  "hooks": [
    {
      "id": "audit-chat",
      "name": "审计对话",
      "event": "before_agent",
      "command": ["python", "scripts/audit_chat.py"],
      "agent_ids": ["stock-assistant"],
      "timeout_seconds": 10
    }
  ]
}
```

Hook 以无 shell 的子进程运行，工作目录为 `stock-server`，继承后端环境变量；JSON 事件上下文通过 stdin 传递（包含用户问题，成功后事件还包含回答）。成功时 stdout 不作为 Agent 指令使用；非零退出或超时会使本次请求失败。不会导入或执行 `.claude/settings.json` 中的 hook 命令。Hook 能读取继承的环境变量并以服务端身份运行，只应放入可信的项目脚本，并在改动配置后重启后端。

Agent frontmatter 中的模型选择目前不映射到其他模型；运行模型由 `stock-server/config/.env` 中的 `CHAT_MODEL` 决定。修改扩展文件后需重启后端以重新扫描。

MCP server 无需服务端 JSON 配置文件；在 `.claude/mcp/`、`.cursor/mcp/` 或 `.github/mcp/` 下放置 JSON 文件，使用 `servers` 数组登记。例如：

```json
{
  "id": "local-weather",
  "name": "本地天气工具",
  "description": "查询天气",
  "command": "node",
  "args": ["mcp/weather-server.js"],
  "cwd": ".",
  "env": {
    "WEATHER_API_KEY": "${WEATHER_API_KEY}"
  }
}
```

然后在 Agent frontmatter 的 `mcp_server_ids` 中引用 `"local-weather"`。MCP 配置是可信的服务端代码执行配置，只应注册经过审查的 server。

修改 JSON 扩展配置后重启后端。当前会话检查点保存在进程内存中，后端重启会清空；跨重启的数据库持久化可作为后续扩展。

---

## 🚀 快速启动

### 前置条件
- Python 3.12+
- Node.js (LTS) & npm
- Ollama (若使用本地模型)

### 1. 启动后端
```powershell
cd stock-server
# 激活虚拟环境 (如果尚未激活)
# & "..\.venv\Scripts\Activate.ps1"

# 执行一键启动脚本
.\start_server.ps1
```
后端服务将运行在: `http://127.0.0.1:8015`

### 2. 启动前端
```powershell
cd stock-ui
# 安装依赖 (仅首次运行)
npm install

# 启动开发服务器
.\start_ui.ps1
```
前端界面访问: `http://localhost:4200`

---

## ⚙️ 配置说明

首次配置时，将模板复制为 `stock-server/config/.env`，再按需修改：

```powershell
Copy-Item stock-server\config\.env.template stock-server\config\.env
```

`.env` 不纳入 Git；提交时只提交 `.env.template`。配置项如下：
- `OPENAI_API_KEY`: 你的 API Key（使用 Ollama 时可填任意字符）。
- `OPENAI_API_BASE`: API 基础路径（如 `http://localhost:11434/v1`）。
- `CHAT_MODEL`: 默认使用的模型名称（如 `gpt-oss:120b-cloud` 或 `qwen3:4b`）。

---

## 📁 目录结构
- `stock-server/`: 后端核心代码
  - `src/`: 源代码 (api, agent, brain, services)
  - `.github/skills/`: 定义的各种 AI 技能
- `stock-ui/`: 前端界面代码
- `.venv/`: Python 虚拟环境
