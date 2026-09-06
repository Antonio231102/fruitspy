import unittest
import unicodedata

from fruitspy.log_fields import TRUNCATION_MARKER, sanitize_log_field


class LogFieldTests(unittest.TestCase):
    def test_control_format_separator_and_backslash_characters_are_escaped(
        self,
    ) -> None:
        sanitized = sanitize_log_field(
            "safe\nforged\r\t\x1b\\path\u2028\u202e"
        )

        self.assertEqual(
            sanitized,
            r"safe\nforged\r\t\x1b\\path\u2028\u202e",
        )
        self.assertFalse(
            any(
                unicodedata.category(character).startswith("C")
                or (
                    unicodedata.category(character).startswith("Z")
                    and character != " "
                )
                for character in sanitized
            )
        )

    def test_long_fields_are_bounded_with_an_explicit_marker(self) -> None:
        sanitized = sanitize_log_field("x" * 100, max_length=32)

        self.assertEqual(len(sanitized), 32)
        self.assertTrue(sanitized.endswith(TRUNCATION_MARKER))

    def test_limit_must_fit_the_truncation_marker(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_length"):
            sanitize_log_field("value", len(TRUNCATION_MARKER) - 1)


if __name__ == "__main__":
    unittest.main()
