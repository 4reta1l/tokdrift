"""Day 2 acceptance tests for the Swift token adapter.

Run from the repo root:
    python swift_ext/test_swift_tokens.py
"""
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "tokdrift"))
from swift_tokens import tokenize_swift, classify  # noqa: E402


def check(label, condition):
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    return condition


ok = True

# ---------------------------------------------------------------- test 1
# Lossless round trip. If this fails, every rewrite you build later is
# silently corrupting source.
SRC = 'let a = numbers.sorted()\nlet b = numbers . sorted()\nlet c = "text.with.dots"\n// comment.here\n'
tokens, has_error = tokenize_swift(SRC)
ok &= check("round-trip is byte-exact", "".join(t.text for t in tokens) == SRC)
ok &= check("offsets address the right text",
            all(SRC[t.start:t.stop + 1] == t.text for t in tokens))
ok &= check("complete file parses clean", not has_error)

# ---------------------------------------------------------------- test 2
# Adjacency. Rule ('.', 'NAME') must fire on `numbers.sorted` and must NOT
# fire on `numbers . sorted`, which already has the space.
IMM = {"sorted"}
matches = []
for i, t in enumerate(tokens):
    if t.type == ".":
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        wire = classify(nxt, IMM)[2] if nxt else None
        matches.append(wire == "NAME")
ok &= check("('.','NAME') fires exactly once (adjacent only)", matches == [True, False])

# ---------------------------------------------------------------- test 3
# Inert regions: nothing inside a string literal or a comment may ever be
# classified as NAME or OP, or rewrites will corrupt text and break tests.
inert = [t for t in tokens if t.type in ("line_str_text", "comment")]
ok &= check("string bodies and comments found", len(inert) == 2)
ok &= check("inert regions classify as neither NAME nor OP",
            all(classify(t, IMM)[2] not in ("NAME", "OP") for t in inert))

# ---------------------------------------------------------------- test 4
# Renameability is separate from wire type. `sorted` is immutable, so it
# still types as NAME but must not be renameable.
by_text = {}
for t in tokens:
    ident, _, wire = classify(t, IMM)
    if wire == "NAME":
        by_text[t.text] = ident
ok &= check("immutable identifier keeps wire type NAME", "sorted" in by_text)
ok &= check("immutable identifier is not renameable", by_text.get("sorted") is False)
ok &= check("ordinary identifier is renameable", by_text.get("numbers") is True)

# ---------------------------------------------------------------- test 5
# Real MultiPL-E prompts are incomplete: a signature and an open brace with
# no body. The adapter must still return a usable token stream.
PROMPT = (
    "/// Check if in given array of numbers, are any two numbers closer to each other than\n"
    "/// given threshold.\n"
    "/// >>> has_close_elements(numbers: [1.0, 2.0, 3.0], threshold: 0.5)\n"
    "/// false\n"
    "func has_close_elements(numbers: [Double], threshold: Double) -> Bool {\n"
)
ptokens, perr = tokenize_swift(PROMPT)
ok &= check("incomplete prompt is flagged as an error tree", perr is True)
ok &= check("incomplete prompt still round-trips",
            "".join(t.text for t in ptokens) == PROMPT)
ok &= check("function name is recoverable from the error tree",
            any(t.text == "has_close_elements" and t.type == "simple_identifier"
                for t in ptokens))

print()
print("ALL GREEN" if ok else "SOMETHING FAILED — do not proceed to Day 3")
sys.exit(0 if ok else 1)
