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

- 仅扫描 `stock-server/.stock` 中的扩展，不加载工程根目录下的 `.github`、`.cursor`，也不读取外部目录或使用 `CLAUDE_CONFIG_DIR`。
- `stock-server/.stock/agents/**/*.md`：自动发现 Agent，并注册为可选 Agent 和可委派子 Agent。
- `stock-server/.stock/commands/**/*.md`：自动发现 Command；默认名称为 `/命令路径`（可由 frontmatter 的 `name` 覆盖），正文作为工作流指令执行，`$ARGUMENTS` 会替换为用户输入。
- `stock-server/.stock/skills/**/SKILL.md`：递归发现 Skill，按 DeepAgents Skills middleware 加载；支持 Skill 子目录。
- `stock-server/.stock/hooks/hooks.json`：读取原生事件、matcher 和 command 定义；`stock-server/.stock/mcp-configs/mcp-servers.json`：读取 `mcpServers` 配置并注册 MCP server。
- Hook 脚本和共享工具位于 `stock-server/.stock/hooks/scripts/`，由 Hook 配置和 `setup-pm` Command 调用。

`GET /agent/catalog` 提供已注册组件信息；聊天界面不要求用户选择或提交组件 ID。服务端扫描时只读取 Agent、Command、Skill 的 YAML frontmatter 和文件路径，用名称、描述等摘要进行初选；只在依赖解析或执行时读取已选组件的正文，不把未选组件的正文送入模型上下文。服务端还会递归检查已选 Agent、Command 和 Skill 的正文，解析其中显式或语义上引用的注册组件：嵌套 Command 合并为本轮指令，嵌套 Skill 加载到主 Agent，嵌套 Agent 提供为可委派子 Agent（由主 Agent 决定是否委派）。`POST /agent/chat` 只接收问题和会话 ID，并校验组件选择均来自注册表。客户端不接受或提交本地 Skill 路径、MCP 启动命令或组件 ID。MCP 的 `env` 配置支持 `${ENV_VAR}` 引用进程环境变量，避免把密钥写入配置文件。

Command 无需额外 JSON 配置；服务端根据问题选择适用命令，也可以不选择 Command 进行普通对话。

Agent 无需额外 JSON 配置；Agent Markdown frontmatter 可通过 `skills` 和 `mcp_server_ids` 声明关联能力。Agent、Command、Skill 和 MCP 的最终选择由服务端根据当前问题完成，不使用默认 Agent 或默认 Command。

Agent、Command 和 Skill Markdown 是指令，不是 Claude Code 或 Cursor 运行时本身：Agent 委派映射为 DeepAgents `task` 子 Agent，Skill 通过技能目录加载，Command 正文作为本轮任务指令。定义中提及的工具只有在本平台实际配置后才能使用；Claude Code 原生 `settings.json` hooks、Cursor 配置、memory 和权限配置不会导入。只读取仓库内上述扩展目录中的项目文件。

Hook 从扩展目录下的 `hooks/hooks.json` 加载，使用原生事件名、matcher 和 command 定义。例如：

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "tool == \"Bash\"",
        "hooks": [
          {
            "type": "command",
            "command": "python stock-server/.stock/hooks/scripts/audit_tool.py"
          }
        ]
      }
    ]
  }
}
```

`PreToolUse` 和 `PostToolUse` 会在工具调用前后执行；其他事件会被读取，但当前尚未连接到运行时触发点。命令在后端工作目录中以 shell 运行并继承后端环境变量，JSON 事件上下文通过 stdin 传递。`PreToolUse` 非零退出会阻止工具运行；命令非零退出或超时会使请求失败。Hook 以服务端身份执行，只应配置可信命令；`stock-server/.stock/settings.json` 不会被导入或执行。

Agent frontmatter 中的模型选择目前不映射到其他模型；运行模型由 `stock-server/config/.env` 中的 `CHAT_MODEL` 决定。修改扩展文件后需重启后端以重新扫描。

MCP server 在扩展目录下的 `mcp-configs/mcp-servers.json` 中登记，配置使用 `mcpServers` 对象。例如：

```json
{
  "mcpServers": {
    "local-weather": {
      "command": "node",
      "args": ["mcp/weather-server.js"],
      "cwd": ".",
      "env": {
        "WEATHER_API_KEY": "${WEATHER_API_KEY}"
      }
    }
  }
}
```

也支持通过 `type: "http"` 和 `url` 配置 HTTP MCP server。聊天时由服务端根据问题从已注册 server 中选择；MCP 配置可启动服务端进程或发起网络连接，只应注册经过审查的 server。

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
  - `.stock/`: Agent、Command、Skill、Hook 和 MCP 扩展定义
- `stock-ui/`: 前端界面代码
- `.venv/`: Python 虚拟环境
