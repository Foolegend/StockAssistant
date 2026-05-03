from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_openai import ChatOpenAI
import os
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env"))

model = ChatOpenAI(
    model=os.environ["CHAT_MODEL"],
    base_url=os.environ["OPENAI_API_BASE"],
    streaming=True
)


async def run():
    client = MultiServerMCPClient(
        {
            "mcpServerExample": {
                "command": "python",
                "args": [os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcpServer", "mcpServerExample.py")],
                "transport": "stdio"
            }
            # ,
            # "fetch-web-content": {
            #     "url": "http://localhost:8001/sse/",
            #     "transport": "sse"
            # }
        })
    tools = await client.get_tools()
    print(tools)
    agent = create_react_agent(model=model, tools=tools)
    async for chunk in agent.astream({"messages": "What is temperature in New York?"}):
        if "agent" in chunk and "messages" in chunk["agent"]:
            messages = chunk["agent"]["messages"]
            if messages and hasattr(messages[-1], "content"):
                content_chunk = messages[-1].content
                if content_chunk:
                    print(content_chunk, end="", flush=True)
    print()  # 输出完成后换行

if __name__ == "__main__":
    import asyncio
    asyncio.run(run())