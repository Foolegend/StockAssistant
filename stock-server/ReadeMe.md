Set-Location 'D:\project\StockAssistant\stock-server'
..\.venv\Scripts\python.exe -m uvicorn src.api.chat:app --reload --host 127.0.0.1 --port 8015 --no-access-log

# 服务目录
Invoke-RestMethod 'http://127.0.0.1:8015/agent/catalog'

# 日志

服务同时输出到控制台和 `logs/stock-server.log`（UTF-8，10 MB 轮转，保留 5 份）。

- 每行带 `req=<请求ID>`：客户端可通过 `X-Request-ID` 请求头指定，否则服务端自动生成，并在响应头中回传。
- 工具调用（LangGraph 在独立线程中执行工具，拿不到请求上下文）用 `thread_id` 关联到具体会话。
- 记录内容：请求耗时、组件路由结果、Agent 构建/invoke 耗时、MCP 服务器与工具加载、工具入参/出参（截断）、Hook 执行结果、异常堆栈。

### 追踪“这次问题由谁处理”

| 想知道 | 日志关键字 | 示例 |
| --- | --- | --- |
| 路由选了哪些组件 | `routing selection` / `routing resolved` | `routing selection agent=planner command=plan skills=['stock-analysis'] mcp_servers=['market-data']` |
| 最终生效的配置 | `resolved agent=` / `building agent` | `building agent model=... mcp_tools=['quote']` |
| 实际读了哪个 Skill | `skill load` | `skill load skill=stock-analysis thread_id=... via=read_file` |
| 调用了哪个 MCP 工具、属于哪个服务 | `tool start` / `tool finished` | `tool start tool=quote mcp_server=market-data thread_id=...` |
| 触发了哪个 Hook | `hook finished` / `hook failed` | `hook finished id=lint-on-edit event=PreToolUse` |
| 某次请求的完整链路 | `X-Request-ID` 或响应头 `X-Request-ID` | `req=ab12cd34ef56` |
| 某个会话的完整链路 | `thread_id=` | `thread_id=8f3c...` |

```powershell
# 追踪单次请求
Select-String -Path logs\stock-server.log -Pattern 'req=ab12cd34ef56'
# 追踪某个会话（含工具 / Skill / Hook）
Select-String -Path logs\stock-server.log -Pattern 'thread_id=8f3c'
```

可通过环境变量调整（见 `src/runtime/logging_config.py`）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `STOCK_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `STOCK_LOG_DIR` | `stock-server/logs` | 日志目录 |
| `STOCK_LOG_FILE` | `stock-server.log` | 日志文件名 |
| `STOCK_LOG_MAX_BYTES` | `10485760` | 单个日志文件大小上限 |
| `STOCK_LOG_BACKUPS` | `5` | 轮转保留文件数 |
| `STOCK_LOG_CONSOLE` | `1` | 设为 `0` 关闭控制台输出 |
