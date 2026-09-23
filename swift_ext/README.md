# Swift support for TokDrift

Extends the spacing-rule analysis to Swift. Main result: the rule most responsible for tokenization drift in the paper – whitespace after a member-access `.` – is not semantics-preserving in Swift. `swiftc` rejects it on 359/359 sites across all 158 files tested, and tree-sitter, the parser most tooling would reach for to check this, misses every one.

## Scope

Covers the measurement half only: rewriting code, verifying rewrites are legal, measuring the effect on tokenization. Does not run the papers nine models – that needs a GPU cluster, that I do not have.

- **Spacing rules only.** 13 of the paper's 18 ported and verified for Swift. The 6 naming rules are not implemented – Swift convention (camelCase, no snake_case) needs its own rule table first.
- **3 of 4 tokenizers.** Qwen2.5-Coder-7B, deepseek-coder-1.3b, CodeQwen1.5-7B. Llama-3.1-8B is gated on the Hub – was not fetched.
- **Tokenization drift, not accuracy drift.** The papers sensitivity metric needs model inference. This measures how often an identifier subword decomposition changes instead, reported below as `split%`.
- **Parse-level verification only.** Checked with `swiftc -parse`, not full compilation. The MultiPL-E prompts have empty bodies, so nothing would typecheck anyway.

## What this adds

| file | purpose |
|---|---|
| `src/tokdrift/swift_tokens.py` | tree-sitter tokenizer adapter, same `(type, text, start, stop)` shape ANTLR provides for the other languages |
| `src/tokdrift/data_generator.py` | adds the `lang == "swift"` branch |
| `src/tokdrift/config.py` | the verified Swift rule table, excluded rules documented in place |
| `swift_ext/corpus.py` | builds a 158-file Swift corpus from MultiPL-E `humaneval-swift` |
| `swift_ext/rules.py` | ranks rules by frequency, checks each against tree-sitter shape and `swiftc -parse` |
| `swift_ext/drift.py` | measures tokenization drift per rule per tokenizer |
| `swift_ext/diagnose.py` | prints one identifiers tokens on both sides of a rewrite – the tool that found the bug below |
| `swift_ext/test_swift_tokens.py`, `test_branch.py` | tests for the adapter and its integration |

## Running it

```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -r swift_ext/requirements-swift.txt
```

Separate requirements file: the main project pins `vllm` and CUDA torch, neither available on macOS or needed here.

```bash
python swift_ext/test_swift_tokens.py   # sanity check, offline
python swift_ext/test_branch.py         # ALL GREEN / contiguous: True

python swift_ext/corpus.py       # around 2 min, needs swiftc
python swift_ext/rules.py verify # around 2 min
python swift_ext/drift.py        # downloads 3 tokenizers
```

## Parser choice

The ANTLR grammar for Swift needs a `SwiftSupport.java` base class that only exists for Java, C++, and C#, so porting it was a dead end. tree-sitter-swift also parses incomplete input, which matters because every MultiPL-E prompt is a bare function signature with no body.

Cost: tree-sitter drops whitespace entirely, so the adapter synthesizes`WHITESPACE` tokens from the gaps between leaf nodes. Without it, the rule matcher can't tell `a.b` from `a . b`. Verified in`test_swift_tokens.py`.

## Rule verification

158 MultiPL-E `humaneval-swift` problems (`prompt + tests`), 158/158 parse clean at baseline, none dropped. Each rule applied at every match, checked against two oracles: tree-sitter shape, and `swiftc -parse`.

**Preserving (10, by frequency - full version in `rule_verdicts.json`):**
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
Also preserving, below the frequency cut: `('[', ']')` (70/40),`('[', '-')` (48/16), `(')', ',')` (37/3 – a 60-file sample had missed this one entirely; all verdicts here are full-corpus).

