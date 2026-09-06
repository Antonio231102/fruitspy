from __future__ import annotations

import unicodedata

TRUNCATION_MARKER = "...[truncated]"


def sanitize_log_field(value: object, max_length: int = 256) -> str:
    if max_length < len(TRUNCATION_MARKER):
        raise ValueError(
            f"max_length must be at least {len(TRUNCATION_MARKER)}"
        )

    output: list[str] = []
    output_length = 0
    for character in str(value):
        escaped = _escape_character(character)
        if output_length + len(escaped) > max_length:
            while output and output_length + len(TRUNCATION_MARKER) > max_length:
                output_length -= len(output.pop())
            output.append(TRUNCATION_MARKER)
            break
        output.append(escaped)
        output_length += len(escaped)
    return "".join(output)


def _escape_character(character: str) -> str:
    if character == "\\":
        return "\\\\"
    if character == "\n":
        return "\\n"
    if character == "\r":
        return "\\r"
    if character == "\t":
        return "\\t"

    category = unicodedata.category(character)
    if category.startswith("C") or (category.startswith("Z") and character != " "):
        code_point = ord(character)
        if code_point <= 0xFF:
            return f"\\x{code_point:02x}"
        if code_point <= 0xFFFF:
            return f"\\u{code_point:04x}"
        return f"\\U{code_point:08x}"
    return character
