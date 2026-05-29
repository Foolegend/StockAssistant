import json
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

from brain.McpBrainByOpenAI import call_mcp_by_openAI

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env"))
# 初始化OpenAI客户端
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=os.environ.get("OPENAI_API_BASE"))

# 设置OpenAI的调试日志
# logging.basicConfig(level=logging.DEBUG)


async def main():
    print("欢迎使用基于OpenAI的聊天机器人！输入'退出'结束对话。")

    # 保存对话历史
    messages = [
        {"role": "system", "content": "你是一个有帮助的助手，可以回答问题并调用工具获取信息。"}
    ]
    while True:
        user_input = input("\n用户: ")
        if user_input.lower() in ['退出', 'exit', 'quit']:
            print("再见！")
            break
        # 添加用户消息到历史
        messages.append({"role": "user", "content": user_input})
        # 调用OpenAI API（第一次调用不使用流式输出）
        result = await call_mcp_by_openAI(user_input)
        print(result)

        messages.extend(json.loads(result))

        # 再次调用API获取基于工具结果的回复（流式输出）
        print("\n[系统] 处理完成，生成最终回复...")
        sys.stdout.write("助手: ")

        second_stream = client.chat.completions.create(
            model=os.environ.get("CHAT_MODEL"),
            messages=messages,
            stream=True
        )

        second_full_response = ""
        for chunk in second_stream:
            if chunk.choices[0].delta.content:
                content_chunk = chunk.choices[0].delta.content
                second_full_response += content_chunk
                sys.stdout.write(content_chunk)
                sys.stdout.flush()

        sys.stdout.write('\n')

        # 将最终回复添加到消息历史
        messages.append({"role": "assistant", "content": second_full_response})


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())