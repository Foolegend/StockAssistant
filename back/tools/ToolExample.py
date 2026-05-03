def add_two_numbers(a: int, b: int) -> int:
  """
  Add two numbers

  Args:
    a (int): The first number
    b (int): The second number

  Returns:
    int: The sum of the two numbers
  """

  # The cast is necessary as returned tool call arguments don't always conform exactly to schema
  # E.g. this would prevent "what is 30 + 12" to produce '3012' instead of 42
  return int(a) + int(b)


def subtract_two_numbers(a: int, b: int) -> int:
  """
  Subtract two numbers
  """

  # The cast is necessary as returned tool call arguments don't always conform exactly to schema
  return int(a) - int(b)



def get_temperature(city: str) -> str:
  """
  Get the temperature for a city in Celsius

  Args:
    city (str): The name of the city

  Returns:
    str: The current temperature in Celsius
  """
  # This is a mock implementation - would need to use a real weather API
  import random

  if city not in ['London', 'Paris', 'New York', 'Tokyo', 'Sydney']:
    return 'Unknown city'

  return str(random.randint(0, 35)) + ' degrees Celsius'


def get_conditions(city: str) -> str:
  """
  Get the weather conditions for a city
  """
  import random
  if city not in ['London', 'Paris', 'New York', 'Tokyo', 'Sydney']:
    return 'Unknown city'
  # This is a mock implementation - would need to use a real weather API
  conditions = ['sunny', 'cloudy', 'rainy', 'snowy']
  return random.choice(conditions)
