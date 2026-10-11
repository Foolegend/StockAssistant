from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.agent.UnifiedAgent import AgentConfig, ask_agent

app = FastAPI(title="StockAssistant Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"message": "StockAssistant Agent 已启动"}


@app.get("/hello")
def hello():
    return "hello world."


class AgentChatRequest(BaseModel):
    question: str = Field(..., description="用户提问")
    model: str | None = Field(default=None, description="可选模型，例如 gpt-oss:120b-cloud")
    use_skills: bool = Field(default=True, description="是否加载 skills")
    skill_dirs: list[str] | None = Field(default=None, description="自定义 skill 路径列表")
    use_mcp: bool = Field(default=False, description="是否加载 MCP tools")
    mcp_servers: list[str] | None = Field(default=None, description="MCP server 脚本路径列表")
    thread_id: str = Field(default="default")


@app.post("/agent/chat")
def agent_chat(req: AgentChatRequest):
    skill_dirs = req.skill_dirs if req.use_skills else []
    mcp_servers = req.mcp_servers if req.use_mcp else []

    config = AgentConfig(
        model_name=req.model,
        skill_dirs=skill_dirs,
        mcp_servers=mcp_servers,
    )

    answer = ask_agent(req.question, config=config, thread_id=req.thread_id)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "answer": answer,
            "model": req.model,
            "use_skills": req.use_skills,
            "use_mcp": req.use_mcp,
            "thread_id": req.thread_id,
        },
    }
