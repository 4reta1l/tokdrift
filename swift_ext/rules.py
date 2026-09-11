"""Swift spacing-rule frequency analysis and semantic verification.

    python swift_ext/rules.py freq                 # rank candidate rules
    python swift_ext/rules.py verify               # classify every rule
    python swift_ext/rules.py verify --limit 20    # fast smoke run

Two oracles, deliberately:
  1. tree-sitter parse-tree shape  -- free, catches gross structural breakage
  2. swiftc -parse                 -- authoritative

They disagree, and the disagreement is the point. tree-sitter does not model
Swift's rule that whitespace decides whether an operator is prefix, infix or
postfix, so it reports `a+b` and `a+ b` as the same program. Only swiftc can
see the difference. The "ts-blind" column counts exactly those cases.
"""
import argparse
import json
import pathlib
import subprocess
import sys
import tempfile
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from tokdrift.swift_tokens import SWIFT_LANGUAGE, tokenize_swift, classify  # noqa: E402
from tree_sitter import Parser  # noqa: E402

CORPUS = pathlib.Path(__file__).resolve().parent / "data" / "corpus.jsonl"

DEFAULT_RULES = [
    ("(", ")"), ("(", "NAME"), (")", ")"), (")", "."),
    (".", "NAME"), ("[", "NAME"), ("]", ")"), (",", "NAME"),
    ("OP", "NAME"), ("OP", "ALL"),
]


# ----------------------------------------------------------------- rewriting
def find_matches(src, rule):
    """Character offsets where `rule` says one space should be inserted."""
    left, right = rule
    tokens, _ = tokenize_swift(src)
    hits = []
    for i, tok in enumerate(tokens):
        _, is_op, _ = classify(tok, frozenset())
        if not is_op or i + 1 >= len(tokens):
            continue
        nxt = tokens[i + 1]
        _, _, nxt_wire = classify(nxt, frozenset())
        if nxt_wire == "WHITESPACE":
            continue                       # already spaced; rule does not apply
        if left != "OP" and tok.text != left:
            continue
        if right == "ALL":
            ok = nxt_wire in ("NAME", "OP")
        elif right == "NAME":
            ok = nxt_wire == "NAME"
        else:
            ok = nxt.text == right
        if ok:
            hits.append(tok.stop + 1)
    return hits


def apply_rule(src, rule):
    """Insert one space at every match. Returns (new_src, n_edits).

    Insertions run right-to-left so that offsets computed against the original
    string stay valid as the string grows.
    """
    hits = find_matches(src, rule)
    out = src
    for pos in reversed(hits):
        out = out[:pos] + " " + out[pos:]
    return out, len(hits)


# ------------------------------------------------------------------- oracles
_parser = Parser(SWIFT_LANGUAGE)


def tree_shape(src):
    """Parse-tree structure with every position stripped out."""
    tree = _parser.parse(src.encode("utf-8"))

    def walk(node):
        return (node.type, tuple(walk(c) for c in node.children))

    return walk(tree.root_node), tree.root_node.has_error


def swiftc_parse(src):
    """Returns (ok, first_error_line)."""
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "main.swift"
        path.write_text(src)
        proc = subprocess.run(["swiftc", "-parse", str(path)],
                              capture_output=True, text=True, timeout=120)
    if proc.returncode == 0:
        return True, ""
    first = next((ln for ln in proc.stderr.splitlines() if ": error:" in ln), "")
    return True if not first else False, first.split(": error: ")[-1].strip()


# ------------------------------------------------------------------ commands
def load_corpus(limit=None):
    if not CORPUS.exists():
        sys.exit(f"No corpus at {CORPUS}. Run: python swift_ext/corpus.py")
    records = [json.loads(ln) for ln in CORPUS.read_text().splitlines() if ln.strip()]
    return records[:limit] if limit else records


