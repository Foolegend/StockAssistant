# 📈 StockAssistant AI

StockAssistant 是一款集成多种金融分析技能（Skills）和 MCP (Model Context Protocol) 工具的智能股票分析助手。它采用前后端分离架构，支持自定义大模型调用，旨在提供专业、结构化的股票数据分析与投资价值评估。

## 🛠️ 项目架构

### 1. 后端 (stock-server)
基于 **FastAPI** 构建，核心逻辑由 `UnifiedAgent` 驱动。
- **Agent 核心**：支持动态加载 Skills 和 MCP 工具。
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
