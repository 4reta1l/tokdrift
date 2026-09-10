import tree_sitter_swift as tss
from tree_sitter import Language, Parser

SRC = b"""func closestPair(_ numbers: [Double]) -> Double {
    let sorted_lst = numbers.sorted()
    return sorted_lst[0] + sorted_lst[1]
}"""

parser = Parser(Language(tss.language()))
tree = parser.parse(SRC)
print("parse error:", tree.root_node.has_error)

def leaves(node):
    if node.child_count == 0:
        yield node
    for child in node.children:
        yield from leaves(child)

for tok in leaves(tree.root_node):
    text = SRC[tok.start_byte:tok.end_byte].decode()
    print(f"{tok.start_byte:>4}-{tok.end_byte:<4} {tok.type:<20} {text!r}")
