uvicorn start.Chat:app --reload --host localhost --port 8000
curl -X POST "http://127.0.0.1:8000/posthello" -H "Content-Type:application/json" -d '{"name":"zhangsan","age":20,"message":"nihao"}'
curl -Uri "http://127.0.0.1:8000/posthello" -UseBasicParsing   -Method POST     -Headers @{"Content-Type" = "application/json"}     -Body '{"name":"张三","age":20,"message":"你好"}'