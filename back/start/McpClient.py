import asyncio
import os

from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession

# ======================
# 【关键】这里填你的 MCP 服务启动命令
# 你的服务应该是这样启动的：python your_server.py
# ======================
MCP_SERVER_COMMAND = StdioServerParameters(
    command="python",  # 执行的命令（比如 python、node 等）
    args=[os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcpServer", "mcpServerExample.py")]  # 命令的参数（你的服务脚本路径）
)

async def main():
    # 1. 启动子进程，建立 stdio 通道
    async with stdio_client(MCP_SERVER_COMMAND) as (read, write):
        # 2. 建立 MCP 会话
        async with ClientSession(read, write) as session:
            # 3. 初始化
            await session.initialize()
            print("✅ stdio 模式连接成功！")

            # 4. 列出所有工具
            tools = await session.list_tools()
            print("\n📋 可用工具：")
            for tool in tools.tools:
                # print(f"- {tool.name}：{tool.description}")
                print(tool)

            # ======================
            # 5. 调用工具（改成你自己的工具名 + 参数）
            # ======================
            result = await session.call_tool(
                name="add_two_numbers",  # 改成你实际的工具名
                arguments={"a":3, "b":4}              # 工具需要的参数
            )

            print("\n🔧 工具调用结果：")
            for content in result.content:
                print(content.text)

if __name__ == "__main__":
    asyncio.run(main())