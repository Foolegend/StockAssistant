import json
import re
from typing import Any


_TOKEN = re.compile(r'\s*(\&\&|\|\||==|matches\b|!|\(|\)|\*|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_.]*)')


class _MatcherParser:
    def __init__(self, expression: str, context: dict[str, Any]) -> None:
        self.context = context
        self.tokens = []
        position = 0
        while position < len(expression):
            if not expression[position:].strip():
                break
            match = _TOKEN.match(expression, position)
            if match is None:
                raise ValueError(f"Invalid hook matcher near: {expression[position:]!r}")
            self.tokens.append(match.group(1))
            position = match.end()
        self.index = 0

    def evaluate(self) -> bool:
        result = self._parse_or()
        if self.index != len(self.tokens):
            raise ValueError(f"Unexpected token in hook matcher: {self.tokens[self.index]}")
        return result

    def _parse_or(self) -> bool:
        result = self._parse_and()
        while self._accept("||"):
            right = self._parse_and()
            result = result or right
        return result

    def _parse_and(self) -> bool:
        result = self._parse_unary()
        while self._accept("&&"):
            right = self._parse_unary()
            result = result and right
        return result

    def _parse_unary(self) -> bool:
        if self._accept("!"):
            return not self._parse_unary()
        if self._accept("("):
            result = self._parse_or()
            self._expect(")")
            return result
        if self._accept("*"):
            return True

        variable = self._next()
        if not variable or not re.fullmatch(r"tool(?:_input\.[A-Za-z_][A-Za-z0-9_]*)?", variable):
            raise ValueError(f"Unsupported field in hook matcher: {variable!r}")
        operator = self._next()
        if operator not in ("==", "matches"):
            raise ValueError(f"Unsupported operator in hook matcher: {operator!r}")
        literal_token = self._next()
        if not literal_token or not literal_token.startswith('"'):
            raise ValueError("Hook matcher comparisons must use quoted string values")

        expected = json.loads(literal_token)
        actual = self._lookup(variable)
        if operator == "==":
            return actual == expected
        return re.search(expected, "" if actual is None else str(actual)) is not None

    def _lookup(self, variable: str) -> Any:
        value: Any = self.context
        for part in variable.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    def _accept(self, token: str) -> bool:
        if self.index < len(self.tokens) and self.tokens[self.index] == token:
            self.index += 1
            return True
        return False

    def _expect(self, token: str) -> None:
        if not self._accept(token):
            raise ValueError(f"Expected {token!r} in hook matcher")

    def _next(self) -> str | None:
        if self.index >= len(self.tokens):
            return None
        token = self.tokens[self.index]
        self.index += 1
        return token


def matches_hook(matcher: str, context: dict[str, Any]) -> bool:
    return _MatcherParser(matcher or "*", context).evaluate()


def validate_hook_matcher(matcher: str) -> None:
    _MatcherParser(matcher, {}).evaluate()
