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