def cmd_freq(args):
    corpus = load_corpus(args.limit)
    pairs, wildcards = Counter(), Counter()
    files_with = Counter()
    for rec in corpus:
        seen = set()
        tokens, _ = tokenize_swift(rec["source"])
        for i, tok in enumerate(tokens):
            _, is_op, _ = classify(tok, frozenset())
            if not is_op or i + 1 >= len(tokens):
                continue
            nxt = tokens[i + 1]
            _, _, nxt_wire = classify(nxt, frozenset())
            if nxt_wire == "WHITESPACE":
                continue
            follower = "NAME" if nxt_wire == "NAME" else nxt.text
            pairs[(tok.text, follower)] += 1
            wildcards[("OP", follower)] += 1
            seen.add((tok.text, follower))
        for key in seen:
            files_with[key] += 1

    print(f"corpus: {len(corpus)} files\n")
    print(f"{'rule':<24}{'occurrences':>12}{'files':>8}")
    print("-" * 44)
    for key, n in pairs.most_common(args.top):
        print(f"{str(key):<24}{n:>12}{files_with[key]:>8}")
    print("\nwildcards:")
    for key, n in wildcards.most_common(5):
        print(f"{str(key):<24}{n:>12}")


def cmd_verify(args):
    if subprocess.run(["which", "swiftc"], capture_output=True).returncode != 0:
        sys.exit("swiftc not on PATH. This harness needs the real compiler; "
                 "tree-sitter cannot see Swift's operator whitespace rules.")

    corpus = load_corpus(args.limit)
    rules = [tuple(r) for r in json.loads(args.rules)] if args.rules else DEFAULT_RULES

    print(f"corpus: {len(corpus)} files, {len(rules)} rules\n")
    print("checking baselines...", end="", flush=True)
    baseline = {}
    for rec in corpus:
        baseline[rec["name"]] = swiftc_parse(rec["source"])[0]
    usable = [r for r in corpus if baseline[r["name"]]]
    print(f" {len(usable)}/{len(corpus)} parse clean\n")

    header = f"{'rule':<18}{'edits':>7}{'files':>7}{'shape≠':>8}{'swiftc✗':>9}{'ts-blind':>10}  verdict"
    print(header)
    print("-" * len(header))

    results = {}
    for rule in rules:
        edits = n_files = shape_diff = broke = blind = 0
        example = ""
        for rec in usable:
            variant, n = apply_rule(rec["source"], rule)
            if n == 0:
                continue
            edits += n
            n_files += 1
            before, _ = tree_shape(rec["source"])
            after, after_err = tree_shape(variant)
            shape_changed = (before != after) or after_err
            ok, err = swiftc_parse(variant)
            if shape_changed:
                shape_diff += 1
            if not ok:
                broke += 1
                if not shape_changed:
                    blind += 1
                if not example:
                    example = f"{rec['name']}: {err[:66]}"

        if n_files == 0:
            verdict = "never fires"
        elif broke == 0:
            verdict = "preserving"
        elif broke == n_files:
            verdict = "ALWAYS BREAKS"
        else:
            verdict = f"BREAKS {broke}/{n_files}"
        results[str(rule)] = verdict
        print(f"{str(rule):<18}{edits:>7}{n_files:>7}{shape_diff:>8}{broke:>9}{blind:>10}  {verdict}")
        if example:
            print(f"{'':18}└─ {example}")

    out = pathlib.Path(__file__).resolve().parent / "data" / "rule_verdicts.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\nwrote {out}")
    print("ts-blind = swiftc rejected it but tree-sitter saw no change at all.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("freq")
    f.add_argument("--top", type=int, default=15)
    f.add_argument("--limit", type=int, default=None)
    f.set_defaults(func=cmd_freq)

    v = sub.add_parser("verify")
    v.add_argument("--rules", default=None, help='JSON list, e.g. \'[["+","NAME"]]\'')
    v.add_argument("--limit", type=int, default=None)
    v.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    args.func(args)
