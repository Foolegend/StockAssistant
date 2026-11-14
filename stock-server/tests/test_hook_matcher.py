import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.services.hook_matcher import matches_hook, validate_hook_matcher


class MatchesHookTests(unittest.TestCase):
    def test_wildcard_matches_every_context(self):
        self.assertTrue(matches_hook("*", {"tool": "Bash"}))
        self.assertTrue(matches_hook("*", {}))

    def test_empty_matcher_falls_back_to_wildcard(self):
        self.assertTrue(matches_hook("", {"tool": "Bash"}))
        self.assertTrue(matches_hook(None, {"tool": "Bash"}))

    def test_exact_equality_on_tool_field(self):
        self.assertTrue(matches_hook('tool == "Bash"', {"tool": "Bash"}))
        self.assertFalse(matches_hook('tool == "Bash"', {"tool": "Write"}))

    def test_equality_is_strict_and_does_not_coerce_types(self):
        self.assertFalse(matches_hook('tool_input.count == "5"', {"tool_input": {"count": 5}}))
        self.assertTrue(matches_hook('tool_input.count == "5"', {"tool_input": {"count": "5"}}))
        self.assertFalse(matches_hook('tool_input.flag == "true"', {"tool_input": {"flag": True}}))

    def test_matches_operator_applies_regex_to_string_value(self):
        context = {"tool_input": {"command": "git push --force"}}

        self.assertTrue(matches_hook('tool_input.command matches "git push"', context))
        self.assertFalse(matches_hook('tool_input.command matches "npm run"', context))

    def test_matches_operator_coerces_non_string_value_to_text(self):
        context = {"tool_input": {"count": 123}}

        self.assertTrue(matches_hook('tool_input.count matches "12"', context))

    def test_missing_field_never_matches(self):
        self.assertFalse(matches_hook('tool_input.missing == "x"', {"tool_input": {}}))
        self.assertFalse(matches_hook('tool_input.command matches "git"', {"tool_input": {}}))

    def test_missing_field_is_matched_against_an_empty_string(self):
        # A missing field resolves to an empty string, so a permissive pattern still matches.
        self.assertTrue(matches_hook('tool_input.command matches ".*"', {"tool_input": {}}))

    def test_nested_lookup_returns_none_when_parent_is_not_a_mapping(self):
        self.assertFalse(matches_hook('tool_input.command == "git"', {"tool_input": "git"}))

    def test_logical_and_requires_every_condition(self):
        context = {"tool": "Bash", "tool_input": {"command": "git push"}}

        self.assertTrue(
            matches_hook('tool == "Bash" && tool_input.command matches "git"', context)
        )
        self.assertFalse(
            matches_hook('tool == "Bash" && tool_input.command matches "npm"', context)
        )

    def test_logical_or_accepts_either_condition(self):
        self.assertTrue(matches_hook('tool == "Bash" || tool == "Write"', {"tool": "Write"}))
        self.assertFalse(matches_hook('tool == "Bash" || tool == "Write"', {"tool": "Read"}))

    def test_and_binds_tighter_than_or(self):
        matcher = 'tool == "Bash" || tool == "Write" && tool_input.x == "1"'

        self.assertTrue(matches_hook(matcher, {"tool": "Write", "tool_input": {"x": "1"}}))
        self.assertFalse(matches_hook(matcher, {"tool": "Write", "tool_input": {"x": "2"}}))

    def test_negation_inverts_the_condition(self):
        self.assertTrue(matches_hook('!(tool == "Bash")', {"tool": "Write"}))
        self.assertFalse(matches_hook('!(tool == "Bash")', {"tool": "Bash"}))

    def test_negation_applies_to_a_grouped_expression(self):
        matcher = '!(tool == "Bash" || tool == "Write")'

        self.assertTrue(matches_hook(matcher, {"tool": "Read"}))
        self.assertFalse(matches_hook(matcher, {"tool": "Write"}))

    def test_parentheses_override_precedence(self):
        matcher = '(tool == "Bash" || tool == "Write") && tool_input.x == "1"'

        self.assertTrue(matches_hook(matcher, {"tool": "Write", "tool_input": {"x": "1"}}))
        self.assertFalse(matches_hook(matcher, {"tool": "Read", "tool_input": {"x": "1"}}))

    def test_wildcard_is_a_truthy_operand_in_a_boolean_expression(self):
        self.assertTrue(matches_hook('* && tool == "Bash"', {"tool": "Bash"}))
        self.assertFalse(matches_hook('* && tool == "Write"', {"tool": "Bash"}))

    def test_string_literal_supports_escaped_quotes(self):
        self.assertTrue(matches_hook('tool == "say \\"hi\\""', {"tool": 'say "hi"'}))
        self.assertFalse(matches_hook('tool == "say \\"hi\\""', {"tool": "say hi"}))

    def test_underscore_digits_are_allowed_in_nested_field_names(self):
        context = {"tool_input": {"a_b1": "x"}}

        self.assertTrue(matches_hook('tool_input.a_b1 == "x"', context))

    def test_unsupported_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported field in hook matcher: 'tool_name'"):
            matches_hook('tool_name == "Bash"', {"tool_name": "Bash"})

    def test_arbitrary_operator_token_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported operator in hook matcher: '\\*'"):
            matches_hook('tool * "x"', {"tool": "Bash"})

    def test_invalid_regular_expression_is_rejected(self):
        with self.assertRaises(re.error):
            matches_hook('tool matches "["', {"tool": "Bash"})

    def test_string_literal_is_never_treated_as_a_regular_expression(self):
        self.assertFalse(matches_hook('tool == "["', {"tool": "Bash"}))

    def test_unsupported_top_level_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported field in hook matcher: 'foo'"):
            matches_hook('foo == "Bash"', {"foo": "Bash"})

    def test_tool_input_without_sub_field_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported field in hook matcher: 'tool_input'"):
            matches_hook('tool_input == "Bash"', {"tool_input": "Bash"})

    def test_reserved_operator_word_cannot_be_used_as_a_field(self):
        with self.assertRaisesRegex(ValueError, "Unsupported field in hook matcher: 'matches'"):
            matches_hook('matches == "Bash"', {"matches": "Bash"})

    def test_not_equal_operator_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid hook matcher"):
            matches_hook('tool != "Bash"', {"tool": "Bash"})

    def test_unquoted_comparison_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must use quoted string values"):
            matches_hook("tool == Bash", {"tool": "Bash"})

    def test_unexpected_trailing_token_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unexpected token in hook matcher: extra"):
            matches_hook('tool == "Bash" extra', {"tool": "Bash"})

    def test_chained_comparison_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unexpected token in hook matcher: =="):
            matches_hook('tool == "Bash" == "Write"', {"tool": "Bash"})

    def test_invalid_character_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid hook matcher"):
            matches_hook('tool == "Bash" #comment', {"tool": "Bash"})

    def test_invalid_field_name_character_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid hook matcher"):
            matches_hook('tool_input.a-b == "x"', {"tool_input": {}})

    def test_incomplete_expression_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported field in hook matcher: None"):
            matches_hook('tool == "Bash" &&', {"tool": "Bash"})

    def test_blank_matcher_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Unsupported field in hook matcher: None"):
            matches_hook("   ", {"tool": "Bash"})

    def test_missing_closing_parenthesis_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Expected"):
            matches_hook('(tool == "Bash"', {"tool": "Bash"})

    def test_invalid_string_literal_is_rejected(self):
        with self.assertRaises(ValueError):
            matches_hook('tool == "\\q"', {"tool": "q"})


class ValidateHookMatcherTests(unittest.TestCase):
    def test_accepts_supported_expressions(self):
        for matcher in (
            "*",
            'tool == "Bash"',
            'tool_input.command matches "git push"',
            '(tool == "Bash" || tool == "Write") && tool_input.x == "1"',
            '!(tool == "Bash")',
        ):
            with self.subTest(matcher=matcher):
                validate_hook_matcher(matcher)

    def test_rejects_unsupported_operator(self):
        with self.assertRaisesRegex(ValueError, "Invalid hook matcher"):
            validate_hook_matcher('tool != "Bash"')

    def test_rejects_unsupported_field(self):
        with self.assertRaisesRegex(ValueError, "Unsupported field in hook matcher"):
            validate_hook_matcher('tool_name == "Bash"')

    def test_rejects_empty_matcher(self):
        with self.assertRaisesRegex(ValueError, "Unsupported field in hook matcher: None"):
            validate_hook_matcher("")

    def test_rejects_invalid_regular_expression(self):
        with self.assertRaises(re.error):
            validate_hook_matcher('tool matches "["')


if __name__ == "__main__":
    unittest.main()
