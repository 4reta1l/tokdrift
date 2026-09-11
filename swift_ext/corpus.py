"""Build a local Swift corpus from MultiPL-E humaneval-swift.

Each record is `prompt + tests`, which concatenates to a complete Swift file:
the prompt ends with an open function brace, the tests field begins with the
closing brace. The body is empty, so the result parses but does not typecheck
(no return on all paths). That is fine and deliberate -- Swift decides operator
classification at parse time, which is the level this study cares about.

Run from the repo root:
    python swift_ext/corpus.py
"""
import json
import pathlib
import subprocess
import sys
import tempfile

from datasets import load_dataset

OUT = pathlib.Path(__file__).resolve().parent / "data" / "corpus.jsonl"


def swiftc_parses(source: str) -> bool:
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "main.swift"
        path.write_text(source)
        proc = subprocess.run(["swiftc", "-parse", str(path)],
                              capture_output=True, text=True, timeout=120)
    return proc.returncode == 0


def main():
    if subprocess.run(["which", "swiftc"], capture_output=True).returncode != 0:
        sys.exit("swiftc not on PATH. Install Xcode command line tools.")

    ds = load_dataset("nuprl/MultiPL-E", "humaneval-swift", split="test")
    print(f"loaded {len(ds)} problems")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    kept, dropped = [], []
    for i, rec in enumerate(ds):
        source = rec["prompt"] + rec["tests"]
        if swiftc_parses(source):
            kept.append({"name": rec["name"], "source": source})
        else:
            dropped.append(rec["name"])
        if (i + 1) % 25 == 0:
            print(f"  checked {i + 1}/{len(ds)}  kept={len(kept)}  dropped={len(dropped)}")

    OUT.write_text("\n".join(json.dumps(r) for r in kept) + "\n")
    print(f"\nwrote {len(kept)} files to {OUT}")
    if dropped:
        print(f"dropped {len(dropped)} that do not parse at baseline:")
        for name in dropped[:10]:
            print(f"  {name}")
        if len(dropped) > 10:
            print(f"  ... and {len(dropped) - 10} more")
    print("\nRecord the drop count. It is a limitation you declare, not one you hide.")


if __name__ == "__main__":
    main()