**Not preserving, two mechanisms neither Java nor Python has:**
| rule | sites | files broken | `swiftc` says | tree-sitter catches |
|---|---|---|---|---|
| `('.', 'NAME')` | 359 | 158/158 | "extraneous whitespace after '.' is not permitted" | 0/158 |
| `('-', '1')` | 61 | 20/20 | "unary operator cannot be separated from its operand" | 20/20 |
`('.', 'NAME')` is S15 in the paper – the `.factorial` example, preserving in both Java and Python. Swift forbids whitespace after `.` outright.

`('-', '1')` is different: Swift reads prefix/infix/postfix from surrounding whitespace, so a space to the right of prefix `-` detaches it from its operand. `('[', '-')` above is fine – the space is on the left.

`('OP', 'NAME')` and `('OP', 'ALL')` (S17/S18) inherit the break: of 3721 sites, 3720 are `('(','NAME')` + `('.','NAME')` + `('[','NAME')`. No arithmetic operator (`+ - * / < > ! ? -> ..< =`) ever appears adjacent to an identifier anywhere in the corpus – MultiPL-E's Swift is machine-formatted and never writes one that way. A hand-written corpus might answer this differently.

tree-sitter catches the unary break but misses the dot break completely – it sees `a.b` and `a . b` as the same tree. A syntax diff oracle alone is not enough for Swift.

## Tokenization drift

Measured on the 10 preserving rules, all three tokenizers. Two metrics beyond the paper's `dTok%` and `files≠%`:

- **`frag%`** – an identifiers subword pieces changed at all, including just gaining a leading-space marker.
- **`split%`** – the piece *count* changed. This is the `.factorial`phenomenon, and the proxy for sensitivity used here.

Nine of ten rules score `split% = 0.00` on every tokenizer. One exception:

| rule | Qwen2.5-Coder-7B | deepseek-coder-1.3b | CodeQwen1.5-7B |
|---|---|---|---|
| `('(', 'NAME')` | 4.16% (401/9635) | 3.60% (347/9635) | 6.72% (647/9635) |
| all other 9 | 0.00% | 0.00% | 0.00% |

`frag%` on this rule is much higher (32.96% on two tokenizers) – mostly the leading-space marker, not real re-fragmentation. Only `split%`counts that.

So: the rule with the most capacity to re-fragment identifiers is the one Swift makes illegal. What's left legal is bracket and delimiter spacing, which barely touches tokenization at all. Full numbers in `drift.json`.

## Limitations

- **Benchmark is not idiomatic Swift.** MultiPL-E keeps Python's snake_case identifiers. A naming-rule extension needs a normalization pass first, like `normalize_humanevalpack_java.py` does for Java.
- **Operator-plus-identifier spacing is untested.** Every non-dot operator rule fires zero times in this corpus. Whether Swift whitespace sensitivity matters for real identifiers is still open.
- **Parse-level only.** A rewrite that parses could still fail to typecheck in a context these empty bodies do not exercise.
- **One measurement bug was found and fixed.** The first drift run reported a spurious re-fragmentation for CodeQwen1.5 on`('[', 'NAME')`. Manual token inspection (`diagnose.py`) showed the two encodings were byte-identical – the tokenizer drops that space entirely, so the apparent change was from computing offsets against two strings of different length. Fixed by short-circuiting the comparison whenever the token ids already match. Numbers here are from the corrected run.
- **No model inference.** Everything above is about the tokenizer, not whether a model gets the problem right way.

## Reproducing

```bash
python swift_ext/rules.py verify --rules '[["(","NAME"],["]",")"],["[","("],[")","]"],["]",","],["[","NAME"],["[","1"],["==","("],["[","\""],[")",")"],["[","]"],["[","-"],[".","NAME"],["-","1"],["OP","NAME"],["OP","ALL"],["(",")"],[")","."],[",","NAME"],[")",","]]'
python swift_ext/drift.py
```

Both overwrite `rule_verdicts.json` and `drift.json` with a fresh run.
