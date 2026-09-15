"""Inspect one identifier's tokenization before and after a rewrite.

    python swift_ext/diagnose.py --tokenizer Qwen/CodeQwen1.5-7B-Chat --rule '["[","NAME"]'

Prints the first few identifiers the rule touches, with their subword pieces
on each side. Use it whenever a number in the drift table looks wrong: read
the actual tokens before theorising about them.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from drift import grammar_spans, fragments_for, shift_map  # noqa: E402
from rules import apply_rule  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--tokenizer", required=True)
ap.add_argument("--rule", required=True, help='JSON pair, e.g. \'["[","NAME"]\'')
ap.add_argument("--corpus", default="swift_ext/data/corpus.jsonl")
ap.add_argument("--show", type=int, default=8)
args = ap.parse_args()

from transformers import AutoTokenizer
tok = AutoTokenizer.from_pretrained(args.tokenizer)
rule = tuple(json.loads(args.rule))
corpus = [json.loads(l) for l in pathlib.Path(args.corpus).read_text().splitlines() if l.strip()]

shown = 0
for rec in corpus:
    src = rec["source"]
    variant, n = apply_rule(src, rule)
    if n == 0:
        continue

    ea = tok(src, add_special_tokens=False, return_offsets_mapping=True)
    eb = tok(variant, add_special_tokens=False, return_offsets_mapping=True)
    pa = tok.convert_ids_to_tokens(ea["input_ids"])
    pb = tok.convert_ids_to_tokens(eb["input_ids"])

    print(f"\n=== {rec['name']} ===")
    print(f"ids identical: {ea['input_ids'] == eb['input_ids']}   "
          f"len before={len(ea['input_ids'])} after={len(eb['input_ids'])}")

    shift = shift_map(src, rule)
    for lo, hi, text in grammar_spans(src):
        fa = fragments_for((lo, hi), ea["offset_mapping"], pa)
        fb = fragments_for((shift(lo), shift(hi)), eb["offset_mapping"], pb)
        if fa != fb:
            print(f"  {text!r:<22} {list(fa)}  ->  {list(fb)}")
            shown += 1
            if shown >= args.show:
                sys.exit(0)
