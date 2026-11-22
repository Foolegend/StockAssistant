# 📈 StockAssistant AI

StockAssistant 是一款集成多种金融分析技能（Skills）和 MCP (Model Context Protocol) 工具的智能股票分析助手。它采用前后端分离架构，支持自定义大模型调用，旨在提供专业、结构化的股票数据分析与投资价值评估。

## 🛠️ 项目架构

### 1. 后端 (stock-server)
基于 **FastAPI** 构建，核心逻辑由 `UnifiedAgent` 驱动。
- **Agent 核心**：从服务端扩展目录发现 Agent、Skills、Commands 和 MCP Servers；聊天界面提供“能力”面板与 `/` 斜杠命令，API 使用注册 ID 定位扩展。
- **Brain 层**：提供多种 LLM 适配器（Ollama, OpenAI 等）。
- **Skill 系统**：
  - `stock-analysis`: 股票数据抓取与技术指标分析。
  - `docx-cn`: Word 文档处理与红线比对。
  - `weather`: 实时天气查询。
  - `doc-handler`: 文档解析处理。
- **MCP 集成**：通过 MCP 适配器扩展标准工具集。

### 2. 前端 (stock-ui)
基于 **Angular** 构建的对话优先研究工作台。
- **分析会话**：最近 30 条对话保存在当前浏览器本地。
- **过程可见**：展示后端实际报告的分析状态、模型思考（模型返回 `<think>` 时）与工具调用（命令/参数/结果），并标注本次使用的 Agent、Command、Skill 与 MCP。
- **能力可见**：顶栏“能力”面板按命令/技能/智能体/MCP/钩子分组展示，输入框键入 `/` 可搜索并插入命令或技能。
- **依据可追溯**：列出回答中明确提供的网页链接；未附链接时明确提示来源未经核实。
- **响应式设计**：在桌面端并列显示对话与依据面板，小屏幕下纵向排列。

---

## 🧩 扩展智能体

- 仅扫描 `stock-server/.stock` 中的扩展，不加载工程根目录下的 `.github`、`.cursor`，也不读取外部目录或使用 `CLAUDE_CONFIG_DIR`。
- `stock-server/.stock/agents/**/*.md`：自动发现 Agent，并注册为可选 Agent 和可委派子 Agent。
- `stock-server/.stock/skills/**/SKILL.md`：递归发现 Skill，按 DeepAgents Skills middleware 加载；支持 Skill 子目录。
- `stock-server/.stock/commands/**/*.md`：自动发现斜杠命令；子目录生成 `目录:名称` 命名空间（如 `reports/daily.md` → `/reports:daily`）。
- `stock-server/.stock/hooks/hooks.json`：读取原生事件、matcher 和 command 定义；`stock-server/.stock/mcp-configs/mcp-servers.json`：读取 `mcpServers` 配置并注册 MCP server。
- Hook 脚本和共享工具位于 `stock-server/.stock/hooks/scripts/`，由 Hook 配置调用。

`GET /agent/catalog` 返回目录 v2：`agents/skills/commands/mcp_servers/hooks`、`counts` 与 `diagnostics`，条目包含来源、相对路径、`when_to_use`、`allowed_tools`、命令的 `argument_hint`，以及 MCP 的 `transport/status/tool_count/error/updated_at`。聊天界面不要求用户选择或提交组件 ID；服务端扫描时只读取 YAML frontmatter 和文件路径，用名称、描述等摘要进行初选；只在依赖解析或执行时读取已选组件的正文。服务端还会递归检查已选 Agent 和 Skill 的正文，解析其中显式或语义上引用的注册组件：嵌套 Skill 加载到主 Agent，嵌套 Agent 提供为可委派子 Agent（由主 Agent 决定是否委派）。`POST /agent/chat` 保留一次性响应接口；前端使用 `POST /agent/chat/stream` 接收分析状态、实际工具调用事件和最终回答。请求包含问题、会话 ID 和本次运行 ID；`POST /agent/chat/runs/{run_id}/cancel` 可请求停止任务，停止在当前模型或工具步骤结束后生效。回答中的 Markdown 网页链接会作为来源返回，但不会由服务端独立核验。客户端不接受或提交本地 Skill 路径、MCP 启动命令或组件 ID。MCP 的 `env` 配置支持 `${ENV_VAR}` 引用进程环境变量，避免把密钥写入配置文件。

Agent 无需额外 JSON 配置；Agent Markdown frontmatter 可通过 `skills` 和 `mcp_server_ids` 声明关联能力。普通问题的 Agent、Skill 和 MCP 选择由服务端根据当前问题完成，不使用默认 Agent。

