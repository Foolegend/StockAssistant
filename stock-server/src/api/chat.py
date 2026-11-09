from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from src.agent.UnifiedAgent import AgentConfig, ask_agent, default_system_prompt
from src.services.extension_registry import ExtensionRegistry

app = FastAPI(title="StockAssistant Agent")
extension_registry = ExtensionRegistry()

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
    agent_id: str | None = Field(default=None, description="已注册 Agent 的 ID")
    command_id: str | None = Field(default=None, description="已注册 Command 的 ID")
    thread_id: str = Field(default_factory=lambda: str(uuid4()))


@app.get("/agent/catalog")
def agent_catalog():
    return {
        "code": 0,
        "message": "success",
        "data": extension_registry.catalog(),
    }


@app.post("/agent/chat")
def agent_chat(req: AgentChatRequest):
    try:
        agent, command = extension_registry.resolve(req.agent_id, req.command_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    config = AgentConfig(**extension_registry.agent_runtime_config(agent, command, default_system_prompt()))

    answer = ask_agent(req.question, config=config, thread_id=req.thread_id)
    return {
        "code": 0,
        "message": "success",
        "data": {
            "answer": answer,
            "agent_id": agent["id"],
            "command_id": command["id"],
            "model": config.model_name,
            "thread_id": req.thread_id,
        },
    }
