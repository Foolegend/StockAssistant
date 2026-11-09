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

- `stock-server/config/agents.json`：定义 Agent、可用 Skill/MCP 和默认 Agent。
- `stock-server/config/commands.json`：定义聊天命令、说明和附加指令。
- `stock-server/config/mcp_servers.json`：注册可信的 MCP stdio server；默认不启动任何 MCP server。
- `stock-server/config/skills/<skill-id>/SKILL.md`：添加 Skill 后，后端自动发现其名称和说明。

前端加载 `GET /agent/catalog` 展示 Agent 和命令名称，再将选中的注册 ID 发送到 `POST /agent/chat`。用户不需要知道或手动输入 ID；服务端不接受客户端传入本地 Skill 路径或 MCP 启动命令。MCP 的 `env` 配置支持 `${ENV_VAR}` 引用进程环境变量，避免把密钥写入配置文件。

MCP server 示例（放入 `mcp_servers.json` 的 `servers` 数组）：

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

然后在 Agent 的 `mcp_server_ids` 中引用 `"local-weather"`。MCP 配置是可信的服务端代码执行配置，只应注册经过审查的 server。

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
.\start_backend.ps1
```
后端服务将运行在: `http://127.0.0.1:8015`

### 2. 启动前端
```powershell
cd stock-ui
# 安装依赖 (仅首次运行)
npm install

# 启动开发服务器
.\start_frontend.ps1
```
前端界面访问: `http://localhost:4200`

---

## ⚙️ 配置说明

后端配置文件位于 `stock-server/config/.env`：
- `OPENAI_API_KEY`: 你的 API Key（使用 Ollama 时可填任意字符）。
- `OPENAI_API_BASE`: API 基础路径（如 `http://localhost:11434/v1`）。
- `CHAT_MODEL`: 默认使用的模型名称（如 `gpt-oss:120b-cloud` 或 `qwen3:4b`）。

---

## 📁 目录结构
- `stock-server/`: 后端核心代码
  - `src/`: 源代码 (api, agent, brain, services)
  - `config/skills/`: 定义的各种 AI 技能
- `stock-ui/`: 前端界面代码
- `.venv/`: Python 虚拟环境
