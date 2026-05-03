import json
import sys
from ollama import chat
from brain.McpBrainByPrompt import call_mcp_by_prompt

MODLE_NAME = 'gpt-oss:120b-cloud'


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


async def main():
    print("欢迎使用基于Ollama的聊天机器人！输入'退出'或者‘exit'结束对话。")
    # 保存对话历史
    messages = [
        {"role": "system", "content": "你是一个有帮助的助手，你可以通过上下文内容来回答用户的问题"}
    ]
    while True:
        user_input = input("\n用户: ")
        if user_input.lower() in ['退出', 'exit', 'quit']:
            print("再见！")
            break
        messages.append({"role": "user", "content": user_input})
        result = await call_mcp_by_prompt(user_input)
        messages.append(json.loads(result))
        # 再次调用API获取基于工具结果的回复（流式输出）
        print("\n[系统] 处理完成，生成最终回复...")
        sys.stdout.write("助手: ")
        responses = chat(MODLE_NAME, messages=messages, stream=True)
        second_full_response = ""
        for response in responses:
            # print("reponse=", response)
            if response['message']['content']:
                content_chunk = response['message']['content']
                second_full_response += content_chunk
                sys.stdout.write(content_chunk)
                sys.stdout.flush()
        sys.stdout.write('\n')
        # 将最终回复添加到消息历史
        messages.append({"role": "assistant", "content": second_full_response})


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())