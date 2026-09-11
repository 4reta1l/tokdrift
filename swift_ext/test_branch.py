"""Exercise the swift branch of DataExtractor without booting the framework.

Run from the repo root:
    python swift_ext/test_branch.py
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from tokdrift.data_generator import DataExtractor  # noqa: E402


class FakeConfig:
    lang = "swift"
    processing_mode = "combined_token_operators"


# __new__ skips __init__, which would demand a tokenizer and a HF dataset
# neither of which this method touches.
ex = DataExtractor.__new__(DataExtractor)
ex.config = FakeConfig()

SRC = 'let a = numbers.sorted()\nlet b = numbers . sorted()\n'
rows = ex._get_tokenize_tokens_details(SRC, set())

for d in rows:
    print(f"{d['pos']:<10} {d['type']:<12} "
          f"id={d['identifier']:<5} op={d['operator']:<5} {d['token_name']!r}")

# The invariant that matters: token positions must tile the source exactly.
# Every rewrite edits at these offsets and shifts what follows, so a gap or
# an overlap desynchronises silently and produces wrong output that still
# looks like valid Swift.
prev_end = 0
gaps = []
for d in rows:
    start, end = (int(x) for x in d['pos'].strip('()').split(','))
    if start != prev_end:
        gaps.append((prev_end, start))
    prev_end = end

print()
print("contiguous:", not gaps, "| covers whole source:", prev_end == len(SRC))
if gaps:
    print("GAPS:", gaps)