### 斜杠命令与技能调用

输入框键入 `/` 会列出可用的命令和技能（`user-invocable: false` 或 `enabled: false` 的条目不参与）。发送后由服务端确定性解析，不再交给 LLM 路由：

- `/命令名 参数`：读取 `.stock/commands/**/*.md`，按 frontmatter 选择 `agent` / `skills` / `mcp_server_ids`，正文替换 `$ARGUMENTS`、`$1`…`$9` 后作为本轮任务指令。
- `/技能名 参数`：直接固定该技能并注入其 `SKILL.md`；参数作为本轮问题。
- 未知命令返回 400，并在详情中列出可用命令与技能。

命令 Markdown 示例：

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

`when_to_use` 与 Skill 的 `metadata.trigger` 会显示在能力面板中，并进入 LLM 路由的精简候选描述。命令正文只在本轮执行时读取，历史中保存用户输入的 `/命令 参数` 原文。

Agent 和 Skill Markdown 是指令，不是 Claude Code 或 Cursor 运行时本身：Agent 委派映射为 DeepAgents `task` 子 Agent，Skill 通过技能目录加载。定义中提及的工具只有在本平台实际配置后才能使用；Claude Code 原生 `settings.json` hooks、Cursor 配置、memory 和权限配置不会导入。只读取仓库内上述扩展目录中的项目文件。

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

Agent frontmatter 中的模型选择目前不映射到其他模型；运行模型由 `stock-server/.env` 中的 `CHAT_MODEL` 决定。修改扩展文件后需重启后端以重新扫描。

若希望右侧“执行过程”显示可折叠的“思考”行，需要选择会输出 `<think>…</think>` 的模型（如 MiniMax-M2.5 / M2.7 / M3；MiniMax-M3.1-Flash 不输出思考内容）。后端会自动从模型内容中提取思考块，并按时间顺序与工具步骤交错展示。

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

也支持通过 `type: "http"` 和 `url` 配置 HTTP MCP server。普通问题由服务端根据问题从已注册 server 中选择；命令或技能也可以通过 `mcp_server_ids` 显式指定。MCP 配置可启动服务端进程或发起网络连接，只应注册经过审查的 server。

MCP 加载按 server 隔离：单个 server 失败不会中断整轮分析，其余 server 的工具继续可用。能力面板显示最近一次 Agent 构建的结果（`已连接` / `加载失败` / `尚未加载`）、工具数与错误摘要；状态只在构建 Agent 时更新，失败后需重启服务或调整配置后重新触发构建。

修改 JSON 扩展配置后重启后端。Agent 会话检查点保存在进程内存中，后端重启会清空；前端历史仅保存在当前浏览器，不会同步到其他设备。

---

## 🚀 快速启动

### 前置条件
- Python 3.12+
- Node.js (LTS) & npm
- Ollama (若使用本地模型)

### 一键启动前后端
在项目根目录执行脚本，脚本会先启动后端并等待其就绪，再启动前端：

```powershell
.\start_all.ps1
```

Linux / macOS:

```bash
bash ./start_all.sh
```

前端访问 `http://localhost:4200`，后端 API 文档访问 `http://127.0.0.1:8015/docs`。前端运行期间按 `Ctrl+C` 可同时停止前后端。

首次运行时，脚本会按需安装前端依赖；后端依赖需已安装在项目虚拟环境中。

### 分别启动（可选）
需要单独调试服务时，可分别启动：

#### 启动后端
```powershell
cd stock-server
# 激活虚拟环境 (如果尚未激活)
# & "..\.venv\Scripts\Activate.ps1"

# 启动后端
.\start_server.ps1
```
后端服务将运行在: `http://127.0.0.1:8015`

#### 启动前端
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

首次配置时，将模板复制为 `stock-server/.env`，再按需修改：

```powershell
Copy-Item stock-server\.env.template stock-server\.env
```

`.env` 不纳入 Git；提交时只提交 `.env.template`。配置项如下：
- `OPENAI_API_KEY`: 你的 API Key（使用 Ollama 时可填任意字符）。
- `OPENAI_API_BASE`: API 基础路径（如 `http://localhost:11434/v1`）。
- `CHAT_MODEL`: 默认使用的模型名称（如 `gpt-oss:120b-cloud` 或 `qwen3:4b`）。

---

## 📁 目录结构
- `stock-server/`: 后端核心代码
  - `src/`: 源代码 (api, agent, brain, services)
  - `.stock/`: Agent、Skill、Command、Hook 和 MCP 扩展定义
- `stock-ui/`: 前端界面代码
- `.venv/`: Python 虚拟环境
