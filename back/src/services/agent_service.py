from src.agent.UnifiedAgent import AgentConfig, ask_agent


def run_agent(question: str, *, model: str | None = None, use_skills: bool = True, skill_dirs: list[str] | None = None, use_mcp: bool = False, mcp_servers: list[str] | None = None, thread_id: str = "default") -> str:
    config = AgentConfig(
        model_name=model,
        skill_dirs=skill_dirs if use_skills else [],
        mcp_servers=mcp_servers if use_mcp else [],
    )
    return ask_agent(question, config=config, thread_id=thread_id)
