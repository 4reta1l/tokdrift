"""Swift token adapter: tree-sitter -> ANTLR-shaped tokens for TokDrift."""
from dataclasses import dataclass

import tree_sitter_swift as tss
from tree_sitter import Language, Parser

SWIFT_LANGUAGE = Language(tss.language())

IDENTIFIER_NODE_TYPES = {"simple_identifier", "type_identifier"}
INERT_NODE_TYPES = {
    "comment", "multiline_comment",
    "line_str_text", "str_escaped_char", "raw_str_part",
}
OPERATOR_CHARS = set("+-*/%<>=!&|^~?.:,;()[]{}@#")


@dataclass
class SwiftToken:
    type: str
    text: str
    start: int   # inclusive char offset, ANTLR convention
    stop: int    # inclusive char offset, ANTLR convention


def _byte_to_char_map(src_bytes: bytes):
    text = src_bytes.decode("utf-8")
    mapping = {}
    b = 0
    for i, ch in enumerate(text):
        mapping[b] = i
        b += len(ch.encode("utf-8"))
    mapping[b] = len(text)
    return mapping


def _leaves(node):
    if node.child_count == 0:
        yield node
    for child in node.children:
        yield from _leaves(child)


def tokenize_swift(context: str):
    src_bytes = context.encode("utf-8")
    parser = Parser(SWIFT_LANGUAGE)
    tree = parser.parse(src_bytes)
    b2c = _byte_to_char_map(src_bytes)

    tokens = []
    cursor = 0
    for node in _leaves(tree.root_node):
        if node.start_byte == node.end_byte:
            continue
        if node.start_byte > cursor:
            gap = src_bytes[cursor:node.start_byte].decode("utf-8")
            tokens.append(SwiftToken("WHITESPACE", gap,
                                     b2c[cursor], b2c[node.start_byte] - 1))
        text = src_bytes[node.start_byte:node.end_byte].decode("utf-8")
        tokens.append(SwiftToken(node.type, text,
                                 b2c[node.start_byte], b2c[node.end_byte] - 1))
        cursor = node.end_byte
    if cursor < len(src_bytes):
        gap = src_bytes[cursor:].decode("utf-8")
        tokens.append(SwiftToken("WHITESPACE", gap, b2c[cursor], b2c[len(src_bytes)] - 1))
    return tokens, tree.root_node.has_error


def classify(token: SwiftToken, immutable_identifiers):
    """Returns (is_renameable_identifier, is_operator, wire_type)."""
    if token.type == "WHITESPACE" or token.type in INERT_NODE_TYPES:
        return False, False, token.type
    is_keyword = token.type == token.text and token.text.isalpha()
    name_shaped = token.type in IDENTIFIER_NODE_TYPES or is_keyword
    is_operator = (token.type == token.text
                   and bool(token.text)
                   and all(c in OPERATOR_CHARS for c in token.text))
    is_identifier = name_shaped and token.text not in immutable_identifiers
    wire_type = "NAME" if name_shaped else "OP" if is_operator else token.type
    return is_identifier, is_operator, wire_type
