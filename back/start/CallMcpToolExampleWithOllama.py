import json
import sys

from ollama import chat

from brain.McpBrainByOllama import call_mcp_by_ollama

MODLE_NAME = 'gpt-oss:120b-cloud'



async def main():
    print("欢迎使用基于Ollama的聊天机器人！输入'退出'结束对话。")

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
        result = await call_mcp_by_ollama(user_input)
        print(result)

        messages.extend(json.loads(result))

        # 再次调用API获取基于工具结果的回复（流式输出）
        print("\n[系统] 处理完成，生成最终回复...")
        sys.stdout.write("助手: ")

        second_stream = chat(MODLE_NAME, messages=messages)
        sys.stdout.write(second_stream.message.content)

        # 将最终回复添加到消息历史
        messages.append({"role": "assistant", "content": second_stream.message.content})


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())