# Swift support for TokDrift

This branch extends TokDrift's spacing-rule analysis to Swift. The headline
result: the rule most responsible for tokenization drift in the paper —
whitespace after a member-access `.` — is not semantics-preserving in Swift
at all. `swiftc` rejects it on 359/359 attempted sites across all 158 files
tested, and the tree-sitter parser most tooling would reach for to verify
this detects none of the breakage.

## Scope

This covers the measurement half of the framework only: rewriting code,
verifying rewrites are legal, and measuring how much a tokenizer's output
changes as a result. It does not run any of the nine LLMs from the paper —
that half needs a GPU cluster this didn't have access to. Concretely:

- **Spacing rules only.** 13 of the paper's 18 spacing rules were ported and
  verified for Swift; the 6 naming rules (identifier case conversion) are
  not implemented. Swift's naming convention (lowerCamelCase, no
  snake_case tradition) needs its own rule table, which is future work.
- **3 of the paper's 4 tokenizers.** Qwen2.5-Coder-7B, deepseek-coder-1.3b,
  and CodeQwen1.5-7B. Llama-3.1-8B is gated on the Hub and wasn't fetched.
- **Tokenization drift, not accuracy drift.** The paper's *sensitivity*
  metric measures how often a model's output correctness flips between
  rewrite variants — that requires running inference. This branch measures
  how often an identifier's own subword decomposition changes, as a
  tokenizer-level proxy for the same phenomenon, reported below as `split%`.
- **Parse-level verification, not full compilation.** Rules are checked
  with `swiftc -parse`, which confirms a rewrite is legal Swift grammar.
  It does not typecheck or run the benchmark's tests, since the MultiPL-E
  prompts used here have empty function bodies.

## What this adds

| file | purpose |
|---|---|
| `src/tokdrift/swift_tokens.py` | tree-sitter-based tokenizer adapter presenting Swift tokens in the same `(type, text, start, stop)` shape ANTLR provides for the other languages, plus a `classify()` helper that separates a token's syntactic type from whether it's a renameable identifier |
| `src/tokdrift/data_generator.py` | adds the `lang == "swift"` branch to `_get_tokenize_tokens_and_immutable_identifiers` and `_get_tokenize_tokens_details` |
| `src/tokdrift/config.py` | the verified Swift spacing-rule table, with excluded rules documented in place alongside the swiftc diagnostic that ruled them out |
| `swift_ext/corpus.py` | builds a 158-file Swift corpus from MultiPL-E `humaneval-swift`, keeping only files that parse cleanly at baseline |
| `swift_ext/rules.py` | ranks candidate rules by frequency and classifies each against two oracles: tree-sitter parse-tree shape, and `swiftc -parse` |
| `swift_ext/drift.py` | measures tokenization drift per rule per tokenizer: token-count change, whether the token sequence changed at all, and whether individual identifiers re-fragmented |
| `swift_ext/diagnose.py` | prints one identifier's tokenization on both sides of a rewrite; the tool that found the bug described below |
| `swift_ext/test_swift_tokens.py`, `swift_ext/test_branch.py` | correctness tests for the token adapter and its integration into `data_generator.py` |

## Running it

```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r swift_ext/requirements-swift.txt
```

`requirements-swift.txt` is separate from the project's main dependencies
because those pin `vllm` and a CUDA build of torch for running the paper's
nine models. Neither is available on macOS or needed for anything below.

Sanity-check the environment before running anything heavier:

```bash
python swift_ext/test_swift_tokens.py   # tokenizer adapter, offline
python swift_ext/test_branch.py         # its integration into data_generator.py
```

Both should print `ALL GREEN` / `contiguous: True`. Neither needs network
access or `swiftc`, so this is the fastest way to confirm the environment
is set up correctly before the steps below.

```bash
python swift_ext/corpus.py       # ~2 min, needs swiftc on PATH
python swift_ext/rules.py verify # ~2 min, re-derives rule_verdicts.json
python swift_ext/drift.py        # downloads 3 tokenizers, re-derives drift.json
```

## Parser choice

