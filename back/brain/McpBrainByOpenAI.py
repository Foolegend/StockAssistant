import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env"))
# 初始化OpenAI客户端
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=os.environ.get("OPENAI_API_BASE"))

server_params = StdioServerParameters(
    command="python",  # 执行的命令（比如 python、node 等）
    args=[os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcpServer", "mcpServerExample.py")]# 命令的参数（你的服务脚本路径）
)
async def build_openai_tools(tools_ori):
    tools = []
    for tool in tools_ori:
        tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        })
    return tools
async def call_mcp_by_openAI(user_input:str)->str:
    # 保存对话历史
    messages = [
        {"role": "system", "content": "你是一个有帮助的助手，可以回答问题并调用工具获取信息。"}
    ]
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化
            print(await session.initialize())
            tools_ori = await session.list_tools()
            # print(tools_ori.tools)
            tools = await build_openai_tools(tools_ori.tools)
            # print(tools)
            # 添加用户消息到历史
            messages.append({"role": "user", "content": user_input})

            # 调用OpenAI API（第一次调用不使用流式输出）
            response = client.chat.completions.create(
                model=os.environ.get("CHAT_MODEL"),
                messages=messages,
                tools=tools,
                tool_choice="auto",
                # 不使用stream=True
            )
            # print(response)
            response_message = response.choices[0].message
            results = []
            if response_message.content:
                results.append({"role": "system", "content": response_message.content})
            try:
                # 检查是否需要调用工具
                if response_message.tool_calls:
                    # 处理工具调用
                    print("\n[系统] 检测到工具调用，正在处理...")
                    # 处理每个工具调用
                    for tool_call in response_message.tool_calls:
                        function_name = tool_call.function.name
                        function_args = json.loads(tool_call.function.arguments)

                        print(f"[系统] 调用函数: {function_name}({function_args})")

                        function_response = await session.call_tool(function_name, function_args)
                        response_str_list = [resp.text for resp in function_response.content]
                        print(f"[系统] 函数调用结果: {response_str_list}")
                        # 调用相应的函数
                        # 将函数调用结果添加到消息历史
                        results.append({
                            "role": "tool",
                            "tool_id": tool_call.id,
                            "tool_name": function_name,
                            "content": json.dumps(response_str_list, ensure_ascii=False)
                        })
                    else:
                        pass
            except:
                pass
            return json.dumps(results, ensure_ascii=False)