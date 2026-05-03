from fastapi import FastAPI

# 1. 创建 FastAPI 实例
app = FastAPI()

# 2. 写一个接口（路径：/）
@app.get("/")
def home():
    return {"message": "你好，FastAPI 运行成功！"}

# 3. 写一个生成 Word 的接口（对应你刚才的 doc_handler）
@app.get("/hello")
def hello():
    return "hello world."

from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware  # 导入CORS中间件

# 配置CORS跨域访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200"],  # 允许所有来源，生产环境请改为你的前端地址
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有HTTP方法（包括OPTIONS预检）
    allow_headers=["*"],
)

# 定义接收的 JSON 结构
class Item(BaseModel):
    name: str
    age: int
    message: str

# POST 接口
@app.post("/posthello")
def hello_post(item: Item):
    return {
        "code": 0,
        "msg": "POST Request Success",
        "data": {
            "name": item.name,
            "age": item.age,
            "message": item.message
        }
    }