The existing ANTLR grammars in `grammars-v4/swift` depend on a
`SwiftSupport.java` base class that exists only for the Java, C++, and C#
targets — porting it was a detour with no payoff. tree-sitter-swift ships
prebuilt wheels and, more importantly, parses incomplete input without
failing: every MultiPL-E prompt is a function signature with no body, and
an error-tolerant parser is required to tokenize those at all.

The cost: tree-sitter drops whitespace from its output entirely, so the
adapter in `swift_tokens.py` synthesizes `WHITESPACE` tokens by diffing
consecutive leaf-node byte spans. Without this, the rule matcher — which
depends on detecting whether two tokens are directly adjacent — cannot
distinguish `a.b` from `a . b`. Verified in `test_swift_tokens.py`.

## Rule verification

158 MultiPL-E `humaneval-swift` problems, `prompt + tests` concatenated
into a complete file (158/158 parse clean at baseline, none dropped). Each
candidate rule is applied at every match it finds, then the rewritten file
is checked against two oracles: tree-sitter parse-tree shape, and
`swiftc -parse`.

**Preserving (10, ranked by frequency; full verdicts in
`swift_ext/data/rule_verdicts.json`):**

| rule | sites | files |
|---|---|---|
| `('(', 'NAME')` | 3176 | 158/158 |
| `(']', ')')` | 818 | 158/158 |
| `('[', '(')` | 323 | 158/158 |
| `(')', ']')` | 323 | 158/158 |
| `(']', ',')` | 318 | 158/158 |
| `('[', 'NAME')` | 185 | 82/158 |
| `('[', '1')` | 174 | 43/158 |
| `('==', '(')` | 158 | 158/158 |
| `('[', '"')` | 121 | 19/158 |
| `(')', ')')` | 90 | 10/158 |

