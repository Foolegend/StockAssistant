Set-Location 'D:\project\StockAssistant\stock-server'
..\.venv\Scripts\python.exe -m uvicorn src.api.chat:app --reload --host 127.0.0.1 --port 8015

# 服务目录
Invoke-RestMethod 'http://127.0.0.1:8015/agent/catalog'