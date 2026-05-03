from typing import Iterator
from ollama import ChatResponse, Client, chat


def call_multi_tools(model='qwen3-coder:480b-cloud', stream=True, available_functions={}, messages=[],think=True):
  client = Client()
  tools = list(available_functions.values())
  response: Iterator[ChatResponse] = client.chat(model, stream=stream, messages=messages, tools=tools, think=think)

  for chunk in response:
    if chunk.message.thinking:
      print(chunk.message.thinking, end='', flush=True)
    if chunk.message.content:
      print(chunk.message.content, end='', flush=True)
    if chunk.message.tool_calls:
      for tool in chunk.message.tool_calls:
        if function_to_call := available_functions.get(tool.function.name):
          print('\nCalling function:', tool.function.name, 'with arguments:', tool.function.arguments)
          output = function_to_call(**tool.function.arguments)
          print('> Function output:', output, '\n')

          # Add the assistant message and tool call result to the messages
          messages.append(chunk.message)
          messages.append({'role': 'tool', 'content': str(output), 'tool_name': tool.function.name})
        else:
          print('Function', tool.function.name, 'not found')

  print('----- Sending result back to model \n')
  if any(msg.get('role') == 'tool' for msg in messages):
    res = client.chat(model, stream=True, tools=tools, messages=messages, think=True)
    done_thinking = False
    for chunk in res:
      if chunk.message.thinking:
        print(chunk.message.thinking, end='', flush=True)
      if chunk.message.content:
        if not done_thinking:
          print('\n----- Final result:')
          done_thinking = True
        print(chunk.message.content, end='', flush=True)
      if chunk.message.tool_calls:
        # Model should be explaining the tool calls and the results in this output
        print('Model returned tool calls:')
        print(chunk.message.tool_calls)
  else:
    print('No tool calls returned')

def call_single_tool(model='qwen3-coder:480b-cloud', available_functions={}, messages=[]):
  tools = list(available_functions.values())
  response: ChatResponse = chat(
    model,
    messages=messages,
    tools=tools,
  )

  if response.message.tool_calls:
    # There may be multiple tool calls in the response
    for tool in response.message.tool_calls:
      # Ensure the function is available, and then call it
      if function_to_call := available_functions.get(tool.function.name):
        print('Calling function:', tool.function.name)
        print('Arguments:', tool.function.arguments)
        output = function_to_call(**tool.function.arguments)
        print('Function output:', output)
      else:
        print('Function', tool.function.name, 'not found')

  # Only needed to chat with the model using the tool call results
  if response.message.tool_calls:
    # Add the function response to messages for the model to use
    messages.append(response.message)
    messages.append({'role': 'tool', 'content': str(output), 'tool_name': tool.function.name})

    # Get final response from model with function outputs
    final_response = chat(model, messages=messages)
    print('Final response:', final_response.message.content)

  else:
    print('No tool calls returned from model')