Two more are preserving but below the top-10 frequency cut used in the
paper's own methodology: `('[', ']')` (70 sites, 40 files) and `('[', '-')`
(48 sites, 16 files). A third, `(')', ',')` (37 sites, 3 files), is also
preserving — it's included here because an earlier 60-file sample reported
it as never firing at all; it occurs in only 3 of 158 files, and all
verdicts in this table are computed over the full corpus specifically
because rules this sparse are invisible to subsets.

**Not preserving, by two mechanisms absent from Java and Python:**

| rule | sites | files broken | `swiftc` says | tree-sitter detects |
|---|---|---|---|---|
| `('.', 'NAME')` | 359 | 158/158 | "extraneous whitespace after '.' is not permitted" | 0/158 |
| `('-', '1')` | 61 | 20/20 | "unary operator cannot be separated from its operand" | 20/20 |

`('.', 'NAME')` is S15 in the paper — the rule in its opening `.factorial`
example, and one of the reported rules for both Java and Python, where it
is preserving. Swift forbids whitespace between `.` and a member name
outright; there's no legal input where this rewrite could apply.

`('-', '1')` is a different mechanism: Swift decides whether an operator is
prefix, infix, or postfix from the whitespace around it, so adding a space
to the right of a prefix `-` detaches it from its operand. Compare with
`('[', '-')` above, which *is* preserving — whitespace on the operator's
left doesn't affect this classification.

The wildcard rules `('OP', 'NAME')` and `('OP', 'ALL')` (S17/S18 in the
paper) inherit the break from `('.', 'NAME')`, since in this corpus they
are almost entirely composed of it: of 3721 sites matched by
`('OP', 'NAME')`, 3720 are accounted for by `('(', 'NAME')` +
`('.', 'NAME')` + `('[', 'NAME')` alone. No operator-plus-identifier pair
outside these ever fires — every arithmetic operator (`+ - * / < > ! ? ->
..< =`) tested zero occurrences adjacent to an identifier anywhere in the
corpus, because MultiPL-E's Swift is machine-formatted and never writes
one that way. This benchmark cannot exercise that question; a hand-written
or scraped Swift corpus might answer it differently.

tree-sitter's parse-tree shape comparison — a free, no-compiler oracle —
catches the unary-operator break in every case but the dot break in none.
It reports `a.b` and `a . b` as structurally identical, when Swift's own
compiler does not. Anyone porting this framework to another language using
only a syntax-tree diff as their oracle should not assume it's sufficient.

## Tokenization drift

Measured on the 10 preserving rules above, across all three tokenizers.
Two metrics beyond the paper's own `dTok%` (token-count change) and
`files≠%` (whether the sequence changed at all):

- **`frag%`** — the percentage of identifiers whose subword piece list
  changed at all, including a rewrite that only adds a leading-space
  marker to an otherwise-unchanged token.
- **`split%`** — the percentage of identifiers whose piece *count*
  changed. This is the direct analogue of the paper's `.factorial`
  example, and the metric used as the proxy for sensitivity here.

Nine of the ten preserving rules show `split% = 0.00` on every tokenizer:
inserting a space at these positions changes token *count* but never an
identifier's internal decomposition. The one exception:

| rule | Qwen2.5-Coder-7B | deepseek-coder-1.3b | CodeQwen1.5-7B |
|---|---|---|---|
| `('(', 'NAME')` | split% 4.16 (401/9635) | split% 3.60 (347/9635) | split% 6.72 (647/9635) |
| all other 9 rules | split% 0.00 | split% 0.00 | split% 0.00 |

`frag%` on this same rule is far higher (32.96% for two of the three
tokenizers) — almost all of that gap is the leading-space-marker case
`frag%` was designed to catch and `split%` was designed to exclude; only
the `split%` figure represents genuine re-fragmentation.

Put together with the rule-verification result: the Swift rule with by far
the largest capacity for re-fragmenting identifiers is the one Swift's own
grammar makes illegal. What remains legal is almost entirely delimiter and
bracket spacing, and that class of edit barely touches identifier
tokenization at all. Full per-rule, per-tokenizer numbers are in
`swift_ext/data/drift.json`.

## Limitations

- **The benchmark is machine-translated, not idiomatic Swift.** MultiPL-E's
  `humaneval-swift` ports HumanEval's Python identifiers verbatim, so
  every function and variable name is snake_case against Swift's
  lowerCamelCase convention. A naming-rule extension will need a
  normalization pass first, the way the paper's own
  `normalize_humanevalpack_java.py` does for Java.
- **Operator-plus-identifier spacing is unexercised by this corpus.** As
  noted above, every non-dot operator rule fires zero times. Whether
  Swift's prefix/infix/postfix whitespace sensitivity affects real-world
  identifier tokenization — as opposed to the digit-literal case verified
  by `('-', '1')` — remains untested here.
- **Parse-level, not type-level, verification.** A rewrite that parses
  legally could still fail to typecheck in a context these empty-bodied
  prompts don't exercise.
- **One measurement bug was found and fixed during this work, not before
  it.** The first version of the drift measurement reported a spurious
  re-fragmentation for CodeQwen1.5 on `('[', 'NAME')`. Manual token
  inspection (`swift_ext/diagnose.py`) showed the two encodings it was
  comparing were byte-identical — the tokenizer discards that particular
  inserted space entirely — and the apparent difference was an artifact of
  computing offsets against two input strings of different lengths. Fixed
  by short-circuiting the comparison whenever the two token-id sequences
  are already equal. The fix and its rationale are in the commit history;
  the numbers in this README reflect the corrected run.
- **No model inference.** Every number above is about what a tokenizer
  produces, not about whether a model gets the problem right on either
  side of a rewrite. That's the part of the paper's method this doesn't
  attempt.

## Reproducing

```bash
python swift_ext/rules.py verify --rules '[["(","NAME"],["]",")"],["[","("],[")","]"],["]",","],["[","NAME"],["[","1"],["==","("],["[","\""],[")",")"],["[","]"],["[","-"],[".","NAME"],["-","1"],["OP","NAME"],["OP","ALL"],["(",")"],[")","."],[",","NAME"],[")",","]]'
python swift_ext/drift.py
```

Both write directly to `swift_ext/data/`, overwriting the committed
`rule_verdicts.json` and `drift.json` with a fresh run.
