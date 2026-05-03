import os
import sys

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from openai import OpenAI

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config", ".env"))
# 初始化OpenAI客户端
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=os.environ.get("OPENAI_API_BASE"))

system_prompt = """
## 角色设定
你是一位专业、高效、多领域的超级智能助手，具备强大的知识整合与问题解决能力。你善于理解用户意图，提供准确、清晰、有温度的回答。

## 📋 核心任务
- 根据用户提问，结合你的专业知识库与可用工具（skills），提供高质量解答
- 回答需遵循：准确性 > 实用性 > 简洁性 > 友好性 的优先级原则
- 遇到模糊问题时，主动澄清需求；遇到复杂问题时，分步骤拆解说明

## 💬 交互风格
- 语言：默认使用用户提问的语言回复
- 格式：复杂内容使用结构化排版（列表/代码块/表格），提升可读性
- 态度：专业但不刻板，耐心倾听，积极反馈

## 💡 注意事项
1. read_file工具使用注意点: 不支持Windows绝对地址, 如: 错误写法 D:\\xxx\\xxx\\SKILL.md, 正确写法为 /xxx/xxx/SKILL.md
2. 执行python脚本时,必须使用 $PYTHON_EXEC(虚拟环境Python),禁止使用python/python3命令
3. 脚本路径必须使用 $SKILL_ROOT(技能根路径)拼接，确保路径准确
"""

llm = ChatOpenAI(
    model=os.getenv("CHAT_MODEL"),
    temperature=0.2,
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    openai_api_base=os.getenv("OPENAI_API_BASE")
)

checkpointer = MemorySaver()

# 项目根路径(当前脚本所在目录)
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
print(f"当前脚本根路径: {root_dir}")

# 技能根路径(指向skills目录)
skill_root = os.path.join(root_dir, "config", "skills")

print(f"skills路径:{skill_root}")

# 当前虚拟环境Python解释器路径
python_exec = sys.executable

print(f"python执行器:{python_exec}")

# 初始化LocalShellBackend
backend = LocalShellBackend(
    root_dir=root_dir,
    # 关键: 传递虚拟环境变量 + 自定义核心变量
    env={
        **os.environ.copy(),
        "PYTHONHASHSEED": "0",
        "SKILL_ROOT": skill_root,
        "PYTHON_EXEC": python_exec,
    },
    timeout=120,
    max_output_bytes=100000,
)

# 创建 deep agent

agent = create_deep_agent(
    model = llm,
    backend = backend,
    skills = [skill_root],
    checkpointer = checkpointer,
    system_prompt = system_prompt
)
if __name__ == "__main__":
    print("=====智能助手已启动，输入q 退出 =====")
    while True:
        question = input("======>请输入: ")
        if not question:
            continue
        if question.strip().lower() == "q":
            print("=====智能助手已退出=====")
            break

        for type, chunk in agent.stream(
            {"messages": [{"role": "user", "content": question}],},
            config={"configurable": {"thread_id": "12345"}},
            stream_mode=["updates"]
        ):
            if "SkillsMiddleware.before_agent" in chunk and chunk["SkillsMiddleware.before_agent"]:
                skills = chunk["SkillsMiddleware.before_agent"]["skills_metadata"]
                print(">" * 10, "加载Skills", "<" * 30)
                for skill in skills:
                    print("Load Skill: ", skill["name"])

            # 输出AI回答内容
            if "model" in chunk:
                message = chunk["model"]["messages"][0]
                if message.content:
                    print(">" * 10, "AIMessage", "<" * 30)
                    print(message.content)

                # 输出工具调用信息
                tool_calls = message.tool_calls
                if tool_calls:
                    print(">" * 10, "Call Tools", "<" * 30)
                    for t in tool_calls:
                        print(f"Tool: {t['name']} , Args: {t['args']}")

            # 输出工具执行结果
            if "tools" in chunk:
                print(">" * 20, "Tools Output", "<" * 20)
                for m in chunk["tools"]["messages"]:
                    print(f"Tool: {m.name}, Output: \n{m.content}")

