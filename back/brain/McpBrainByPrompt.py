import json
from ollama import chat
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import os
from textwrap import dedent

server_params = StdioServerParameters(
    command="python",  # 执行的命令（比如 python、node 等）
    args=[os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcpServer", "mcpServerExample.py")]# 命令的参数（你的服务脚本路径）
)
MODLE_NAME = 'gpt-oss:120b-cloud'


SYSTEM_PROMPT = """
# 角色
你是一个智能助手，可以根据问题选择最合适的工具进行处理。请严格按照以下要求响应：

## 可用工具列表
%s

## 响应要求
1. 仔细分析用户问题是否需要使用工具
2. 必须使用以下JSON格式响应：
{
    "reasoning": "思考过程分析", 
    "action": {
        "type": "tool_call" | "direct_answer",
        "tool_name": "工具名称（可选）",
        "parameters": {参数键值对（可选）}
    }
}

## 工具详细信息
按此格式描述每个工具：
%s

## 响应示例
示例1：
用户：北京现在气温多少度？
{
    "reasoning": "用户询问城市天气，需要调用天气查询工具",
    "action": {
        "type": "tool_call",
        "tool_name": "get_weather",
        "parameters": {"city": "北京"}
    }
}

示例2：
用户：帮我计算(3+5)*2的值
{
    "reasoning": "需要进行数学计算，调用计算器工具",
    "action": {
        "type": "tool_call",
        "tool_name": "calculator",
        "parameters": {"expression": "(3+5)*2"}
    }
}

示例3：
用户：你好吗？
{
    "reasoning": "问候不需要使用工具",
    "action": {
        "type": "direct_answer"
    }
}

## 当前用户问题
%s

请严格遵循上述格式，不要添加任何额外内容！
"""


def extract_json(text: str, trigger: str = "</think>") -> dict:
    json_content = None
    try:
        # Locate trigger and extract content
        if trigger in text:
            start_idx = text.index(trigger) + len(trigger)
            json_content = text[start_idx:].strip()
        else:
            json_content = text
        # Remove code block markers
        json_content = json_content.lstrip("```json").rstrip("```").strip()

        # Handle escaped newlines if needed
        json_content = json_content.replace(r"\n", "")

        return json.loads(json_content)

    except ValueError as e:
        raise ValueError(f"Trigger '{trigger}' not found in text") from e
    except json.JSONDecodeError as e:
        print(f"Invalid JSON content: {json_content}")
        raise
async def call_mcp_by_prompt(user_input: str) -> str:
    messages = [
        {"role": "system", "content": "你是一个有帮助的助手，你可以通过上下文内容来回答用户的问题"}
    ]
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 初始化
            print(await session.initialize())
            tools_ori = await session.list_tools()
            tools_list_str = "\n".join([f"- {tool.name}" for tool in tools_ori.tools])
            tools_list_detail = "\n".join([dedent(f"""
                <tool>
                名称：{tool.name}
                描述：{tool.description}
                参数格式：
                {tool.inputSchema}
                </tool>
            """) for tool in tools_ori.tools])

            prompt = SYSTEM_PROMPT % (tools_list_str, tools_list_detail, user_input)
            print(prompt)
            messages.append({"role": "system", "content": prompt})

            response = chat(MODLE_NAME, messages=messages)
            response_message = response['message']
            print(response_message)
            try:
                json_data = extract_json(response_message['content'])
                print("json_data:", json_data)
                if not json_data:
                    json_data = json.load(response_message['content'])
                # 使用正则表达式查找JSON部分
                # 检查是否需要调用工具
                if "direct_answer" != json_data.get("action").get("type"):
                    # 处理工具调用
                    print("\n[系统] 检测到工具调用，正在处理...")
                    function_name = json_data.get("action").get("tool_name")
                    function_args = json_data.get("action").get("parameters")

                    # 处理每个工具调用
                    print(f"[系统] 调用函数: {function_name}({function_args})")

                    function_response = await session.call_tool(function_name, function_args)
                    response_str_list = [resp.text for resp in function_response.content]
                    print(f"[系统] 函数调用结果: {response_str_list}")
                    return json.dumps({'role': 'tool', 'content': json.dumps(response_str_list, ensure_ascii=False),
                                       'tool_name': function_name}, ensure_ascii=False)
                else:
                    return json.dumps({'role': 'system', 'content': response_message}, ensure_ascii=False)
            except Exception as ex:
                return json.dumps({'role': 'system', 'content': f"无法处理的错误{ex}"}, ensure_ascii=False)