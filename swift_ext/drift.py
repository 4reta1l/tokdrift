"""Measure tokenization drift under semantics-preserving rewrites.

    python swift_ext/drift.py --corpus swift_ext/data/corpus.jsonl

For each (rule, tokenizer) pair this reports:
  dTok%      mean change in total LLM token count
  files%     share of files where the token sequence changed at all
  frag%      share of grammar tokens whose subword decomposition changed

frag% is the one that matters. It is the direct measurement of the paper's
premise: the grammar sees one token, the model sees a different pile of
subwords depending on surrounding whitespace.

NOTE ON TOKENIZERS: do not use src/tokdrift/tokenizers/. Those carry a
'CodeLexer' pre-tokenizer from the authors' fork (the control experiment),
not the stock pipelines. Load from the Hub.
"""
import argparse
import json
import pathlib
import sys
from statistics import mean

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from tokdrift.swift_tokens import tokenize_swift, classify  # noqa: E402
from rules import apply_rule, find_matches  # noqa: E402

DEFAULT_TOKENIZERS = [
    "Qwen/Qwen2.5-Coder-7B-Instruct",
    "deepseek-ai/deepseek-coder-1.3b-instruct",
    "Qwen/CodeQwen1.5-7B-Chat",
]

# the ten rules verified preserving and above the frequency cut
DEFAULT_RULES = [
    ("(", "NAME"), ("]", ")"), ("[", "("), (")", "]"), ("]", ","),
    ("[", "NAME"), ("[", "1"), ("==", "("), ("[", '"'), (")", ")"),
]


def grammar_spans(src):
    """Character spans of the grammar tokens we care about (identifiers)."""
    tokens, _ = tokenize_swift(src)
    out = []
    for tok in tokens:
        _, _, wire = classify(tok, frozenset())
        if wire == "NAME":
            out.append((tok.start, tok.stop + 1, tok.text))
    return out


def fragments_for(span, offsets, pieces):
    """The subword pieces overlapping a character span."""
    lo, hi = span
    return tuple(p for (s, e), p in zip(offsets, pieces) if s < hi and e > lo)


def shift_map(src, rule):
    """Map original char offset -> offset in the rewritten string."""
    hits = find_matches(src, rule)
    def shift(pos):
        return pos + sum(1 for h in hits if h <= pos)
    return shift


def measure(tokenizer, corpus, rule):
    d_tok, changed_files = [], 0
    frag_changed = split_changed = frag_total = 0
    for rec in corpus:
        src = rec["source"]
        variant, n = apply_rule(src, rule)
        if n == 0:
            continue

        enc_a = tokenizer(src, add_special_tokens=False, return_offsets_mapping=True)
        enc_b = tokenizer(variant, add_special_tokens=False, return_offsets_mapping=True)
        ids_a, ids_b = enc_a["input_ids"], enc_b["input_ids"]
        off_a, off_b = enc_a["offset_mapping"], enc_b["offset_mapping"]
        pieces_a = tokenizer.convert_ids_to_tokens(ids_a)
        pieces_b = tokenizer.convert_ids_to_tokens(ids_b)

        d_tok.append((len(ids_b) - len(ids_a)) / len(ids_a) * 100)
        if ids_a != ids_b:
            changed_files += 1

        shift = shift_map(src, rule)
        for lo, hi, _text in grammar_spans(src):
            frag_total += 1
            fa = fragments_for((lo, hi), off_a, pieces_a)
            fb = fragments_for((shift(lo), shift(hi)), off_b, pieces_b)
            if fa != fb:
                frag_changed += 1
            # a leading-space marker alone is cosmetic: the identifier still
            # decomposes into the same number of pieces. a change in piece
            # COUNT is genuine re-fragmentation -- the .factorial phenomenon.
            if len(fa) != len(fb):
                split_changed += 1

    if not d_tok:
        return None
    return {
        "files": len(d_tok),
        "dTok_pct": mean(d_tok),
        "files_changed_pct": changed_files / len(d_tok) * 100,
        "frag_pct": frag_changed / frag_total * 100 if frag_total else 0.0,
        "split_pct": split_changed / frag_total * 100 if frag_total else 0.0,
        "frag_changed": frag_changed,
        "split_changed": split_changed,
        "frag_total": frag_total,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="swift_ext/data/corpus.jsonl")
    ap.add_argument("--tokenizers", nargs="*", default=DEFAULT_TOKENIZERS)
    ap.add_argument("--rules", default=None, help="JSON list of [left, right] pairs")
    ap.add_argument("--out", default="swift_ext/data/drift.json")
    args = ap.parse_args()

    from transformers import AutoTokenizer

    corpus = [json.loads(l) for l in pathlib.Path(args.corpus).read_text().splitlines() if l.strip()]
    rules = [tuple(r) for r in json.loads(args.rules)] if args.rules else DEFAULT_RULES
    print(f"corpus: {len(corpus)} files, {len(rules)} rules\n")

    results = {}
    for name in args.tokenizers:
        try:
            tok = AutoTokenizer.from_pretrained(name)
        except Exception as e:
            print(f"!! {name}: {type(e).__name__}: {str(e)[:80]}\n")
            continue
        if not tok.is_fast:
            print(f"!! {name}: slow tokenizer, no offset mapping. skipped\n")
            continue

        print(f"--- {name} ---")
        print(f"{'rule':<18}{'files':>7}{'dTok%':>9}{'files≠%':>9}"
              f"{'frag%':>8}{'split%':>8}   split changed")
        print("-" * 74)
        for rule in rules:
            r = measure(tok, corpus, rule)
            if r is None:
                print(f"{str(rule):<18}{'-':>7}   never fires")
                continue
            results[f"{name}|{rule}"] = r
            print(f"{str(rule):<18}{r['files']:>7}{r['dTok_pct']:>9.2f}"
                  f"{r['files_changed_pct']:>9.1f}{r['frag_pct']:>8.2f}"
                  f"{r['split_pct']:>8.2f}   {r['split_changed']}/{r['frag_total']}")
        print()

    pathlib.Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"wrote {args.out}")
    print("\nfrag%  = identifier's piece tuple changed at all "
          "(includes gaining a leading-space marker)")
    print("split% = identifier's piece COUNT changed -- genuine re-fragmentation")


if __name__ == "__main__":
    main()
