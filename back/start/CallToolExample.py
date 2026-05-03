import random
from tools.ToolExample import get_conditions,get_temperature
from brain.ToolBrainByOllama import call_multi_tools, call_single_tool

# available_functions = {
#   'get_temperature': get_temperature,
#   'get_conditions': get_conditions,
# }
#
#
#
# cities = ['London', 'Paris', 'New York', 'Tokyo', 'Sydney']
# city = random.choice(cities)
# city2 = random.choice(cities)
# messages = [{'role': 'user', 'content': f'What is the temperature in {city}? and what are the weather conditions in {city2}?'}]
# print('----- Prompt:', messages[0]['content'], '\n')
#
# call_multi_tools(messages=messages, available_functions=available_functions)


from tools.ToolExample import add_two_numbers,subtract_two_numbers

# Tools can still be manually defined and passed into chat
subtract_two_numbers_tool = {
  'type': 'function',
  'function': {
    'name': 'subtract_two_numbers',
    'description': 'Subtract two numbers',
    'parameters': {
      'type': 'object',
      'required': ['a', 'b'],
      'properties': {
        'a': {'type': 'integer', 'description': 'The first number'},
        'b': {'type': 'integer', 'description': 'The second number'},
      },
    },
  },
}

messages = [{'role': 'user', 'content': 'What is three plus one?'}]
print('Prompt:', messages[0]['content'])

available_functions = {
  'add_two_numbers': add_two_numbers,
  'subtract_two_numbers': subtract_two_numbers,
}
call_single_tool(available_functions=available_functions, messages=messages)

